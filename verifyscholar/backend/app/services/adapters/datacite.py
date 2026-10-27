"""DataCite adapter (https://api.datacite.org): DOIs registered with DataCite instead of CrossRef.

DataCite is the registration agency for arXiv (10.48550), Zenodo (10.5281), figshare (10.6084), most dataset and software
repositories, and many university repositories. A DOI that CrossRef reports as "not found" may simply be a DataCite DOI.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ...config import Settings, get_settings
from ...schemas import Author, ParsedCitation, PaperRecord
from ...utils.text import normalize, strip_tags
from ..http_client import HttpService
from .base import AdapterResponse, SourceAdapter, split_display_name

TYPE_MAP = {
    "preprint": "posted-content",
    "journalarticle": "journal-article",
    "conferencepaper": "proceedings-article",
    "conferenceproceeding": "proceedings",
    "book": "book",
    "bookchapter": "book-chapter",
    "dissertation": "dissertation",
    "report": "report",
}
_MAX_QUERY_WORDS = 14


def _publisher(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return value.get("name") or None
    return str(value) if value else None


def _title(titles: List[Dict[str, Any]]) -> Optional[str]:
    main = [t.get("title") for t in titles or [] if t.get("title") and not t.get("titleType")]
    anything = [t.get("title") for t in titles or [] if t.get("title")]
    return (main or anything or [None])[0]


def _creator(c: Dict[str, Any]) -> Optional[Author]:
    name = (c.get("name") or "").strip()
    family, given = (c.get("familyName") or "").strip(), (c.get("givenName") or "").strip()
    if family:
        return Author(family=family, given=given, name=name or f"{family}, {given}".strip(", "))
    if not name:
        return None
    if c.get("nameType") == "Organizational":
        return Author(name=name)
    return split_display_name(name)


def _years(dates: List[Dict[str, Any]]) -> List[int]:
    out = []
    for d in dates or []:
        if d.get("dateType") in ("Issued", "Created"):
            m = re.match(r"^(\d{4})", str(d.get("date") or ""))
            if m:
                out.append(int(m.group(1)))
    return out


def item_to_record(item: Dict[str, Any]) -> Optional[PaperRecord]:
    a = item.get("attributes") or {}
    doi = (a.get("doi") or item.get("id") or "").lower() or None
    title = _title(a.get("titles") or [])
    if not title:
        return None
    types = a.get("types") or {}
    general = str(types.get("resourceTypeGeneral") or "")
    container = a.get("container") or {}
    first, last = container.get("firstPage"), container.get("lastPage")
    pages = f"{first}-{last}" if first and last and str(first) != str(last) else (str(first) if first else None)
    year = a.get("publicationYear")
    year = int(year) if str(year or "").isdigit() else None
    abstract = next((d.get("description") for d in a.get("descriptions") or [] if d.get("descriptionType") == "Abstract" and d.get("description")), None)
    journal = container.get("title") or None
    return PaperRecord(
        doi=doi,
        title=strip_tags(title),
        authors=[x for x in (_creator(c) for c in a.get("creators") or []) if x is not None],
        journal=journal,
        journal_aliases=[journal] if journal else [],
        publisher=_publisher(a.get("publisher")),
        year=year,
        alt_years=sorted(set(_years(a.get("dates") or [])) - ({year} if year else set())),
        volume=str(container["volume"]) if container.get("volume") else None,
        issue=str(container["issue"]) if container.get("issue") else None,
        pages=pages,
        type=TYPE_MAP.get(general.lower(), general.lower() or None),
        abstract=strip_tags(abstract) if abstract else None,
        url=a.get("url") or (f"https://doi.org/{doi}" if doi else None),
        doi_url=f"https://doi.org/{doi}" if doi else None,
        cited_by_count=a.get("citationCount"),
        language=a.get("language") or None,
        sources=["datacite"],
    )


class DataCiteAdapter(SourceAdapter):
    name = "datacite"
    label = "DataCite API"
    secondary = True
    supports_year_window = False

    def __init__(self, http: HttpService, settings: Optional[Settings] = None) -> None:
        self.http = http
        self.settings = settings or get_settings()

    @property
    def base(self) -> str:
        return self.settings.datacite_base_url.rstrip("/")

    def relevant(self, citation: ParsedCitation, lookup_only: bool = False) -> bool:
        if citation.kind == "book":
            return False
        return bool(citation.doi) or bool(citation.title and len(citation.title) >= 8 and not lookup_only)

    async def lookup_doi(self, doi: str) -> AdapterResponse:
        query = f"GET /dois/{doi}"
        res = await self.http.get_json(f"{self.base}/dois/{quote(doi, safe='/:')}")
        if res.status == "ok":
            record = item_to_record((res.data or {}).get("data") or {}) if isinstance(res.data, dict) else None
            return AdapterResponse("ok" if record else "not_found", [record] if record else [], query, res.http_status)
        return AdapterResponse(res.status, [], query, res.http_status, res.error)

    async def search(self, citation: ParsedCitation, rows: int = 10, year_window: bool = False) -> AdapterResponse:
        q = " ".join(normalize(citation.title).split()[:_MAX_QUERY_WORDS])
        desc = f"GET /dois?query={q[:120]}"
        if not q:
            return AdapterResponse("not_found", [], desc)
        res = await self.http.get_json(f"{self.base}/dois", params={"query": q, "page[size]": rows, "disable-facets": "true"})
        if res.status != "ok":
            return AdapterResponse(res.status, [], desc, res.http_status, res.error)
        data = res.data if isinstance(res.data, dict) else {}
        records = [r for r in (item_to_record(i) for i in data.get("data") or [] if isinstance(i, dict)) if r is not None]
        total = (data.get("meta") or {}).get("total")
        return AdapterResponse("ok" if records else "not_found", records, desc, res.http_status, total=total if isinstance(total, int) else None)

    async def prefix_registered(self, prefix: str) -> AdapterResponse:
        """Is this DOI prefix registered with DataCite? (CrossRef's prefix list does not include DataCite's.)"""
        query = f"GET /prefixes/{prefix}"
        res = await self.http.get_json(f"{self.base}/prefixes/{quote(prefix, safe='')}")
        return AdapterResponse(res.status, [], query, res.http_status, res.error)