"""OpenAlex adapter (https://api.openalex.org)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ...config import Settings, get_settings
from ...schemas import Author, ParsedCitation, PaperRecord, RetractionInfo, RetractionNotice
from ...utils.text import clean_doi, uniq
from ..http_client import HttpService
from .base import AdapterResponse, SourceAdapter, search_text

WORK_SELECT = (
    "id,doi,title,display_name,publication_year,publication_date,authorships,primary_location,biblio,type,"
    "cited_by_count,is_retracted,open_access,best_oa_location,abstract_inverted_index,language"
)

TYPE_MAP = {"article": "journal-article", "preprint": "posted-content", "book-chapter": "book-chapter", "book": "book"}


def short_id(openalex_id: Optional[str]) -> Optional[str]:
    if not openalex_id:
        return None
    return openalex_id.rstrip("/").split("/")[-1]


def rebuild_abstract(inverted: Optional[Dict[str, List[int]]]) -> Optional[str]:
    """OpenAlex ships abstracts as {word: [positions]}; turn that back into text."""
    if not inverted:
        return None
    positions: Dict[int, str] = {}
    for word, idxs in inverted.items():
        for i in idxs:
            positions[i] = word
    if not positions:
        return None
    return " ".join(positions[i] for i in sorted(positions))


def _split_name(display: str) -> Author:
    parts = display.replace(",", " ").split()
    if not parts:
        return Author(name=display)
    return Author(family=parts[-1], given=" ".join(parts[:-1]), name=display)


def work_to_record(w: Dict[str, Any]) -> PaperRecord:
    doi = clean_doi(w["doi"]) if w.get("doi") else None
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    biblio = w.get("biblio") or {}
    first, last = biblio.get("first_page"), biblio.get("last_page")
    pages = f"{first}-{last}" if first and last and first != last else (first or None)
    oa = w.get("open_access") or {}
    best = w.get("best_oa_location") or {}
    oa_url = oa.get("oa_url") or best.get("pdf_url") or best.get("landing_page_url")
    issn = src.get("issn") or ([src["issn_l"]] if src.get("issn_l") else [])
    is_retracted = w.get("is_retracted")
    if is_retracted is True:
        retraction = RetractionInfo(status="retracted", notices=[RetractionNotice(type="retraction", source="openalex")])
    elif is_retracted is False:
        retraction = RetractionInfo(status="none_found")
    else:
        retraction = RetractionInfo(status="unknown")
    authors = [_split_name((a.get("author") or {}).get("display_name", "")) for a in (w.get("authorships") or []) if (a.get("author") or {}).get("display_name")]
    wtype = w.get("type")
    journal = src.get("display_name")
    return PaperRecord(
        doi=doi,
        title=w.get("title") or w.get("display_name"),
        authors=authors,
        journal=journal,
        journal_aliases=[journal] if journal else [],
        issn=uniq(issn),
        publisher=src.get("host_organization_name"),
        year=w.get("publication_year"),
        volume=biblio.get("volume") or None,
        issue=biblio.get("issue") or None,
        pages=pages,
        type=TYPE_MAP.get(wtype or "", wtype),
        abstract=rebuild_abstract(w.get("abstract_inverted_index")),
        url=loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else None),
        doi_url=f"https://doi.org/{doi}" if doi else None,
        is_oa=oa.get("is_oa"),
        oa_status=oa.get("oa_status"),
        oa_url=oa_url,
        openalex_id=short_id(w.get("id")),
        cited_by_count=w.get("cited_by_count"),
        language=w.get("language"),
        retraction=retraction,
        sources=["openalex"],
    )


class OpenAlexAdapter(SourceAdapter):
    name = "openalex"
    label = "OpenAlex API"

    def __init__(self, http: HttpService, settings: Optional[Settings] = None) -> None:
        self.http = http
        self.settings = settings or get_settings()

    @property
    def base(self) -> str:
        return self.settings.openalex_base_url.rstrip("/")

    def _params(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        p: Dict[str, Any] = {"mailto": self.settings.contact_email}
        if self.settings.openalex_api_key:
            p["api_key"] = self.settings.openalex_api_key
        if extra:
            p.update(extra)
        return p

    async def _get(self, path: str, params: Dict[str, Any]) -> Any:
        res = await self.http.get_json(f"{self.base}{path}", params=self._params(params))
        if res.status == "unavailable" and res.http_status == 400 and "select" in params:
            params = {k: v for k, v in params.items() if k != "select"}  # tolerate an unexpected select field
            res = await self.http.get_json(f"{self.base}{path}", params=self._params(params))
        return res

    # -------------------------------------------------------------- SourceAdapter API
    async def lookup_doi(self, doi: str) -> AdapterResponse:
        res = await self._get(f"/works/doi:{quote(doi, safe='/:')}", {"select": WORK_SELECT})
        query = f"GET /works/doi:{doi}"
        if res.status == "ok" and isinstance(res.data, dict) and res.data.get("id"):
            return AdapterResponse("ok", [work_to_record(res.data)], query, res.http_status)
        if res.status == "ok":
            return AdapterResponse("not_found", [], query, res.http_status)
        return AdapterResponse(res.status, [], query, res.http_status, res.error)

    async def search(self, citation: ParsedCitation, rows: int = 10, year_window: bool = False) -> AdapterResponse:
        filters = [f"publication_year:{citation.year - 1}-{citation.year + 1}"] if (year_window and citation.year) else None
        return await self.search_works(search_text(citation), per_page=rows, filters=filters)

    # -------------------------------------------------------------- extras used by features
    async def search_works(
        self, q: str, per_page: int = 10, page: int = 1, filters: Optional[List[str]] = None, sort: Optional[str] = None
    ) -> AdapterResponse:
        params: Dict[str, Any] = {"search": q, "per_page": per_page, "page": page, "select": WORK_SELECT}
        if filters:
            params["filter"] = ",".join(filters)
        if sort:
            params["sort"] = sort
        desc = f"GET /works?search={q[:120]}" + (f"&filter={params['filter']}" if filters else "")
        res = await self._get("/works", params)
        if res.status != "ok":
            return AdapterResponse(res.status, [], desc, res.http_status, res.error)
        data = res.data or {}
        recs = [work_to_record(w) for w in data.get("results", []) or []]
        total = (data.get("meta") or {}).get("count")
        return AdapterResponse("ok" if recs else "not_found", recs, desc, res.http_status, total=total)

    async def get_work(self, work_id: str, select: Optional[str] = None) -> Optional[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if select:
            params["select"] = select
        res = await self._get(f"/works/{quote(work_id, safe='')}", params)
        return res.data if res.status == "ok" and isinstance(res.data, dict) else None

    async def works_by_ids(self, ids: List[str]) -> AdapterResponse:
        ids = [short_id(i) or i for i in ids][:100]
        if not ids:
            return AdapterResponse("ok", [], "GET /works?filter=openalex:")
        params = {"filter": "openalex:" + "|".join(ids), "per_page": min(100, len(ids)), "select": WORK_SELECT}
        desc = f"GET /works?filter=openalex:{'|'.join(ids)[:100]}"
        res = await self._get("/works", params)
        if res.status != "ok":
            return AdapterResponse(res.status, [], desc, res.http_status, res.error)
        recs = [work_to_record(w) for w in (res.data or {}).get("results", []) or []]
        order = {i: n for n, i in enumerate(ids)}
        recs.sort(key=lambda r: order.get(r.openalex_id or "", 10**6))
        return AdapterResponse("ok", recs, desc, res.http_status, total=len(ids))

    async def cited_by(self, work_id: str, page: int, per_page: int, sort: str = "cited_by_count:desc") -> AdapterResponse:
        params = {"filter": f"cites:{work_id}", "per_page": per_page, "page": page, "sort": sort, "select": WORK_SELECT}
        desc = f"GET /works?filter=cites:{work_id}"
        res = await self._get("/works", params)
        if res.status != "ok":
            return AdapterResponse(res.status, [], desc, res.http_status, res.error)
        data = res.data or {}
        recs = [work_to_record(w) for w in data.get("results", []) or []]
        return AdapterResponse("ok", recs, desc, res.http_status, total=(data.get("meta") or {}).get("count", len(recs)))

    async def author_exists(self, name: str) -> AdapterResponse:
        desc = f"GET /authors?search={name}"
        res = await self._get("/authors", {"search": name, "per_page": 3, "select": "id,display_name,works_count"})
        if res.status != "ok":
            return AdapterResponse(res.status, [], desc, res.http_status, res.error)
        data = res.data or {}
        count = (data.get("meta") or {}).get("count", len(data.get("results", []) or []))
        return AdapterResponse("ok" if count else "not_found", [], desc, res.http_status, total=count)
