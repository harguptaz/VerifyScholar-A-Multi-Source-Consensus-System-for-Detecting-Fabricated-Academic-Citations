"""The verification engine: DOI-first lookup, fallback search, journal check, retraction check, scoring."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Dict, List, Optional

from ..config import Settings, get_settings
from ..schemas import (
    CandidateInfo, CitationResult, FieldMatch, HeuristicFlag, Links, ParsedCitation, PaperRecord, RetractionInfo, SourceResult,
)
from ..utils.text import similarity
from . import heuristics, scoring
from .adapters.base import AdapterResponse, SourceAdapter
from .adapters.crossref import CrossRefAdapter, retraction_from_notices
from .adapters.doaj import DoajAdapter
from .adapters.openalex import OpenAlexAdapter
from .http_client import RequestContext
from .matching import Comparison, accept_candidate, compare, rank_candidates
from .merge import merge_all, merge_retraction

ProgressCallback = Callable[[int, int, CitationResult], Awaitable[None]]


def _outcome_unavailable(resp: AdapterResponse) -> str:
    code = f"HTTP {resp.http_status}" if resp.http_status else (resp.error or "error")
    return f"Source unavailable ({code})"


class VerificationEngine:
    def __init__(self, crossref: CrossRefAdapter, openalex: OpenAlexAdapter, doaj: DoajAdapter, settings: Optional[Settings] = None) -> None:
        self.crossref, self.openalex, self.doaj = crossref, openalex, doaj
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ record sources (CrossRef / OpenAlex)
    async def _record_source(self, adapter: SourceAdapter, parsed: ParsedCitation, ctx: RequestContext, lookup_only: bool) -> SourceResult:
        label = adapter.label
        chosen: Optional[tuple[PaperRecord, Comparison, str]] = None
        doi_record: Optional[PaperRecord] = None
        doi_mismatch = False
        unavailable_msg: Optional[str] = None

        if parsed.doi:
            resp = await adapter.lookup_doi(parsed.doi)
            if resp.status == "unavailable":
                ctx.log(label, resp.query, _outcome_unavailable(resp))
                unavailable_msg = resp.error or "source unavailable"
            elif resp.status == "ok" and resp.records:
                rec = resp.records[0]
                cmp = compare(parsed, rec, matched_by="doi")
                title_bad = cmp.fields["title"].status == "mismatch"
                authors_ok = cmp.fields["authors"].status == "match"
                if not lookup_only and title_bad and not authors_ok:
                    doi_mismatch, doi_record = True, rec
                    ctx.log(label, resp.query, "Partial match (the DOI resolves to a different paper)")
                else:
                    ctx.log(label, resp.query, "Match found")
                chosen = (rec, cmp, "doi")
            else:
                ctx.log(label, resp.query, "No record")

        infos: List[CandidateInfo] = []
        needs_search = unavailable_msg is None and (chosen is None or doi_mismatch) and not (lookup_only and parsed.doi)
        if needs_search:
            # Pass 1 searches everything; if nothing acceptable turns up and we know the year, pass 2 searches only
            # the cited year +/- 1. Short titles ("Layer normalization") have thousands of hits, so the real paper
            # may not be in the top results of an unrestricted search.
            for window in (False, True):
                if window and not parsed.year:
                    break
                resp = await adapter.search(parsed, year_window=window)
                if resp.status == "unavailable":
                    ctx.log(label, resp.query, _outcome_unavailable(resp))
                    unavailable_msg = resp.error or "source unavailable"
                    break
                if not (resp.status == "ok" and resp.records):
                    ctx.log(label, resp.query, "No record")
                    continue
                accepted: Optional[tuple[PaperRecord, Comparison]] = None
                for _score, rec, cmp in rank_candidates(parsed, resp.records)[:10]:
                    ok, why = accept_candidate(parsed, cmp)
                    info = CandidateInfo(title=rec.title, doi=rec.doi, year=rec.year, journal=rec.journal, title_similarity=round(cmp.title_sim, 2), note=why)
                    if ok and accepted is None and (chosen is None or cmp.title_sim > chosen[1].title_sim):
                        accepted, info.accepted = (rec, cmp), True
                    if not any(i.doi and i.doi == info.doi or (not i.doi and i.title == info.title) for i in infos):
                        infos.append(info)
                if accepted:
                    rec, cmp = accepted
                    if doi_mismatch:
                        cmp.fields["doi"] = FieldMatch(submitted=parsed.doi, found=rec.doi, similarity=0.0, status="mismatch")
                        cmp.mismatched = [k for k, v in cmp.fields.items() if v.status == "mismatch"]
                    chosen = (rec, cmp, "search")
                    good = cmp.title_sim >= 0.85 and not cmp.mismatched
                    ctx.log(label, resp.query, ("Match found" if good else "Partial match") + (" (second search, cited year ±1)" if window else ""))
                    break
                ctx.log(label, resp.query, f"No record ({len(resp.records)} candidates, none was the cited paper)")

        infos = infos[:6]
        if chosen is None:
            if unavailable_msg:
                return SourceResult(source=adapter.name, label=label, status="unavailable", note=unavailable_msg, candidates=infos)
            return SourceResult(source=adapter.name, label=label, status="no_record", score=0, note="No matching record", candidates=infos)

        rec, cmp, method = chosen
        still_mismatch = doi_mismatch and method == "doi"
        score = scoring.source_score(cmp.fields, lookup_only=lookup_only, doi_mismatch=still_mismatch)
        status = "match" if score >= self.settings.verified_threshold else "partial"
        return SourceResult(
            source=adapter.name, label=label, status=status, method=method, score=score, fields=cmp.fields, record=rec,  # type: ignore[arg-type]
            doi_resolves_to=doi_record if doi_mismatch else None, candidates=infos,
            note="The cited DOI resolves to a different paper" if doi_mismatch else None,
        )

    # ------------------------------------------------------------------ DOAJ
    async def _journal_source(self, anchor: Optional[PaperRecord], parsed: ParsedCitation, ctx: RequestContext) -> SourceResult:
        label = self.doaj.label
        if parsed.kind == "conference":  # a conference is not a journal, so DOAJ (and CrossRef's journal list) have nothing to say
            return SourceResult(source="doaj", label=label, kind="journal", status="skipped", note="Conference or proceedings venue, not a journal")
        issns = list(anchor.issn) if anchor and anchor.issn else ([parsed.issn] if parsed.issn else [])
        name = (anchor.journal if anchor and anchor.journal else None) or parsed.journal
        if not issns and not name:
            return SourceResult(source="doaj", label=label, kind="journal", status="skipped", note="No journal given")

        check = None
        if issns:  # DOAJ's issn: query matches the print or the electronic ISSN, so one lookup is enough
            check = await self.doaj.check_by_issn(issns[0])
            ctx.log(label, check.query, "Match found" if check.status == "match" else ("No record" if check.status == "no_record" else _outcome_unavailable(AdapterResponse("unavailable", http_status=check.http_status, error=check.error))))
        if check is None and name:
            check = await self.doaj.check_by_title(name)
            ctx.log(label, check.query, "Match found" if check.status == "match" else ("No record" if check.status == "no_record" else _outcome_unavailable(AdapterResponse("unavailable", http_status=check.http_status, error=check.error))))
        assert check is not None
        if check.status == "unavailable":
            return SourceResult(source="doaj", label=label, kind="journal", status="unavailable", note=check.error)
        if check.status == "match":
            return SourceResult(
                source="doaj", label=label, kind="journal", status="match", method=check.method,  # type: ignore[arg-type]
                score=100, note=f"Journal “{check.title}” is listed in DOAJ" + (f" ({check.url})" if check.url else ""),
            )
        return SourceResult(source="doaj", label=label, kind="journal", status="no_record", score=0, note="Not listed in DOAJ (DOAJ lists open-access journals only)")

    # ------------------------------------------------------------------ retraction
    async def _retraction(self, record: PaperRecord, ctx: RequestContext) -> RetractionInfo:
        infos = [record.retraction]
        if record.doi:
            resp = await self.crossref.retraction_notices(record.doi)
            if resp.status == "ok" and resp.records:
                notices = resp.records[0].retraction
                ctx.log(self.crossref.label, resp.query, f"{len(notices.notices)} notice(s) found" if notices.notices else "No retraction notices")
                infos.append(notices if notices.notices else RetractionInfo(status="none_found"))
            else:
                ctx.log(self.crossref.label, resp.query, "No record" if resp.status == "not_found" else _outcome_unavailable(resp))
        return merge_retraction(infos)

    # ------------------------------------------------------------------ main entry
    async def verify_citation(self, parsed: ParsedCitation, lookup_only: Optional[bool] = None) -> CitationResult:
        ctx = RequestContext(parsed.index)
        if lookup_only is None:
            lookup_only = bool(parsed.doi and not parsed.title and not parsed.authors and not parsed.journal and not parsed.year)

        flags: List[HeuristicFlag] = heuristics.local_flags(parsed)
        cr, oa = await asyncio.gather(
            self._record_source(self.crossref, parsed, ctx, lookup_only),
            self._record_source(self.openalex, parsed, ctx, lookup_only),
        )
        sources: List[SourceResult] = [cr, oa]

        matched = [s for s in (cr, oa) if s.record is not None and s.status in ("match", "partial")]
        anchor_src = max(matched, key=lambda s: s.score or 0) if matched else None
        merged: Optional[PaperRecord] = None
        if anchor_src and anchor_src.record:
            group = [anchor_src.record] + [s.record for s in matched if s is not anchor_src and s.record]
            merged = merge_all(group)

        doaj_res = await self._journal_source(merged, parsed, ctx)
        sources.append(doaj_res)
        if merged and doaj_res.status in ("match", "no_record"):
            merged.in_doaj = doaj_res.status == "match"
            if "doaj" not in merged.sources and doaj_res.status == "match":
                merged.sources.append("doaj")

        retraction = merged.retraction if merged else RetractionInfo()
        if merged and merged.doi:
            retraction = await self._retraction(merged, ctx)
            merged.retraction = retraction

        outcome = scoring.score_result(sources, flags, self.settings, parsed.kind, merged.type if merged else None)
        if outcome.verdict in ("suspicious", "ghost"):
            doi_not_found = bool(parsed.doi) and not any(s.method == "doi" and s.record is not None for s in (cr, oa))
            journal_confirmed = parsed.kind == "conference" or bool(anchor_src and anchor_src.fields.get("journal") and anchor_src.fields["journal"].status == "match")
            try:
                flags += await heuristics.network_flags(parsed, self.crossref, self.openalex, ctx, doi_not_found, skip_journal=journal_confirmed)
            except Exception:  # pragma: no cover - heuristics must never break verification
                pass
            outcome = scoring.score_result(sources, flags, self.settings, parsed.kind, merged.type if merged else None)

        return self._assemble(parsed, sources, merged, flags, anchor_src, outcome, ctx, retraction)

    def _assemble(
        self, parsed: ParsedCitation, sources: List[SourceResult], merged: Optional[PaperRecord], flags: List[HeuristicFlag],
        anchor_src: Optional[SourceResult], outcome: scoring.Outcome, ctx: RequestContext, retraction: RetractionInfo,
    ) -> CitationResult:
        record_sources = [s for s in sources if s.kind == "record"]
        doi_status = self._doi_status(parsed, record_sources)
        journal_status = self._journal_status(parsed, anchor_src, sources)
        links = Links()
        if merged:
            links = Links(doi_url=merged.doi_url, landing_page=merged.url, open_access=merged.oa_url)
        result = CitationResult(
            index=parsed.index, raw=parsed.raw, parsed=parsed, verdict=outcome.verdict, confidence=outcome.confidence,  # type: ignore[arg-type]
            sources=sources, record=merged, flags=flags, links=links, doi_status=doi_status, journal_status=journal_status,
            retraction=retraction, calls=ctx.calls,
        )
        result.reason = scoring.build_reason(parsed, outcome.verdict, sources, flags, merged, doi_status, anchor_src)
        if retraction.status == "retracted":
            result.reason += " Note: this paper has been retracted; do not cite it without acknowledging the retraction."
        elif retraction.status == "concern":
            result.reason += " Note: the publisher has issued an expression of concern for this paper."
        return result

    @staticmethod
    def _doi_status(parsed: ParsedCitation, record_sources: List[SourceResult]) -> str:
        if not parsed.doi:
            return "no_doi"
        if all(s.status == "unavailable" for s in record_sources):
            return "unavailable"
        if any(s.doi_resolves_to is not None for s in record_sources):
            return "mismatch"
        if any(s.method == "doi" and s.record is not None for s in record_sources):
            return "found"
        # cited DOI not found directly; a search match may still carry the DOI it should have
        return "not_found"

    @staticmethod
    def _journal_status(parsed: ParsedCitation, anchor: Optional[SourceResult], sources: List[SourceResult]) -> str:
        doaj = next((s for s in sources if s.kind == "journal"), None)
        if anchor and anchor.fields.get("journal") and anchor.fields["journal"].status in ("match", "partial"):
            return "verified_in_index"
        if doaj and doaj.status == "match":
            return "found_in_doaj"
        if not parsed.journal and not (anchor and anchor.record and anchor.record.journal):
            return "not_provided"
        if doaj and doaj.status == "unavailable" and not anchor:
            return "unavailable"
        return "not_found"

    # ------------------------------------------------------------------ many citations
    def rescore(self, r: CitationResult) -> None:
        outcome = scoring.score_result(r.sources, r.flags, self.settings, r.parsed.kind, r.record.type if r.record else None)
        r.verdict, r.confidence = outcome.verdict, outcome.confidence  # type: ignore[assignment]
        anchor = max((s for s in r.sources if s.kind == "record" and s.record and s.status in ("match", "partial")), key=lambda s: s.score or 0, default=None)
        r.reason = scoring.build_reason(r.parsed, r.verdict, r.sources, r.flags, r.record, r.doi_status, anchor)
        if r.retraction.status == "retracted":
            r.reason += " Note: this paper has been retracted; do not cite it without acknowledging the retraction."
        elif r.retraction.status == "concern":
            r.reason += " Note: the publisher has issued an expression of concern for this paper."

    async def _batch(self, items: List[ParsedCitation], concurrency: int, progress: Optional[ProgressCallback] = None) -> List[CitationResult]:
        sem = asyncio.Semaphore(concurrency)
        results: List[Optional[CitationResult]] = [None] * len(items)
        done = 0

        async def worker(pos: int, p: ParsedCitation) -> None:
            nonlocal done
            async with sem:
                try:
                    r = await self.verify_citation(p)
                except Exception as exc:  # one bad citation must not sink the whole job
                    r = CitationResult(index=p.index, raw=p.raw, parsed=p, verdict="inconclusive", reason=f"Verification failed unexpectedly: {exc}")
            results[pos] = r
            done += 1
            if progress:
                await progress(done, len(items), r)

        await asyncio.gather(*(worker(i, p) for i, p in enumerate(items)))
        return [r for r in results if r is not None]

    @staticmethod
    def _source_down(r: CitationResult) -> bool:
        return r.verdict == "inconclusive" and any(s.kind == "record" and s.status == "unavailable" for s in r.sources)

    async def verify_many(
        self, items: List[ParsedCitation], progress: Optional[ProgressCallback] = None, concurrency: Optional[int] = None,
        on_retry: Optional[Callable[[int], None]] = None,
    ) -> List[CitationResult]:
        final = await self._batch(items, concurrency or self.settings.max_parallel_citations, progress)

        # A source that throttled or timed out should not leave a citation unchecked: retry those once, gently.
        retry = [r for r in final if self._source_down(r)]
        if retry and self.settings.retry_unavailable:
            if on_retry:
                on_retry(len(retry))
            await asyncio.sleep(self.settings.retry_pause_seconds)
            fresh = {r.index: r for r in await self._batch([r.parsed for r in retry], 2)}
            for pos, old in enumerate(final):
                new = fresh.get(old.index)
                if new is not None:
                    new.calls = old.calls + new.calls  # the transparency log keeps the failed first attempt too
                    final[pos] = new

        for idx in heuristics.apply_cluster_flags(final):
            self.rescore(next(r for r in final if r.index == idx))
        return final
