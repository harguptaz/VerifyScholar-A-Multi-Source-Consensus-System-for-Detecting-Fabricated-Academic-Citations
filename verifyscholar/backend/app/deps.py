"""Process-wide service instances, wired together once."""
from __future__ import annotations

from .config import get_settings
from .services.adapters.base import SourceAdapter
from .services.adapters.crossref import CrossRefAdapter
from .services.adapters.datacite import DataCiteAdapter
from .services.adapters.dblp import DblpAdapter
from .services.adapters.doaj import DoajAdapter
from .services.adapters.openalex import OpenAlexAdapter
from .services.adapters.openlibrary import OpenLibraryAdapter
from .services.grobid import grobid_client
from .services.http_client import http_service
from .services.verifier import VerificationEngine

settings = get_settings()
crossref = CrossRefAdapter(http_service, settings)
openalex = OpenAlexAdapter(http_service, settings)
doaj = DoajAdapter(http_service, settings)

# Secondary sources (asked only when CrossRef and OpenAlex leave a citation unsettled). Each can be switched off in .env.
secondary: list[SourceAdapter] = []
if settings.enable_dblp:
    secondary.append(DblpAdapter(http_service, settings))
if settings.enable_datacite:
    secondary.append(DataCiteAdapter(http_service, settings))
if settings.enable_openlibrary:
    secondary.append(OpenLibraryAdapter(http_service, settings))

engine = VerificationEngine(crossref, openalex, doaj, settings, secondary=secondary)
grobid = grobid_client
http = http_service