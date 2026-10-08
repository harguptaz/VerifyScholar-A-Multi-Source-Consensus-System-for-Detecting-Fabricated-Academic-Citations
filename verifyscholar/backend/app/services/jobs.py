"""Background jobs: split/parse -> verify -> (relevance) -> summary -> transparency log."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Settings, get_settings
from ..database import JobRow, db
from ..schemas import CitationResult, DocumentInfo, JobResult, JobSummary, ParsedCitation
from . import relevance, transparency
from .docx_convert import ConversionError, docx_to_pdf
from .grobid import GrobidClient, GrobidError, ParsedDocument
from .parsing import finalize, heuristic_parse
from .splitter import parse_bibtex, parse_ris, split_citations
from .verifier import VerificationEngine
from ..schemas import Author
from ..utils.text import extract_doi


# ------------------------------------------------------------------------------------------
# job table helpers
# ------------------------------------------------------------------------------------------
def create_job(mode: str, params: Optional[Dict[str, Any]] = None) -> str:
    job_id = str(uuid.uuid4())
    with db.session() as s:
        s.add(JobRow(id=job_id, mode=mode, status="queued", params=params or {}))
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    with db.session() as s:
        row = s.get(JobRow, job_id)
        if row:
            for k, v in fields.items():
                setattr(row, k, v)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with db.session() as s:
        row = s.get(JobRow, job_id)
        if not row:
            return None
        return {
            "id": row.id, "mode": row.mode, "status": row.status, "stage": row.stage, "total": row.total, "completed": row.completed,
            "error": row.error, "result": row.result, "params": row.params,
        }


def fail_job(job_id: str, message: str) -> None:
    update_job(job_id, status="error", error=message, stage="Failed", finished_at=datetime.now(timezone.utc))


def _summarise(results: List[CitationResult], document: Optional[DocumentInfo], started: float) -> JobSummary:
    return JobSummary(
        total=len(results),
        verified=sum(r.verdict == "verified" for r in results),
        suspicious=sum(r.verdict == "suspicious" for r in results),
        ghost=sum(r.verdict == "ghost" for r in results),
        inconclusive=sum(r.verdict == "inconclusive" for r in results),
        retracted=sum(r.retraction.status == "retracted" for r in results),
        orphans=len(document.orphan_indices) if document else 0,
        elapsed_seconds=round(time.monotonic() - started, 1),
    )


def _finish(job_id: str, results: List[CitationResult], document: Optional[DocumentInfo], warnings: List[str], started: float) -> None:
    transparency.write_job_log(job_id, results)
    payload = JobResult(summary=_summarise(results, document, started), results=results, document=document, warnings=warnings)
    update_job(job_id, status="done", stage="Done", completed=len(results), total=len(results), result=payload.model_dump(mode="json"), finished_at=datetime.now(timezone.utc))


async def _verify(job_id: str, engine: VerificationEngine, parsed: List[ParsedCitation]) -> List[CitationResult]:
    update_job(job_id, total=len(parsed), completed=0, stage=f"Checking citation 1 of {len(parsed)} against CrossRef, OpenAlex and DOAJ…")

    async def progress(done: int, total: int, _r: CitationResult) -> None:
        nxt = min(done + 1, total)
        update_job(job_id, completed=done, stage=f"Checking citation {nxt} of {total} against CrossRef, OpenAlex and DOAJ…" if done < total else "Finishing up…")

    def retrying(n: int) -> None:
        update_job(job_id, stage=f"Retrying {n} citation{'s' if n != 1 else ''} whose data source did not respond…")

    return await engine.verify_many(parsed, progress, on_retry=retrying)


# ------------------------------------------------------------------------------------------
# Mode A: pasted citations
# ------------------------------------------------------------------------------------------
async def parse_raw_citations(raws: List[str], grobid: GrobidClient, settings: Settings, warnings: List[str]) -> List[ParsedCitation]:
    heuristic = [heuristic_parse(r, i) for i, r in enumerate(raws, start=1)]
    if not settings.use_grobid_for_citations:
        return heuristic
    try:
        if not await grobid.is_alive():
            warnings.append("GROBID is not reachable, so a built-in parser was used. Parsing is less accurate; start GROBID for best results.")
            return heuristic
        parsed = await grobid.process_citation_list(raws)
    except GrobidError as exc:
        warnings.append(f"GROBID failed ({exc}); a built-in parser was used instead.")
        return heuristic
    for g, h in zip(parsed, heuristic):  # fill gaps in GROBID's output from the heuristic parser
        g.title = g.title or h.title
        g.authors = g.authors or h.authors
        g.journal = g.journal or h.journal
        g.year = g.year or h.year
        g.volume, g.issue, g.pages = g.volume or h.volume, g.issue or h.issue, g.pages or h.pages
        # a publisher that GROBID did not label as a journal must not be borrowed as one from the heuristic parser
        if g.publisher and g.journal and g.journal.lower() == g.publisher.lower():
            g.journal = None
        finalize(g)
    return parsed


async def run_citations_job(job_id: str, text: str, engine: VerificationEngine, grobid: GrobidClient, settings: Optional[Settings] = None) -> None:
    settings = settings or get_settings()
    started = time.monotonic()
    warnings: List[str] = []
    try:
        update_job(job_id, status="running", stage="Splitting citations")
        raws = split_citations(text)
        if not raws:
            return fail_job(job_id, "No citations were found in the pasted text.")
        if len(raws) > settings.max_citations_per_job:
            return fail_job(job_id, f"{len(raws)} citations found; the limit per job is {settings.max_citations_per_job}.")
        update_job(job_id, stage="Parsing citations", total=len(raws))
        parsed = await parse_raw_citations(raws, grobid, settings, warnings)
        results = await _verify(job_id, engine, parsed)
        _finish(job_id, results, None, warnings, started)
    except Exception as exc:  # pragma: no cover - last-resort guard so a job never stays "running"
        fail_job(job_id, f"Unexpected error: {exc}")


# ------------------------------------------------------------------------------------------
# Modes B / C: uploaded documents (PDF, DOCX) and reference-manager files (BibTeX, RIS)
# ------------------------------------------------------------------------------------------
def _author_from_string(s: str) -> Author:
    s = s.strip()
    if "," in s:
        fam, given = [p.strip() for p in s.split(",", 1)]
        return Author(family=fam, given=given, name=s)
    toks = s.split()
    return Author(family=toks[-1] if toks else s, given=" ".join(toks[:-1]), name=s)


def parsed_from_import(entries: List[Dict[str, Any]]) -> List[ParsedCitation]:
    out = []
    for i, e in enumerate(entries, start=1):
        year = None
        try:
            year = int(str(e.get("year", ""))[:4]) if e.get("year") else None
        except ValueError:
            pass
        doi = extract_doi(e.get("doi") or "") if e.get("doi") else None
        authors = [_author_from_string(a) for a in e.get("authors", []) if a]
        out.append(finalize(ParsedCitation(
            index=i, raw=e.get("raw") or (e.get("title") or ""), doi=doi, title=e.get("title"), authors=authors, journal=e.get("journal"),
            year=year, volume=e.get("volume"), issue=e.get("issue"), pages=(e.get("pages") or "").replace("--", "-") or None, parse_method="import",
        )))
    return out


async def run_upload_job(
    job_id: str, filename: str, data: bytes, relevance_on: bool, engine: VerificationEngine, grobid: GrobidClient, settings: Optional[Settings] = None
) -> None:
    settings = settings or get_settings()
    started = time.monotonic()
    warnings: List[str] = []
    ext = Path(filename).suffix.lower()
    try:
        update_job(job_id, status="running", stage="Reading the uploaded file")
        document: Optional[DocumentInfo] = None
        parsed_doc: Optional[ParsedDocument] = None

        if ext in (".bib", ".ris"):
            text = data.decode("utf-8", errors="replace")
            entries = parse_bibtex(text) if ext == ".bib" else parse_ris(text)
            parsed = parsed_from_import(entries)
            if not parsed:
                return fail_job(job_id, f"No references were found in this {ext} file.")
        elif ext in (".pdf", ".docx"):
            pdf = data
            if ext == ".docx":
                update_job(job_id, stage="Converting DOCX to PDF")
                pdf = await docx_to_pdf(data, filename)
            update_job(job_id, stage="Extracting references with GROBID (this can take a little while)")
            parsed_doc = await grobid.process_fulltext(pdf, Path(filename).stem + ".pdf")
            parsed = parsed_doc.references
            if not parsed:
                return fail_job(job_id, "No reference list was found in this document. Check that it has a bibliography and is not a scanned image.")
        else:
            return fail_job(job_id, f"Unsupported file type '{ext}'. Upload a PDF, DOCX, BibTeX (.bib) or RIS (.ris) file.")

        if len(parsed) > settings.max_citations_per_job:
            warnings.append(f"The document has {len(parsed)} references; only the first {settings.max_citations_per_job} were checked.")
            parsed = parsed[: settings.max_citations_per_job]

        results = await _verify(job_id, engine, parsed)

        if parsed_doc is not None:
            if relevance_on:
                update_job(job_id, stage="Comparing cited abstracts with your text")
                try:
                    document = await relevance.analyze(parsed_doc, results, settings)
                except Exception as exc:  # the relevance check is advisory; never lose the verification results
                    warnings.append(f"The relevance check could not be completed: {exc}")
                    document = relevance.link_only(parsed_doc, results)
            else:
                document = relevance.link_only(parsed_doc, results)
            document.filename = filename
        _finish(job_id, results, document, warnings, started)
    except (GrobidError, ConversionError) as exc:
        fail_job(job_id, str(exc))
    except Exception as exc:  # pragma: no cover
        fail_job(job_id, f"Unexpected error: {exc}")
