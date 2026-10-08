"""CrossRef REST API adapter (https://api.crossref.org)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ...config import Settings, get_settings
from ...schemas import Author, ParsedCitation, PaperRecord, RetractionInfo, RetractionNotice
from ...utils.text import clean_doi, normalize, strip_tags, uniq
from ..http_client import HttpService
from .base import AdapterResponse, SourceAdapter, search_text

SEARCH_SELECT = (
    "DOI,title,author,container-title,short-container-title,issued,published,volume,issue,page,"
    "ISSN,publisher,type,URL,abstract,is-referenced-by-count,language,update-to"
)

RETRACTED_TYPES = {"retraction", "withdrawal", "removal", "partial_retraction"}
CONCERN_TYPES = {"expression_of_concern"}
CORRECTED_TYPES = {"correction", "corrigendum", "erratum", "addendum", "clarification"}


def _date_year(obj: Optional[Dict[str, Any]]) -> Optional[int]:
    try:
        parts = (obj or {}).get("date-parts", [[None]])[0]
        return int(parts[0]) if parts and parts[0] else None
    except (TypeError, ValueError, IndexError):
        return None


def retraction_from_notices(notices: List[RetractionNotice]) -> RetractionInfo:
    kinds = {n.type.lower() for n in notices}
    if kinds & RETRACTED_TYPES:
        status = "retracted"
    elif kinds & CONCERN_TYPES:
        status = "concern"
    elif kinds & CORRECTED_TYPES:
        status = "corrected"
    else:
        status = "none_found"  # other update types (new_version, new_edition ...) are not warnings
    return RetractionInfo(status=status, notices=notices)


def item_to_record(item: Dict[str, Any]) -> PaperRecord:
    """Convert one CrossRef work item into a PaperRecord."""
    doi = clean_doi(item["DOI"]) if item.get("DOI") else None
    authors: List[Author] = []
    for a in item.get("author", []) or []:
        family, given, name = a.get("family", "") or "", a.get("given", "") or "", a.get("name", "") or ""
        authors.append(Author(family=family or name, given=given, name=name or f"{given} {family}".strip()))

    titles = item.get("title") or []
    containers = item.get("container-title") or []
    shorts = item.get("short-container-title") or []
    issued_year = _date_year(item.get("issued"))
    print_year = _date_year(item.get("published-print"))
    online_year = _date_year(item.get("published-online"))
    pub_year = _date_year(item.get("published"))
    year = print_year or pub_year or issued_year or online_year
    alts = [y for y in uniq([str(y) for y in (issued_year, print_year, online_year, pub_year) if y]) if y != str(year)]

    notices: List[RetractionNotice] = []
    for entry in (item.get("updated-by") or []):
        if entry.get("type"):
            notices.append(
                RetractionNotice(
                    type=str(entry["type"]), source=str(entry.get("source", "crossref")), doi=clean_doi(entry["DOI"]) if entry.get("DOI") else None,
                    date=str(_date_year(entry.get("updated")) or ""), label=entry.get("label"),
                )
            )
    retraction = retraction_from_notices(notices) if notices else RetractionInfo(status="unknown")

    abstract = strip_tags(item["abstract"]) if item.get("abstract") else None
    return PaperRecord(
        doi=doi,
        title=strip_tags(titles[0]) if titles else None,
        authors=authors,
        journal=containers[0] if containers else None,
        journal_aliases=uniq(list(containers) + list(shorts)),
        issn=uniq(item.get("ISSN") or []),
        publisher=item.get("publisher"),
        year=year,
        alt_years=[int(y) for y in alts],
        volume=str(item["volume"]) if item.get("volume") else None,
        issue=str(item["issue"]) if item.get("issue") else None,
        pages=item.get("page"),
        type=item.get("type"),
        abstract=abstract,
        url=item.get("URL"),
        doi_url=f"https://doi.org/{doi}" if doi else None,
        cited_by_count=item.get("is-referenced-by-count"),
        language=item.get("language"),
        retraction=retraction,
        sources=["crossref"],
    )


class CrossRefAdapter(SourceAdapter):
    name = "crossref"
    label = "CrossRef REST API"

    def __init__(self, http: HttpService, settings: Optional[Settings] = None) -> None:
        self.http = http
        self.settings = settings or get_settings()

    # -------------------------------------------------------------- helpers
    @property
    def base(self) -> str:
        return self.settings.crossref_base_url.rstrip("/")

    def _params(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        p: Dict[str, Any] = {"mailto": self.settings.contact_email}
        if extra:
            p.update(extra)
        return p

    # -------------------------------------------------------------- SourceAdapter API
    async def lookup_doi(self, doi: str) -> AdapterResponse:
        url = f"{self.base}/works/{quote(doi, safe='/')}"
        res = await self.http.get_json(url, params=self._params())
        query = f"GET /works/{doi}"
        if res.status == "ok":
            msg = (res.data or {}).get("message")
            if isinstance(msg, dict) and msg:
                return AdapterResponse("ok", [item_to_record(msg)], query, res.http_status)
            return AdapterResponse("not_found", [], query, res.http_status)
        return AdapterResponse(res.status, [], query, res.http_status, res.error)

    async def search(self, citation: ParsedCitation, rows: int = 10, year_window: bool = False) -> AdapterResponse:
        # query.bibliographic is designed for reference strings; the first author goes into query.author, which
        # ranks (not filters), so a wrong author cannot hide the right paper.
        q = search_text(citation)
        filters = None
        if citation.year:
            if year_window:
                filters = f"from-pub-date:{citation.year - 1},until-pub-date:{citation.year + 1}"
            else:
                q = f"{q} {citation.year}"
        author = citation.authors[0].family if citation.authors and citation.authors[0].family else None
        return await self.search_works(q, rows=rows, filters=filters, author=author)

    # -------------------------------------------------------------- extras used by features
    async def search_works(
        self, q: str, rows: int = 5, offset: int = 0, filters: Optional[str] = None, sort: Optional[str] = None, author: Optional[str] = None
    ) -> AdapterResponse:
        params = self._params({"query.bibliographic": q, "rows": rows, "offset": offset, "select": SEARCH_SELECT})
        if author:
            params["query.author"] = author
        if filters:
            params["filter"] = filters
        if sort:
            params["sort"], params["order"] = sort, "desc"
        desc = f"GET /works?query.bibliographic={q[:120]}" + (f"&filter={filters}" if filters else "")
        res = await self.http.get_json(f"{self.base}/works", params=params)
        if res.status == "unavailable" and res.http_status == 400:
            params.pop("select", None)  # tolerate an unexpected select field
            res = await self.http.get_json(f"{self.base}/works", params=params)
        if res.status != "ok":
            return AdapterResponse(res.status, [], desc, res.http_status, res.error)
        msg = (res.data or {}).get("message", {})
        items = msg.get("items", []) or []
        recs = [item_to_record(i) for i in items]
        return AdapterResponse("ok" if recs else "not_found", recs, desc, res.http_status, total=msg.get("total-results"))

    async def retraction_notices(self, doi: str) -> AdapterResponse:
        """Notices (retractions, corrections, concerns) that update this DOI, incl. Retraction Watch data."""
        params = self._params({"filter": f"updates:{doi}", "rows": 20})
        desc = f"GET /works?filter=updates:{doi}"
        res = await self.http.get_json(f"{self.base}/works", params=params)
        if res.status != "ok":
            return AdapterResponse(res.status, [], desc, res.http_status, res.error)
        items = ((res.data or {}).get("message", {}) or {}).get("items", []) or []
        notices: List[RetractionNotice] = []
        for it in items:
            for upd in it.get("update-to", []) or []:
                if clean_doi(str(upd.get("DOI", ""))) == doi.lower() and upd.get("type"):
                    notices.append(
                        RetractionNotice(
                            type=str(upd["type"]), source=str(upd.get("source", "publisher")),
                            doi=clean_doi(it["DOI"]) if it.get("DOI") else None,
                            date=str(_date_year(upd.get("updated")) or ""), label=upd.get("label"),
                        )
                    )
        rec = PaperRecord(doi=doi, retraction=retraction_from_notices(notices), sources=["crossref"])
        return AdapterResponse("ok", [rec], desc, res.http_status)

    async def prefix_registered(self, prefix: str) -> AdapterResponse:
        """Is a DOI prefix registered with CrossRef? (not_found = not a CrossRef prefix)."""
        res = await self.http.get_json(f"{self.base}/prefixes/{quote(prefix, safe='')}", params=self._params())
        desc = f"GET /prefixes/{prefix}"
        if res.status == "ok":
            msg = (res.data or {}).get("message") or {}
            return AdapterResponse("ok", [PaperRecord(publisher=msg.get("name"), sources=["crossref"])], desc, res.http_status)
        return AdapterResponse(res.status, [], desc, res.http_status, res.error)

    async def find_journal(self, title: str) -> AdapterResponse:
        """Journals whose title resembles `title`. Records carry journal + ISSNs only."""
        params = self._params({"query": title, "rows": 5})
        desc = f"GET /journals?query={title[:100]}"
        res = await self.http.get_json(f"{self.base}/journals", params=params)
        if res.status != "ok":
            return AdapterResponse(res.status, [], desc, res.http_status, res.error)
        items = ((res.data or {}).get("message", {}) or {}).get("items", []) or []
        recs = [PaperRecord(journal=i.get("title"), issn=uniq(i.get("ISSN") or []), publisher=i.get("publisher"), sources=["crossref"]) for i in items]
        return AdapterResponse("ok" if recs else "not_found", recs, desc, res.http_status)

    async def journal_edge(self, issn: str, newest: bool) -> AdapterResponse:
        """Earliest or latest indexed work of a journal (year + volume)."""
        params = self._params({"rows": 1, "sort": "published", "order": "desc" if newest else "asc", "select": "issued,volume,published"})
        desc = f"GET /journals/{issn}/works?sort=published&order={'desc' if newest else 'asc'}&rows=1"
        res = await self.http.get_json(f"{self.base}/journals/{quote(issn, safe='')}/works", params=params)
        if res.status == "unavailable" and res.http_status == 400:
            params.pop("select", None)
            res = await self.http.get_json(f"{self.base}/journals/{quote(issn, safe='')}/works", params=params)
        if res.status != "ok":
            return AdapterResponse(res.status, [], desc, res.http_status, res.error)
        items = ((res.data or {}).get("message", {}) or {}).get("items", []) or []
        recs = [item_to_record(i) for i in items]
        return AdapterResponse("ok" if recs else "not_found", recs, desc, res.http_status)
