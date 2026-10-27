"""Shared async HTTP service: connection pooling, SQLite response cache, retries with backoff.

A timeout, a rate limit or a 5xx is reported as ``unavailable`` - it is *never* reported as
"not found". Only a definitive 404 produces ``not_found``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from urllib.parse import urlparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

import httpx
from tenacity import AsyncRetrying, RetryError, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import Settings, get_settings
from ..database import CacheRow, db

FetchStatus = Literal["ok", "not_found", "unavailable"]


class RetryableHTTPError(Exception):
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class FetchResult:
    status: FetchStatus
    data: Any = None
    http_status: Optional[int] = None
    error: Optional[str] = None
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class RequestContext:
    """Collects the transparency-log entries produced while verifying one citation."""

    citation_index: Optional[int] = None
    calls: List[Dict[str, Any]] = field(default_factory=list)

    def log(self, source: str, query: str, outcome: str) -> None:
        self.calls.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "citation_index": self.citation_index,
                "source": source,
                "query": query,
                "outcome": outcome,
            }
        )


class HttpService:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._client: Optional[httpx.AsyncClient] = None
        self._sem: Optional[asyncio.Semaphore] = None
        self._host_sems: Dict[str, asyncio.Semaphore] = {}

    def _host_limit(self, url: str) -> Optional[asyncio.Semaphore]:
        """A per-service concurrency cap, created lazily (semaphores belong to the running event loop)."""
        host = urlparse(url).hostname or ""
        st = self.settings
        limits: Dict[str, int] = {}
        for base_url, cap in (
            (st.openalex_base_url, st.openalex_max_concurrent_requests),
            (st.crossref_base_url, st.crossref_max_concurrent_requests),
            (st.dblp_base_url, st.dblp_max_concurrent_requests),
            (st.datacite_base_url, st.datacite_max_concurrent_requests),
            (st.openlibrary_base_url, st.openlibrary_max_concurrent_requests),
        ):
            limits[urlparse(base_url).hostname or ""] = cap
        if host not in limits or limits[host] <= 0:
            return None
        if host not in self._host_sems:
            self._host_sems[host] = asyncio.Semaphore(limits[host])
        return self._host_sems[host]

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.http_timeout_seconds, connect=10.0),
                follow_redirects=True,
                headers={"User-Agent": f"VerifyScholar/1.0 (mailto:{self.settings.contact_email})"},
                limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
            )
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.settings.http_max_concurrent_requests)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def use_client(self, client: httpx.AsyncClient) -> None:
        """Test hook: inject a client that uses a mock transport."""
        self._client = client
        self._sem = asyncio.Semaphore(self.settings.http_max_concurrent_requests)
        self._host_sems = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HttpService.start() has not been called")
        return self._client

    # ------------------------------------------------------------------ cache
    @staticmethod
    def cache_key(url: str, params: Optional[Dict[str, Any]], extra: str = "") -> str:
        safe = {k: v for k, v in (params or {}).items() if k not in ("api_key", "mailto")}
        raw = url + "?" + json.dumps(safe, sort_keys=True, default=str) + extra
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        with db.session() as s:
            row = s.get(CacheRow, key)
            if row and row.expires_at > time.time():
                return json.loads(row.value)
        return None

    def _cache_set(self, key: str, value: Dict[str, Any], ttl_seconds: float) -> None:
        with db.session() as s:
            row = s.get(CacheRow, key)
            payload = json.dumps(value)
            if row:
                row.value, row.expires_at = payload, time.time() + ttl_seconds
            else:
                s.add(CacheRow(key=key, value=payload, expires_at=time.time() + ttl_seconds))

    # ------------------------------------------------------------------ requests
    async def get_json(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        use_cache: bool = True,
    ) -> FetchResult:
        """GET a JSON document. 200 -> ok, 404 -> not_found, anything else -> unavailable."""
        key = self.cache_key(url, params)
        if use_cache:
            cached = self._cache_get(key)
            if cached is not None:
                return FetchResult(
                    status=cached["status"], data=cached.get("data"), http_status=cached.get("http_status"), from_cache=True
                )

        result = await self._request(url, params, headers)
        if use_cache and result.status in ("ok", "not_found"):
            ttl_h = self.settings.cache_ttl_hours if result.status == "ok" else self.settings.cache_negative_ttl_hours
            self._cache_set(key, {"status": result.status, "data": result.data, "http_status": result.http_status}, ttl_h * 3600)
        return result

    async def get_text(
        self, url: str, *, headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, Any]] = None, use_cache: bool = True
    ) -> FetchResult:
        """GET a text document (used for doi.org content negotiation)."""
        key = self.cache_key(url, params, extra=json.dumps(headers or {}, sort_keys=True))
        if use_cache:
            cached = self._cache_get(key)
            if cached is not None:
                return FetchResult(status=cached["status"], data=cached.get("data"), http_status=cached.get("http_status"), from_cache=True)
        result = await self._request(url, params, headers, as_text=True)
        if use_cache and result.status in ("ok", "not_found"):
            ttl_h = self.settings.cache_ttl_hours if result.status == "ok" else self.settings.cache_negative_ttl_hours
            self._cache_set(key, {"status": result.status, "data": result.data, "http_status": result.http_status}, ttl_h * 3600)
        return result

    async def _request(
        self, url: str, params: Optional[Dict[str, Any]], headers: Optional[Dict[str, str]], as_text: bool = False
    ) -> FetchResult:
        assert self._sem is not None
        attempts = max(1, self.settings.http_max_attempts)

        async def once() -> FetchResult:
            try:
                async with (self._host_limit(url) or contextlib.nullcontext()), self._sem:  # type: ignore[union-attr]
                    resp = await self.client.get(url, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise RetryableHTTPError(f"{type(exc).__name__}: {exc}") from exc
            code = resp.status_code
            if code == 404:
                return FetchResult("not_found", http_status=code)
            if code == 429 or code >= 500:
                retry_after = None
                try:
                    retry_after = float(resp.headers.get("Retry-After", ""))
                except ValueError:
                    pass
                raise RetryableHTTPError(f"HTTP {code}", retry_after=retry_after)
            if code in (401, 403):
                return FetchResult("unavailable", http_status=code, error=f"HTTP {code} (check API key / permissions)")
            if code >= 400:
                return FetchResult("unavailable", http_status=code, error=f"HTTP {code}")
            if as_text:
                return FetchResult("ok", data=resp.text, http_status=code)
            try:
                return FetchResult("ok", data=resp.json(), http_status=code)
            except ValueError:
                return FetchResult("unavailable", http_status=code, error="Invalid JSON in response")

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(attempts),
                wait=wait_exponential(multiplier=1, min=self.settings.http_backoff_min_seconds, max=self.settings.http_backoff_max_seconds),
                retry=retry_if_exception_type(RetryableHTTPError),
                reraise=True,
            ):
                with attempt:
                    try:
                        return await once()
                    except RetryableHTTPError as exc:
                        if exc.retry_after and exc.retry_after <= 10:
                            await asyncio.sleep(exc.retry_after)
                        raise
        except RetryableHTTPError as exc:
            return FetchResult("unavailable", error=str(exc), http_status=None)
        except RetryError as exc:  # pragma: no cover - reraise=True makes this unlikely
            return FetchResult("unavailable", error=str(exc))
        return FetchResult("unavailable", error="unknown error")  # pragma: no cover


http_service = HttpService()