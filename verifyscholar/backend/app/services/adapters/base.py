"""Common interface for the bibliographic sources."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Literal, Optional

from ...schemas import Author, ParsedCitation, PaperRecord

# Sources that are asked only when CrossRef and OpenAlex did not settle a citation. They add evidence ("this paper
# exists") but are never evidence against a citation: a miss, or a source that is down, does not change a verdict.
SECONDARY_SOURCES = frozenset({"dblp", "datacite", "openlibrary"})


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
    # Secondary sources are consulted only when the primary ones (CrossRef, OpenAlex) leave a citation unsettled.
    secondary: bool = False
    supports_doi_lookup: bool = True  # False: the source has no DOI lookup, so the engine skips that step
    supports_year_window: bool = True  # False: a second search restricted to the cited year would repeat the first

    def relevant(self, citation: ParsedCitation, lookup_only: bool = False) -> bool:
        """Is this source worth asking about this citation? (Used for secondary sources only.)"""
        return True

    @abstractmethod
    async def lookup_doi(self, doi: str) -> AdapterResponse: ...

    @abstractmethod
    async def search(self, citation: ParsedCitation, rows: int = 10, year_window: bool = False) -> AdapterResponse:
        """Search for the cited paper. `year_window` restricts results to the cited year +/- 1 (a second-chance search)."""
        ...


def split_display_name(display: str) -> Author:
    """Split a display name: 'Ashish Vaswani' -> family 'Vaswani', given 'Ashish'. 'Vaswani, Ashish' is read the same way."""
    display = (display or "").strip()
    if not display:
        return Author()
    if "," in display:
        family, _, given = display.partition(",")
        return Author(family=family.strip(), given=given.strip(), name=display)
    parts = display.split()
    return Author(family=parts[-1], given=" ".join(parts[:-1]), name=display)


def search_text(citation: ParsedCitation, max_len: int = 250) -> str:
    """Text used for a free-text search: the title when we have one, otherwise the raw citation."""
    if citation.title and len(citation.title) >= 8:
        return citation.title[:max_len]
    return citation.raw[:max_len]