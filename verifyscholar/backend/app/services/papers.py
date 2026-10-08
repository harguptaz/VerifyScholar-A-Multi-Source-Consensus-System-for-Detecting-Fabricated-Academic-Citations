"""Paper Finder (F31), search (F32) and citation network (F33)."""
from __future__ import annotations

import asyncio
import re
from typing import List, Optional, Tuple

from ..config import Settings, get_settings
from ..schemas import (
    LookupResponse, NetworkResponse, PaperAccess, PaperCard, ParsedCitation, PaperRecord, RetractionInfo, SearchResponse, SourceResult,
)
from ..utils.text import OPENALEX_ID_RE, arxiv_to_doi, extract_doi, normalize, similarity
from .adapters.crossref import CrossRefAdapter
from .adapters.openalex import OpenAlexAdapter, short_id
from .merge import merge_all, merge_records
from .verifier import VerificationEngine

WORK_TYPES = {"article": "journal-article", "preprint": "posted-content", "book": "book", "book-chapter": "book-chapter", "dissertation": "dissertation"}


def build_access(r: PaperRecord) -> PaperAccess:
    """Access ladder: free full text, else the publisher page, else nothing. We never host or proxy paywalled PDFs."""
    publisher = r.doi_url or r.url
    if r.oa_url and r.is_oa is not False:
        status = f" (open-access status: {r.oa_status})" if r.oa_status and r.oa_status != "closed" else ""
        return PaperAccess(level="open_access", open_access_url=r.oa_url, publisher_url=publisher, note=f"A free, legal full-text copy is available{status}.")
    if publisher:
        return PaperAccess(
            level="publisher_page", publisher_url=publisher,
            note="No free full text was found. Open the publisher page; your institution's library login may give you access.",
        )
    return PaperAccess(level="none", note="No link is available for this record.")


def classify(q: str) -> Tuple[str, str]:
    q = q.strip()
    m = OPENALEX_ID_RE.match(q)
    if m:
        return "openalex_id", m.group(1).upper()
    doi = arxiv_to_doi(q) or extract_doi(q)
    if doi:
        return "doi", doi
    return "title", q


class PaperService:
    def __init__(self, engine: VerificationEngine, crossref: CrossRefAdapter, openalex: OpenAlexAdapter, settings: Optional[Settings] = None) -> None:
        self.engine, self.crossref, self.openalex = engine, crossref, openalex
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ lookup
    async def _card_for_doi(self, doi: str) -> PaperCard:
        parsed = ParsedCitation(index=1, raw=doi, doi=doi, parse_method="identifier")
        r = await self.engine.verify_citation(parsed, lookup_only=True)
        record = r.record or PaperRecord(doi=doi)
        return PaperCard(record=record, verdict=r.verdict, confidence=r.confidence, reason=r.reason, sources=r.sources, access=build_access(record), retraction=r.retraction)

    async def lookup(self, q: str) -> LookupResponse:
        kind, value = classify(q)
        if kind == "doi":
            card = await self._card_for_doi(value)
            msg = "" if card.verdict != "ghost" else "No record was found for this DOI."
            return LookupResponse(query=q, query_type="doi", paper=card, message=msg)

        if kind == "openalex_id":
            work = await self.openalex.get_work(value)
            if not work:
                return LookupResponse(query=q, query_type="openalex_id", message="No OpenAlex record was found for this ID.")
            from .adapters.openalex import work_to_record  # local import keeps module graph simple

            rec = work_to_record(work)
            if rec.doi:
                card = await self._card_for_doi(rec.doi)
                return LookupResponse(query=q, query_type="openalex_id", paper=card)
            src = SourceResult(source="openalex", label=self.openalex.label, status="match", method="doi", score=100, record=rec)
            card = PaperCard(record=rec, verdict="verified", confidence=100, reason="Found in OpenAlex (no DOI on record).", sources=[src], access=build_access(rec), retraction=rec.retraction)
            return LookupResponse(query=q, query_type="openalex_id", paper=card)

        # free text -> candidate list
        resp = await self.search(q, per_page=8)
        cands = resp.items
        return LookupResponse(
            query=q, query_type="title", candidates=cands,
            message="" if cands else "No papers matched that title. Try fewer words, or search by keywords.",
        )

    # ------------------------------------------------------------------ search
    async def search(
        self, q: str, year_from: Optional[int] = None, year_to: Optional[int] = None, oa_only: bool = False,
        work_type: Optional[str] = None, sort: str = "relevance", page: int = 1, per_page: int = 10,
    ) -> SearchResponse:
        oa_filters: List[str] = []
        cr_filters: List[str] = []
        if year_from:
            oa_filters.append(f"from_publication_date:{year_from}-01-01")
            cr_filters.append(f"from-pub-date:{year_from}")
        if year_to:
            oa_filters.append(f"to_publication_date:{year_to}-12-31")
            cr_filters.append(f"until-pub-date:{year_to}")
        if oa_only:
            oa_filters.append("open_access.is_oa:true")
        if work_type and work_type in WORK_TYPES:
            oa_filters.append(f"type:{work_type}")
            cr_filters.append(f"type:{WORK_TYPES[work_type]}")
        oa_sort = {"cited_by": "cited_by_count:desc", "newest": "publication_date:desc"}.get(sort)
        cr_sort = {"cited_by": "is-referenced-by-count", "newest": "published"}.get(sort)

        tasks = [self.openalex.search_works(q, per_page=per_page, page=page, filters=oa_filters or None, sort=oa_sort)]
        use_crossref = not oa_only  # CrossRef cannot filter on open access
        if use_crossref:
            tasks.append(self.crossref.search_works(q, rows=per_page, offset=(page - 1) * per_page, filters=",".join(cr_filters) or None, sort=cr_sort))
        responses = await asyncio.gather(*tasks)
        oa_resp = responses[0]
        cr_resp = responses[1] if use_crossref else None

        warnings: List[str] = []
        if oa_resp.status == "unavailable":
            warnings.append("OpenAlex did not respond; showing CrossRef results only.")
        if cr_resp is not None and cr_resp.status == "unavailable":
            warnings.append("CrossRef did not respond; showing OpenAlex results only.")

        merged: List[PaperRecord] = []
        by_key: dict = {}

        def key_of(r: PaperRecord) -> str:
            return r.doi.lower() if r.doi else "t:" + normalize(r.title)

        for rec in oa_resp.records:
            by_key[key_of(rec)] = [rec]
            merged.append(rec)
        order = {key_of(r): i for i, r in enumerate(merged)}
        extra: List[PaperRecord] = []
        if cr_resp is not None:
            for rec in cr_resp.records:
                k = key_of(rec)
                if k in by_key:
                    by_key[k].append(rec)
                else:
                    by_key[k] = [rec]
                    extra.append(rec)
        combined: List[PaperRecord] = []
        for rec in merged + extra:
            group = by_key[key_of(rec)]
            combined.append(merge_all(group) or rec)

        if sort == "cited_by":
            combined.sort(key=lambda r: r.cited_by_count or 0, reverse=True)
        elif sort == "newest":
            combined.sort(key=lambda r: r.year or 0, reverse=True)
        total = oa_resp.total if oa_resp.total is not None else (cr_resp.total if cr_resp else None)
        return SearchResponse(query=q, page=page, per_page=per_page, total=total, items=combined, warnings=warnings)

    # ------------------------------------------------------------------ network
    async def network(self, openalex_id: str, kind: str, page: int, per_page: int) -> Optional[NetworkResponse]:
        wid = short_id(openalex_id) or openalex_id
        if kind == "cited_by":
            resp = await self.openalex.cited_by(wid, page, per_page)
            if resp.status == "unavailable":
                return None
            return NetworkResponse(kind="cited_by", openalex_id=wid, page=page, per_page=per_page, total=resp.total or 0, items=resp.records)
        work = await self.openalex.get_work(wid, select="id,referenced_works,related_works")
        if work is None:
            return None
        ids = [short_id(i) or i for i in (work.get("referenced_works" if kind == "references" else "related_works") or [])]
        window = ids[(page - 1) * per_page : page * per_page]
        resp = await self.openalex.works_by_ids(window)
        return NetworkResponse(kind=kind, openalex_id=wid, page=page, per_page=per_page, total=len(ids), items=resp.records)  # type: ignore[arg-type]
