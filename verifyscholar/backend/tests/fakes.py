"""A fake CrossRef / OpenAlex / DOAJ for tests: canned JSON served through httpx.MockTransport."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List
from urllib.parse import unquote

import httpx


def crossref_item(doi: str, title: str, authors: List[tuple], journal: str, year: int, volume: str = "", issue: str = "", page: str = "",
                  issn: List[str] | None = None, abstract: str | None = None, cited: int = 0, publisher: str = "Test Publisher") -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "DOI": doi, "title": [title], "author": [{"family": f, "given": g} for f, g in authors], "container-title": [journal],
        "issued": {"date-parts": [[year]]}, "published-print": {"date-parts": [[year]]}, "ISSN": issn or [], "publisher": publisher,
        "type": "journal-article", "URL": f"https://doi.org/{doi}", "is-referenced-by-count": cited,
    }
    if volume:
        item["volume"] = volume
    if issue:
        item["issue"] = issue
    if page:
        item["page"] = page
    if abstract:
        item["abstract"] = f"<jats:p>{abstract}</jats:p>"
    return item


def openalex_work(doi: str, title: str, authors: List[str], journal: str, year: int, wid: str, volume: str = "", pages: tuple = (None, None),
                  abstract: str | None = None, oa_url: str | None = None, retracted: bool = False, cited: int = 0, issn: List[str] | None = None) -> Dict[str, Any]:
    inv = None
    if abstract:
        inv = {}
        for pos, w in enumerate(abstract.split()):
            inv.setdefault(w, []).append(pos)
    return {
        "id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}" if doi else None, "title": title, "display_name": title,
        "publication_year": year, "authorships": [{"author": {"display_name": a}} for a in authors],
        "primary_location": {"source": {"display_name": journal, "issn": issn or []}, "landing_page_url": f"https://doi.org/{doi}" if doi else None},
        "biblio": {"volume": volume or None, "issue": None, "first_page": pages[0], "last_page": pages[1]}, "type": "article",
        "cited_by_count": cited, "is_retracted": retracted, "open_access": {"is_oa": bool(oa_url), "oa_status": "gold" if oa_url else "closed", "oa_url": oa_url},
        "best_oa_location": None, "abstract_inverted_index": inv, "language": "en",
    }


class FakeWeb:
    def __init__(self) -> None:
        self.crossref: Dict[str, Dict[str, Any]] = {}
        self.openalex: Dict[str, Dict[str, Any]] = {}
        self.cr_search: List[Dict[str, Any]] = []
        self.oa_search: List[Dict[str, Any]] = []
        self.cr_search_windowed: List[Dict[str, Any]] | None = None  # returned when the query is restricted to a year window
        self.oa_search_windowed: List[Dict[str, Any]] | None = None
        self.doaj_titles: Dict[str, Dict[str, Any]] = {}  # normalised title -> bibjson
        self.doaj_issns: Dict[str, Dict[str, Any]] = {}
        self.notices: Dict[str, List[Dict[str, Any]]] = {}  # doi -> list of {DOI, type, source}
        self.cr_journals: List[Dict[str, Any]] = []
        self.registered_prefixes: set[str] = set()
        self.down: set[str] = set()
        self.fail_first: Dict[str, int] = {}  # host -> number of initial requests that fail with HTTP 503
        self.calls: List[str] = []

    # ------------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host, path = url.host, unquote(url.path)
        q = dict(url.params)
        self.calls.append(f"{host}{path}?{url.query.decode()}")
        if host in self.down:
            return httpx.Response(503, json={"error": "down"})
        if self.fail_first.get(host, 0) > 0:
            self.fail_first[host] -= 1
            return httpx.Response(503, json={"error": "flaky"})
        if host == "api.crossref.org":
            return self._crossref(path, q)
        if host == "api.openalex.org":
            return self._openalex(path, q)
        if host == "doaj.org":
            return self._doaj(path)
        if host == "doi.org":
            return httpx.Response(404, text="not found")
        return httpx.Response(404, json={})

    def _crossref(self, path: str, q: Dict[str, str]) -> httpx.Response:
        if path.startswith("/works/"):
            doi = path[len("/works/"):].lower()
            if doi in self.crossref:
                return httpx.Response(200, json={"status": "ok", "message": self.crossref[doi]})
            return httpx.Response(404, text="Resource not found.")
        if path == "/works":
            flt = q.get("filter", "")
            if flt.startswith("updates:"):
                doi = flt.split(":", 1)[1].lower()
                items = [{"DOI": n["DOI"], "update-to": [{"DOI": doi, "type": n["type"], "source": n.get("source", "publisher")}]} for n in self.notices.get(doi, [])]
                return httpx.Response(200, json={"status": "ok", "message": {"items": items, "total-results": len(items)}})
            items = self.cr_search_windowed if ("from-pub-date" in flt and self.cr_search_windowed is not None) else self.cr_search
            return httpx.Response(200, json={"status": "ok", "message": {"items": items[: int(q.get("rows", 5))], "total-results": len(items)}})
        if path.startswith("/prefixes/"):
            prefix = path.split("/")[-1]
            if prefix in self.registered_prefixes:
                return httpx.Response(200, json={"status": "ok", "message": {"name": "Some Publisher", "prefix": prefix}})
            return httpx.Response(404, text="Resource not found.")
        if path == "/journals":
            return httpx.Response(200, json={"status": "ok", "message": {"items": self.cr_journals}})
        m = re.match(r"^/journals/([^/]+)/works$", path)
        if m:
            order = q.get("order", "asc")
            works = [w for w in self.crossref.values() if m.group(1) in (w.get("ISSN") or [])]
            works.sort(key=lambda w: w["issued"]["date-parts"][0][0], reverse=(order == "desc"))
            return httpx.Response(200, json={"status": "ok", "message": {"items": works[:1]}})
        return httpx.Response(404, json={})

    def _openalex(self, path: str, q: Dict[str, str]) -> httpx.Response:
        if path.startswith("/works/doi:"):
            doi = path[len("/works/doi:"):].lower()
            if doi in self.openalex:
                return httpx.Response(200, json=self.openalex[doi])
            return httpx.Response(404, json={"error": "not found"})
        if path == "/works":
            flt = q.get("filter", "")
            if flt.startswith("openalex:"):
                ids = flt.split(":", 1)[1].split("|")
                works = [w for w in self.openalex.values() if w["id"].split("/")[-1] in ids]
                return httpx.Response(200, json={"results": works, "meta": {"count": len(works)}})
            if flt.startswith("cites:"):
                return httpx.Response(200, json={"results": self.oa_search, "meta": {"count": len(self.oa_search)}})
            res = self.oa_search_windowed if ("publication_year" in flt and self.oa_search_windowed is not None) else self.oa_search
            return httpx.Response(200, json={"results": res, "meta": {"count": len(res)}})
        m = re.match(r"^/works/(W\d+)$", path)
        if m:
            for w in self.openalex.values():
                if w["id"].endswith(m.group(1)):
                    return httpx.Response(200, json=w)
            return httpx.Response(404, json={})
        if path == "/authors":
            name = q.get("search", "")
            exists = "nonexistent" not in name.lower()
            return httpx.Response(200, json={"results": [{"id": "A1"}] if exists else [], "meta": {"count": 1 if exists else 0}})
        return httpx.Response(404, json={})

    def _doaj(self, path: str) -> httpx.Response:
        query = path.split("/search/journals/")[-1]
        if query.startswith("issn:"):
            b = self.doaj_issns.get(query[5:])
            return httpx.Response(200, json={"total": 1 if b else 0, "results": [{"id": "x", "bibjson": b}] if b else []})
        if query.startswith("bibjson.title:"):
            title = re.sub(r"[^a-z0-9 ]", "", query[len("bibjson.title:"):].lower()).strip()
            b = self.doaj_titles.get(title)
            return httpx.Response(200, json={"total": 1 if b else 0, "results": [{"id": "x", "bibjson": b}] if b else []})
        return httpx.Response(200, json={"total": 0, "results": []})


# A few real-looking records used across tests
DEEP_LEARNING_DOI = "10.1038/nature14539"


def seed_deep_learning(web: FakeWeb) -> None:
    web.crossref[DEEP_LEARNING_DOI] = crossref_item(
        DEEP_LEARNING_DOI, "Deep learning", [("LeCun", "Yann"), ("Bengio", "Yoshua"), ("Hinton", "Geoffrey")], "Nature", 2015,
        volume="521", issue="7553", page="436-444", issn=["0028-0836", "1476-4687"], cited=60000,
        abstract="Deep learning allows computational models composed of multiple processing layers to learn representations of data with multiple levels of abstraction.",
    )
    web.openalex[DEEP_LEARNING_DOI] = openalex_work(
        DEEP_LEARNING_DOI, "Deep learning", ["Yann LeCun", "Yoshua Bengio", "Geoffrey Hinton"], "Nature", 2015, "W2919115771", volume="521",
        pages=("436", "444"), abstract="Deep learning allows computational models composed of multiple processing layers to learn representations of data with multiple levels of abstraction.",
        oa_url="https://example.org/dl.pdf", cited=60000, issn=["0028-0836"],
    )
