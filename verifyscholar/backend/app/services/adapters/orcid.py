"""ORCID adapter (https://info.orcid.org/documentation/api-tutorials/api-tutorial-searching-the-orcid-registry).

ORCID is a registry of researchers, not an index of papers, so it is never asked whether a paper exists. It answers two
questions the other sources cannot:

* Who claims this paper? `doi-self:"<doi>"` returns the researchers who list the DOI as their own work, so a cited
  author among them confirms the authorship.
* Does this researcher exist? A name search, used as a second opinion before an author is flagged as unknown.

Most researchers do not list every paper on ORCID, and many have no record at all, so a miss is never evidence against
a citation. ORCID can only add confidence.

The public API needs an access token, obtained once with the client ID and secret (client-credentials grant, scope
/read-public) and kept in memory. ORCID tokens are long-lived; if one is refused, a new one is fetched.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

import httpx

from ...config import Settings, get_settings
from ...schemas import Researcher
from ..http_client import FetchResult, HttpService

_MAX_CLAIMANTS = 50  # a paper with more claimants than this is a large collaboration; the first 50 are plenty to compare


@dataclass
class OrcidResponse:
    status: Literal["ok", "not_found", "unavailable"]
    people: List[Researcher] = field(default_factory=list)
    query: str = ""  # human-readable description of the request (goes to the transparency log)
    http_status: Optional[int] = None
    error: Optional[str] = None
    total: int = 0


def _clean(value: str) -> str:
    """Characters that would break ORCID's (Lucene) query syntax are dropped."""
    return re.sub(r'["\\]', "", value or "").strip()


def _first(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def person_from_result(item: Dict[str, Any]) -> Optional[Researcher]:
    """One entry of an expanded-search answer."""
    orcid = str(item.get("orcid-id") or "").strip()
    if not orcid:
        return None
    given = _first(item.get("given-names")).strip()
    family = _first(item.get("family-names")).strip()
    credit = _first(item.get("credit-name")).strip()
    others = [str(n) for n in (item.get("other-name") or []) if n] if isinstance(item.get("other-name"), list) else []
    name = credit or " ".join(p for p in (given, family) if p) or orcid
    return Researcher(orcid=orcid, given=given, family=family, name=name, other_names=others, url=f"https://orcid.org/{orcid}")


class OrcidAdapter:
    name = "orcid"
    label = "ORCID Public API"

    def __init__(self, http: HttpService, settings: Optional[Settings] = None) -> None:
        self.http = http
        self.settings = settings or get_settings()
        self._token: Optional[str] = None
        self._lock: Optional[asyncio.Lock] = None
        self.token_error: Optional[str] = None  # why the last token request failed (shown by the connection test)

    @property
    def base(self) -> str:
        return self.settings.orcid_base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return self.settings.orcid_configured

    # ------------------------------------------------------------------ access token
    async def _fetch_token(self) -> Optional[str]:
        s = self.settings
        try:
            resp = await self.http.client.post(
                s.orcid_token_url,
                data={"client_id": s.orcid_client_id, "client_secret": s.orcid_client_secret, "grant_type": "client_credentials", "scope": "/read-public"},
                headers={"Accept": "application/json"},
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            self.token_error = f"could not reach ORCID ({type(exc).__name__})"
            return None
        if resp.status_code in (400, 401, 403):
            self.token_error = f"ORCID refused the client ID / secret (HTTP {resp.status_code}): check ORCID_CLIENT_ID and ORCID_CLIENT_SECRET"
            return None
        if resp.status_code != 200:
            self.token_error = f"the token request failed (HTTP {resp.status_code})"
            return None
        try:
            token = (resp.json() or {}).get("access_token")
        except ValueError:
            token = None
        if not token:
            self.token_error = "ORCID's answer did not contain an access token"
            return None
        self.token_error = None
        return str(token)

    async def token(self, refresh: bool = False) -> Optional[str]:
        if self._lock is None:
            self._lock = asyncio.Lock()  # created lazily: a lock belongs to the running event loop
        async with self._lock:
            if refresh:
                self._token = None
            if self._token is None:
                self._token = await self._fetch_token()
            return self._token

    # ------------------------------------------------------------------ search
    async def _search(self, q: str, rows: int, desc: str, use_cache: bool = True) -> OrcidResponse:
        if not self.configured:
            return OrcidResponse("unavailable", [], desc, error="ORCID is not configured")
        res: Optional[FetchResult] = None
        for attempt in range(2):
            tok = await self.token(refresh=attempt > 0)
            if not tok:
                return OrcidResponse("unavailable", [], desc, error=self.token_error)
            res = await self.http.get_json(
                f"{self.base}/expanded-search/", params={"q": q, "rows": rows},
                headers={"Accept": "application/json", "Authorization": f"Bearer {tok}"}, use_cache=use_cache,
            )
            if res.http_status != 401:  # 401: the token was revoked or has expired, so get a new one and try once more
                break
        assert res is not None
        if res.status == "not_found":
            return OrcidResponse("not_found", [], desc, res.http_status)
        if res.status != "ok":
            return OrcidResponse("unavailable", [], desc, res.http_status, res.error)
        data = res.data or {}
        items = data.get("expanded-result") or []
        people = [p for p in (person_from_result(i) for i in items if isinstance(i, dict)) if p is not None]
        total = int(data.get("num-found") or len(people))
        return OrcidResponse("ok" if people else "not_found", people, desc, res.http_status, total=total)

    async def claimants(self, doi: str) -> OrcidResponse:
        """The researchers who list this DOI as their own work."""
        doi = _clean(doi).lower()
        return await self._search(f'doi-self:"{doi}"', _MAX_CLAIMANTS, f'GET /expanded-search/?q=doi-self:"{doi}"')

    async def person_exists(self, given: str, family: str) -> OrcidResponse:
        """Is there an ORCID record for this name? Only the first given name is used (records often omit middle names)."""
        family = _clean(family)
        first = _clean(given).split()[0] if _clean(given) else ""
        q = f'family-name:"{family}"' + (f' AND given-names:"{first}"' if first else "")
        return await self._search(q, 1, f"GET /expanded-search/?q={q}")

    async def check(self) -> Dict[str, Any]:
        """Connection test for /health/sources: get a token, then search for a paper whose authors are on ORCID."""
        tok = await self.token(refresh=True)
        if not tok:
            return {"ok": False, "detail": f"Could not get an ORCID access token: {self.token_error}", "http_status": None}
        res = await self._search('doi-self:"10.1038/nature14539"', 1, "connection test", use_cache=False)
        if res.status in ("ok", "not_found"):
            return {"ok": True, "detail": "Reachable (access token accepted)", "http_status": res.http_status}
        if res.http_status in (401, 403):
            return {"ok": False, "detail": f"The service refused the request (HTTP {res.http_status}): check ORCID_CLIENT_ID and ORCID_CLIENT_SECRET", "http_status": res.http_status}
        return {"ok": False, "detail": f"Cannot reach the service: {res.error or 'no response'}. Check the container's internet access, proxy or firewall.", "http_status": res.http_status}
