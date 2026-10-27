"""DBLP adapter (https://dblp.org/search/publ/api): computer-science journals, conferences and workshops.

DBLP is a curated bibliography, so a hit is strong evidence that a paper exists. It covers computer science only, and it
lists venues by short names ("NIPS", "CVPR"), so a venue that differs from the cited one is not held against a citation
(see `matching.compare`). It has no DOI lookup: it is searched by title.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...config import Settings, get_settings
from ...schemas import ParsedCitation, PaperRecord
from ...utils.text import clean_doi, normalize, uniq
from ..http_client import HttpService
from .base import AdapterResponse, SourceAdapter, split_display_name

TYPE_MAP = {
    "Journal Articles": "journal-article",
    "Conference and Workshop Papers": "proceedings-article",
    "Informal and Other Publications": "posted-content",  # mostly arXiv / CoRR entries
    "Books and Theses": "book",
    "Parts in Books or Collections": "book-chapter",
    "Editorship": "proceedings",
    "Reference Works": "reference-entry",
}
_DISAMBIGUATION = re.compile(r"\s+\d{4}$")  # DBLP tells two people of the same name apart with a number: "Wei Wang 0001"
_MAX_QUERY_WORDS = 12


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _doi_from(info: Dict[str, Any]) -> Optional[str]:
    if info.get("doi"):
        return clean_doi(str(info["doi"]))
    for link in _as_list(info.get("ee")):
        m = re.match(r"^https?://(?:dx\.)?doi\.org/(10\..+)$", str(link), flags=re.IGNORECASE)
        if m:
            return clean_doi(m.group(1))
    return None


def hit_to_record(hit: Dict[str, Any]) -> Optional[PaperRecord]:
    info = hit.get("info") or {}
    title = str(info.get("title") or "").strip().rstrip(".").strip()
    if not title:
        return None
    authors = []
    for a in _as_list((info.get("authors") or {}).get("author")):
        name = a.get("text") if isinstance(a, dict) else a
        name = _DISAMBIGUATION.sub("", str(name or "").strip())
        if name:
            authors.append(split_display_name(name))
    venues = [str(v) for v in _as_list(info.get("venue")) if v]
    journal = venues[0] if venues else None
    year = int(info["year"]) if str(info.get("year", "")).isdigit() else None
    corr = (journal or "").lower() == "corr"
    doi = _doi_from(info)
    links = [str(x) for x in _as_list(info.get("ee"))]
    url = links[0] if links else info.get("url")
    wtype = info.get("type")
    return PaperRecord(
        doi=doi,
        title=title,
        authors=authors,
        journal=journal,
        journal_aliases=uniq(venues),
        year=year,
        volume=None if corr else (str(info["volume"]) if info.get("volume") else None),  # CoRR volumes look like "abs/1706.03762"
        issue=str(info["number"]) if info.get("number") else None,
        pages=str(info["pages"]) if info.get("pages") else None,
        type=TYPE_MAP.get(wtype or "", (wtype or "").lower().replace(" ", "-") or None),
        url=url,
        doi_url=f"https://doi.org/{doi}" if doi else None,
        sources=["dblp"],
    )


class DblpAdapter(SourceAdapter):
    name = "dblp"
    label = "DBLP API"
    secondary = True
    supports_doi_lookup = False
    supports_year_window = False

    def __init__(self, http: HttpService, settings: Optional[Settings] = None) -> None:
        self.http = http
        self.settings = settings or get_settings()

    @property
    def base(self) -> str:
        return self.settings.dblp_base_url.rstrip("/")

    def relevant(self, citation: ParsedCitation, lookup_only: bool = False) -> bool:
        return not lookup_only and citation.kind != "book" and bool(citation.title and len(citation.title) >= 8)

    async def lookup_doi(self, doi: str) -> AdapterResponse:  # pragma: no cover - the engine skips it (supports_doi_lookup is False)
        return AdapterResponse("not_found", [], f"DBLP has no DOI lookup ({doi})")

    @staticmethod
    def query_for(citation: ParsedCitation) -> str:
        """DBLP ANDs the words of a query, and punctuation can break it, so the title is reduced to plain words."""
        return " ".join(normalize(citation.title).split()[:_MAX_QUERY_WORDS])

    async def search(self, citation: ParsedCitation, rows: int = 10, year_window: bool = False) -> AdapterResponse:
        q = self.query_for(citation)
        desc = f"GET /search/publ/api?q={q[:120]}"
        if not q:
            return AdapterResponse("not_found", [], desc)
        res = await self.http.get_json(f"{self.base}/search/publ/api", params={"q": q, "format": "json", "h": rows})
        if res.status != "ok":
            return AdapterResponse(res.status, [], desc, res.http_status, res.error)
        hits = ((((res.data or {}).get("result") or {}).get("hits")) or {})
        records = [r for r in (hit_to_record(h) for h in _as_list(hits.get("hit")) if isinstance(h, dict)) if r is not None]
        total = int(hits["@total"]) if str(hits.get("@total", "")).isdigit() else None
        return AdapterResponse("ok" if records else "not_found", records, desc, res.http_status, total=total)