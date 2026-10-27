"""VerifyScholar API."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import deps
from .database import db
from .routers import bibliography, papers, verify
from .services.docx_convert import libreoffice_binary
from .services.http_client import http_service

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("verifyscholar")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.configure()
    await http_service.start()
    s = deps.settings
    if s.contact_email == "you@example.com":
        log.warning("CONTACT_EMAIL is not set: CrossRef and OpenAlex asked for a real contact address (polite pool). Set it in .env.")
    if not s.openalex_api_key:
        log.warning("OPENALEX_API_KEY is not set: OpenAlex strongly recommends a free key (https://openalex.org/settings/api).")
    yield
    await http_service.close()


app = FastAPI(
    title="VerifyScholar API",
    version="1.0.0",
    description="Verify AI-generated academic citations against CrossRef, OpenAlex, DOAJ, DBLP, DataCite and Open Library; find papers; check relevance.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware, allow_origins=deps.settings.cors_origin_list, allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
app.include_router(verify.router)
app.include_router(papers.router)
app.include_router(bibliography.router)


@app.get("/health", tags=["system"])
async def health() -> dict:
    s = deps.settings
    g = await deps.grobid.status()
    return {
        "status": "ok",
        "grobid": g["alive"],
        "grobid_url": g["url"],
        "grobid_detail": g["detail"],
        "libreoffice": libreoffice_binary() is not None,
        "embedding_backend_setting": s.embedding_backend,
        "openalex_api_key_set": bool(s.openalex_api_key),
        "contact_email_set": s.contact_email != "you@example.com",
    }


@app.get("/health/sources", tags=["system"])
async def health_sources() -> dict:
    """Live connectivity test for CrossRef, OpenAlex, DOAJ and the enabled secondary sources (DBLP, DataCite, Open Library).

    Each source is asked for a record that is known to exist. Responses are not cached. Use it when every
    citation comes back "Inconclusive": it shows whether the backend can reach the internet and whether your
    OpenAlex key is accepted.
    """
    s = deps.settings
    oa_params = {"mailto": s.contact_email, "select": "id"}
    if s.openalex_api_key:
        oa_params["api_key"] = s.openalex_api_key
    probes = {
        "crossref": (f"{s.crossref_base_url.rstrip('/')}/works/10.1038/nature14539", {"mailto": s.contact_email}),
        "openalex": (f"{s.openalex_base_url.rstrip('/')}/works/doi:10.1038/nature14539", oa_params),
        "doaj": (f"{s.doaj_base_url.rstrip('/')}/search/journals/{quote('issn:0028-0836', safe='')}", {}),
    }
    if s.enable_dblp:
        probes["dblp"] = (f"{s.dblp_base_url.rstrip('/')}/search/publ/api", {"q": "attention is all you need", "format": "json", "h": 1})
    if s.enable_datacite:
        probes["datacite"] = (f"{s.datacite_base_url.rstrip('/')}/dois/10.48550/arxiv.1607.06450", {})
    if s.enable_openlibrary:
        probes["openlibrary"] = (f"{s.openlibrary_base_url.rstrip('/')}/search.json", {"title": "thinking fast and slow", "limit": 1})

    async def probe(name: str, url: str, params: dict) -> tuple[str, dict]:
        res = await http_service.get_json(url, params=params, use_cache=False)
        if res.status == "ok":
            return name, {"ok": True, "detail": "Reachable", "http_status": res.http_status}
        if res.status == "not_found":
            return name, {"ok": True, "detail": "Reachable (the test record was not found, which is unexpected but harmless)", "http_status": res.http_status}
        if res.http_status in (401, 403):
            hint = "check OPENALEX_API_KEY" if name == "openalex" else "access was refused"
            return name, {"ok": False, "detail": f"The service refused the request (HTTP {res.http_status}): {hint}", "http_status": res.http_status}
        reason = res.error or "no response"
        return name, {"ok": False, "detail": f"Cannot reach the service: {reason}. Check the container's internet access, proxy or firewall.", "http_status": res.http_status}

    results = await asyncio.gather(*(probe(n, u, p) for n, (u, p) in probes.items()))
    return dict(results)