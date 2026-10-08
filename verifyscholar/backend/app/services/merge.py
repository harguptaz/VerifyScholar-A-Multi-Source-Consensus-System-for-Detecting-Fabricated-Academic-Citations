"""Combine records from several sources into one PaperRecord, and combine retraction signals."""
from __future__ import annotations

from typing import List, Optional

from ..schemas import PaperRecord, RetractionInfo, RetractionNotice
from ..utils.text import similarity, uniq

_PRIORITY = {"retracted": 4, "concern": 3, "corrected": 2, "none_found": 1, "unknown": 0}


def merge_retraction(infos: List[RetractionInfo]) -> RetractionInfo:
    notices: List[RetractionNotice] = []
    seen = set()
    for info in infos:
        for n in info.notices:
            key = (n.type.lower(), (n.doi or "").lower())
            if key not in seen:
                seen.add(key)
                notices.append(n)
    # a notice-bearing status always wins; otherwise prefer a definitive "none_found" over "unknown"
    status = max((i.status for i in infos), key=lambda s: _PRIORITY[s], default="unknown")
    return RetractionInfo(status=status, notices=notices)


def same_work(a: PaperRecord, b: PaperRecord) -> bool:
    if a.doi and b.doi:
        return a.doi.lower() == b.doi.lower()
    return similarity(a.title, b.title) >= 0.9


def merge_records(primary: PaperRecord, others: List[PaperRecord]) -> PaperRecord:
    """`primary` supplies the bibliographic fields (CrossRef is preferred upstream); others fill gaps."""
    out = primary.model_copy(deep=True)
    for o in others:
        if not same_work(out, o):
            continue
        for f in ("doi", "title", "journal", "publisher", "year", "volume", "issue", "pages", "type", "url", "doi_url", "language"):
            if not getattr(out, f) and getattr(o, f):
                setattr(out, f, getattr(o, f))
        if not out.authors and o.authors:
            out.authors = o.authors
        out.issn = uniq(out.issn + o.issn)
        out.journal_aliases = uniq(out.journal_aliases + o.journal_aliases)
        out.alt_years = sorted(set(out.alt_years + o.alt_years) - ({out.year} if out.year else set()))
        # open-access / identifiers / counts come from OpenAlex when present
        for f in ("is_oa", "oa_status", "oa_url", "openalex_id"):
            if getattr(o, f) is not None and getattr(o, f) != "":
                setattr(out, f, getattr(o, f))
        if o.abstract and (not out.abstract or len(o.abstract) > len(out.abstract)):
            out.abstract = o.abstract
        if o.cited_by_count is not None:
            out.cited_by_count = max(out.cited_by_count or 0, o.cited_by_count)
        out.retraction = merge_retraction([out.retraction, o.retraction])
        out.sources = uniq(out.sources + o.sources)
        if o.in_doaj is not None:
            out.in_doaj = o.in_doaj
    if out.doi and not out.doi_url:
        out.doi_url = f"https://doi.org/{out.doi}"
    return out


def merge_all(records: List[PaperRecord]) -> Optional[PaperRecord]:
    """Merge records describing the same work; CrossRef first as the bibliographic authority."""
    if not records:
        return None
    ordered = sorted(records, key=lambda r: 0 if "crossref" in r.sources else 1)
    return merge_records(ordered[0], ordered[1:])
