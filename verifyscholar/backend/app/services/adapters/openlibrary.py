"""Open Library adapter (https://openlibrary.org/search.json): books.

CrossRef and OpenAlex cover books poorly. Open Library lists one entry per work with its first publication year and the
years of its editions, so a book cited as a later edition still matches.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...config import Settings, get_settings
from ...schemas import ParsedCitation, PaperRecord
from ..http_client import HttpService
from .base import AdapterResponse, SourceAdapter, split_display_name

FIELDS = "key,title,subtitle,author_name,first_publish_year,publish_year,publisher,language,edition_count"
_MAX_ALT_YEARS = 60


def _first(value: Any) -> Optional[str]:
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value else None


def doc_to_record(doc: Dict[str, Any]) -> Optional[PaperRecord]:
    title = str(doc.get("title") or "").strip()
    if not title:
        return None
    subtitle = str(doc.get("subtitle") or "").strip()
    if subtitle:
        title = f"{title}: {subtitle}"
    first_year = doc.get("first_publish_year")
    first_year = int(first_year) if str(first_year or "").isdigit() else None
    edition_years = sorted({int(y) for y in doc.get("publish_year") or [] if str(y).isdigit()})[:_MAX_ALT_YEARS]
    year = first_year or (edition_years[0] if edition_years else None)
    key = doc.get("key") or ""
    return PaperRecord(
        title=title,
        authors=[split_display_name(n) for n in doc.get("author_name") or [] if n],
        publisher=_first(doc.get("publisher")),
        year=year,
        alt_years=[y for y in edition_years if y != year],
        type="book",
        url=f"https://openlibrary.org{key}" if key else None,
        language=_first(doc.get("language")),
        sources=["openlibrary"],
    )


class OpenLibraryAdapter(SourceAdapter):
    name = "openlibrary"
    label = "Open Library API"
    secondary = True
    supports_doi_lookup = False
    supports_year_window = False

    def __init__(self, http: HttpService, settings: Optional[Settings] = None) -> None:
        self.http = http
        self.settings = settings or get_settings()

    @property
    def base(self) -> str:
        return self.settings.openlibrary_base_url.rstrip("/")

    def relevant(self, citation: ParsedCitation, lookup_only: bool = False) -> bool:
        if lookup_only or not (citation.title and len(citation.title) >= 5):
            return False
        # a reference that looks like a book, or one the parser could not classify and that names no journal or DOI
        return citation.kind == "book" or (citation.kind == "unknown" and not citation.journal and not citation.doi)

    async def lookup_doi(self, doi: str) -> AdapterResponse:  # pragma: no cover - the engine skips it (supports_doi_lookup is False)
        return AdapterResponse("not_found", [], f"Open Library has no DOI lookup ({doi})")

    async def search(self, citation: ParsedCitation, rows: int = 10, year_window: bool = False) -> AdapterResponse:
        title = (citation.title or "").strip()
        desc = f"GET /search.json?title={title[:120]}"
        if not title:
            return AdapterResponse("not_found", [], desc)
        # title only: the authors are compared afterwards, so a book cited with a wrong author is reported, not lost
        res = await self.http.get_json(f"{self.base}/search.json", params={"title": title[:250], "limit": rows, "fields": FIELDS})
        if res.status != "ok":
            return AdapterResponse(res.status, [], desc, res.http_status, res.error)
        data = res.data if isinstance(res.data, dict) else {}
        records: List[PaperRecord] = [r for r in (doc_to_record(d) for d in data.get("docs") or [] if isinstance(d, dict)) if r is not None]
        total = data.get("numFound")
        return AdapterResponse("ok" if records else "not_found", records, desc, res.http_status, total=total if isinstance(total, int) else None)