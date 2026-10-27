"""DOAJ adapter: is this journal in the Directory of Open Access Journals?

DOAJ only lists open-access journals, so a hit is positive evidence and a miss says nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ...config import Settings, get_settings
from ...utils.text import similarity
from ..http_client import HttpService

_LUCENE_SPECIAL = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


def lucene_phrase(title: str) -> str:
    """Make a journal title safe for DOAJ's query-string syntax (wildcards and fuzziness are disabled)."""
    cleaned = re.sub(r"\s+", " ", _LUCENE_SPECIAL.sub(" ", title)).strip()
    return f'"{cleaned}"'


_ISSN = re.compile(r"^(\d{4})-?(\d{3}[\dXx])$")


def normalize_issn(value: Optional[str]) -> Optional[str]:
    """'15491676', ' 1549-1676 ' and 'issn 1549-167x' all become the form DOAJ expects ('1549-1676', '1549-167X'); anything else is None."""
    text = re.sub(r"(?i)^e?issn[:\s]*", "", (value or "").strip())
    m = _ISSN.match(text.replace(" ", ""))
    return f"{m.group(1)}-{m.group(2).upper()}" if m else None


@dataclass
class JournalCheck:
    status: str  # match | no_record | unavailable
    query: str = ""
    http_status: Optional[int] = None
    error: Optional[str] = None
    title: Optional[str] = None
    issns: List[str] = field(default_factory=list)
    publisher: Optional[str] = None
    url: Optional[str] = None
    method: Optional[str] = None  # issn | title
    similarity: Optional[float] = None


class DoajAdapter:
    name = "doaj"
    label = "DOAJ"

    def __init__(self, http: HttpService, settings: Optional[Settings] = None) -> None:
        self.http = http
        self.settings = settings or get_settings()

    @property
    def base(self) -> str:
        return self.settings.doaj_base_url.rstrip("/")

    @staticmethod
    def _parse(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for r in results or []:
            b = r.get("bibjson") or {}
            issns = [x for x in (b.get("eissn"), b.get("pissn")) if x]
            out.append(
                {
                    "title": b.get("title"),
                    "alt": b.get("alternative_title"),
                    "issns": issns,
                    "publisher": (b.get("publisher") or {}).get("name"),
                    "id": r.get("id"),
                }
            )
        return out

    async def check_by_issn(self, issn: str) -> JournalCheck:
        desc = f"GET /search/journals/issn:{issn}"
        res = await self.http.get_json(f"{self.base}/search/journals/{quote('issn:' + issn, safe='')}")
        if res.status == "unavailable":
            return JournalCheck("unavailable", desc, res.http_status, res.error)
        rows = self._parse((res.data or {}).get("results", []) if res.status == "ok" else [])
        if not rows:
            return JournalCheck("no_record", desc, res.http_status)
        top = rows[0]
        return JournalCheck(
            "match", desc, res.http_status, title=top["title"], issns=top["issns"], publisher=top["publisher"],
            url=f"https://doaj.org/toc/{top['issns'][0]}" if top["issns"] else None, method="issn", similarity=1.0,
        )

    async def check_by_title(self, title: str) -> JournalCheck:
        phrase = lucene_phrase(title)
        desc = f"GET /search/journals/bibjson.title:{phrase}"
        res = await self.http.get_json(f"{self.base}/search/journals/{quote('bibjson.title:' + phrase, safe='')}", params={"pageSize": 5})
        if res.status == "unavailable":
            return JournalCheck("unavailable", desc, res.http_status, res.error)
        rows = self._parse((res.data or {}).get("results", []) if res.status == "ok" else [])
        best, best_sim = None, 0.0
        for r in rows:
            sim = max(similarity(title, r["title"]), similarity(title, r["alt"]))
            if sim > best_sim:
                best, best_sim = r, sim
        if best and best_sim >= 0.9:
            return JournalCheck(
                "match", desc, res.http_status, title=best["title"], issns=best["issns"], publisher=best["publisher"],
                url=f"https://doaj.org/toc/{best['issns'][0]}" if best["issns"] else None, method="title", similarity=best_sim,
            )
        return JournalCheck("no_record", desc, res.http_status, similarity=best_sim or None)