"""Common interface for the bibliographic sources."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Literal, Optional

from ...schemas import ParsedCitation, PaperRecord


@dataclass
class AdapterResponse:
    status: Literal["ok", "not_found", "unavailable"]
    records: List[PaperRecord] = field(default_factory=list)
    query: str = ""  # human-readable description of the request (goes to the transparency log)
    http_status: int | None = None
    error: str | None = None
    total: int | None = None


class SourceAdapter(ABC):
    """A source that can resolve a DOI or search for a citation and return paper records."""

    name: str = ""
    label: str = ""

    @abstractmethod
    async def lookup_doi(self, doi: str) -> AdapterResponse: ...

    @abstractmethod
    async def search(self, citation: ParsedCitation, rows: int = 10, year_window: bool = False) -> AdapterResponse:
        """Search for the cited paper. `year_window` restricts results to the cited year +/- 1 (a second-chance search)."""
        ...


def search_text(citation: ParsedCitation, max_len: int = 250) -> str:
    """Text used for a free-text search: the title when we have one, otherwise the raw citation."""
    if citation.title and len(citation.title) >= 8:
        return citation.title[:max_len]
    return citation.raw[:max_len]
