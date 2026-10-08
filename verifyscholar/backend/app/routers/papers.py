"""Paper Finder (Mode D): lookup, search, citation network, corrected-citation formatting."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import deps
from ..schemas import FormatBody, FormatResponse, LookupResponse, NetworkResponse, SearchResponse
from ..services import formatters
from ..services.papers import PaperService

router = APIRouter(prefix="/papers", tags=["papers"])
service = PaperService(deps.engine, deps.crossref, deps.openalex, deps.settings)


@router.get("/lookup", response_model=LookupResponse)
async def lookup(id: str = Query(..., min_length=3, description="A DOI, doi.org link, arXiv link, OpenAlex ID (W…) or a paper title")) -> LookupResponse:
    return await service.lookup(id)


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=2),
    year_from: Optional[int] = Query(None, ge=1500, le=2100),
    year_to: Optional[int] = Query(None, ge=1500, le=2100),
    oa: bool = False,
    type: Optional[str] = Query(None, pattern="^(article|preprint|book|book-chapter|dissertation)$"),
    sort: str = Query("relevance", pattern="^(relevance|cited_by|newest)$"),
    page: int = Query(1, ge=1, le=100),
    per_page: int = Query(10, ge=1, le=50),
) -> SearchResponse:
    return await service.search(q, year_from, year_to, oa, type, sort, page, per_page)


@router.get("/{openalex_id}/{kind}", response_model=NetworkResponse)
async def network(openalex_id: str, kind: str, page: int = Query(1, ge=1, le=200), per_page: int = Query(15, ge=1, le=50)) -> NetworkResponse:
    """`kind` is references, cited-by or related. `openalex_id` looks like W2741809807."""
    kinds = {"references": "references", "cited-by": "cited_by", "related": "related"}
    if kind not in kinds:
        raise HTTPException(404, "kind must be one of: references, cited-by, related")
    if not openalex_id.upper().startswith("W") or not openalex_id[1:].isdigit():
        raise HTTPException(400, "openalex_id must look like W2741809807")
    result = await service.network(openalex_id.upper(), kinds[kind], page, per_page)
    if result is None:
        raise HTTPException(502, "OpenAlex did not return this paper's network. Try again later.")
    return result


@router.post("/format", response_model=FormatResponse)
async def format_citation(body: FormatBody) -> FormatResponse:
    """Corrected citation: the verified record formatted in the chosen style (CSL via doi.org, local fallback)."""
    text, source = await formatters.format_reference(body.record, body.style, deps.http, deps.settings)
    return FormatResponse(style=body.style, text=text, bibtex=formatters.to_bibtex(body.record, formatters.bibtex_key(body.record, set())), source=source)  # type: ignore[arg-type]
