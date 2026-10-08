import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("CONTACT_EMAIL", "tests@example.com")
os.environ.setdefault("EMBEDDING_BACKEND", "tfidf")
os.environ.setdefault("HTTP_MAX_ATTEMPTS", "2")
os.environ.setdefault("HTTP_BACKOFF_MIN_SECONDS", "0.01")
os.environ.setdefault("RETRY_PAUSE_SECONDS", "0")

from app.config import get_settings  # noqa: E402
from app.database import db  # noqa: E402
from app.services.http_client import http_service  # noqa: E402
from tests.fakes import FakeWeb  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    db.configure("sqlite://")
    yield


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
async def http(web):
    client = httpx.AsyncClient(transport=httpx.MockTransport(web.handler), follow_redirects=True)
    http_service.use_client(client)
    yield http_service
    await client.aclose()
    http_service._client = None


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def engine(http, settings):
    from app.services.adapters.crossref import CrossRefAdapter
    from app.services.adapters.doaj import DoajAdapter
    from app.services.adapters.openalex import OpenAlexAdapter
    from app.services.verifier import VerificationEngine

    return VerificationEngine(CrossRefAdapter(http, settings), OpenAlexAdapter(http, settings), DoajAdapter(http, settings), settings)
