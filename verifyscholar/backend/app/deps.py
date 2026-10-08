"""Process-wide service instances, wired together once."""
from __future__ import annotations

from .config import get_settings
from .services.adapters.crossref import CrossRefAdapter
from .services.adapters.doaj import DoajAdapter
from .services.adapters.openalex import OpenAlexAdapter
from .services.grobid import grobid_client
from .services.http_client import http_service
from .services.verifier import VerificationEngine

settings = get_settings()
crossref = CrossRefAdapter(http_service, settings)
openalex = OpenAlexAdapter(http_service, settings)
doaj = DoajAdapter(http_service, settings)
engine = VerificationEngine(crossref, openalex, doaj, settings)
grobid = grobid_client
http = http_service
