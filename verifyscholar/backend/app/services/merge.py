"""Combine records from several sources into one PaperRecord, and combine retraction signals."""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..schemas import PaperRecord, RetractionInfo, RetractionNotice
from ..utils.text import similarity, surname_key, uniq
from .adapters.base import SECONDARY_SOURCES

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


def _first_family(r: PaperRecord) -> Optional[str]:
    for a in r.authors:
        key = surname_key(a.family or a.name or "")
        if key:
            return key
    return None


def is_duplicate(a: PaperRecord, b: PaperRecord) -> bool:
    """Two records of the same work under different identifiers (a re-registered DOI, a preprint and its published version).

    Same DOI, or an (almost) identical title with the same first author. A record without authors is never called a
    duplicate on its title alone: generic titles ("Introduction") are shared by unrelated papers.
    """
    if a.doi and b.doi and a.doi.lower() == b.doi.lower():
        return True
    if similarity(a.title, b.title) < 0.95:
        return False
    fa, fb = _first_family(a), _first_family(b)
    return bool(fa and fb and fa == fb)


def _richness(r: PaperRecord) -> int:
    return sum(1 for v in (r.journal, r.volume, r.pages, r.doi, r.authors) if v)


def collapse_duplicates(primary: PaperRecord, candidates: List[PaperRecord], cited_year: Optional[int]) -> Tuple[PaperRecord, int]:
    """Pick ONE record for a work that a source lists several times; returns (record, number of duplicates seen).

    Preference: the record dated the cited year, otherwise the EARLIEST one (the original publication, not a later
    re-registration), otherwise the most complete one. The years of all the duplicates are kept as `alt_years`, so a year
    that any copy carries counts as known for the work. No other field is copied across: a later copy may name a
    different venue, and mixing them would invent a record that does not exist.
    """
    group = [primary] + [c for c in candidates if c is not primary and is_duplicate(primary, c)]
    if len(group) == 1:
        return primary, 1

    def key(r: PaperRecord) -> tuple:
        return (0 if cited_year and r.year == cited_year else 1, r.year if r.year else 9999, -_richness(r))

    chosen = min(group, key=key).model_copy(deep=True)
    years = {y for r in group for y in ([r.year] if r.year else []) + list(r.alt_years)}
    chosen.alt_years = sorted(years - ({chosen.year} if chosen.year else set()))
    return chosen, len(group)


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


def _years(r: PaperRecord) -> set:
    return {y for y in ([r.year] if r.year else []) + list(r.alt_years)}


def year_compatible(a: PaperRecord, b: PaperRecord) -> bool:
    """Could these two records be the same publication? (Within a year of each other, or one of them has no year.)

    A later re-registration or a preprint of the same work is dated years apart. When a record from DBLP, DataCite or
    Open Library is the best match, copying that other record's DOI, venue or year into it would invent a record that
    does not exist.
    """
    ya, yb = _years(a), _years(b)
    if not ya or not yb:
        return True
    return any(abs(x - y) <= 1 for x in ya for y in yb)


def _is_secondary(r: PaperRecord) -> bool:
    return bool(r.sources) and r.sources[0] in SECONDARY_SOURCES


def merge_all(records: List[PaperRecord], lead: Optional[PaperRecord] = None) -> Optional[PaperRecord]:
    """Merge records describing the same work; CrossRef first as the bibliographic authority.

    When `lead` (the best-matching record) comes from a secondary source (DBLP, DataCite, Open Library) it is the base
    instead, and the other records only fill its gaps if their year is compatible with it.
    """
    if not records:
        return None
    if lead is not None and _is_secondary(lead):
        return merge_records(lead, [r for r in records if r is not lead and year_compatible(lead, r)])
    ordered = sorted(records, key=lambda r: 0 if "crossref" in r.sources else 1)
    base = ordered[0]
    # a secondary record dated differently from the base is a different version of the work: do not mix it in
    return merge_records(base, [r for r in ordered[1:] if not _is_secondary(r) or year_compatible(base, r)])