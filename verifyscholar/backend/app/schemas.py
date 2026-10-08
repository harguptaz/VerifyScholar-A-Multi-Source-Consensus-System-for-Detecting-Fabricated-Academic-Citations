"""Pydantic models shared by the engine, the API and the exports."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------------------
# Bibliographic building blocks
# --------------------------------------------------------------------------------------
class Author(BaseModel):
    family: str = ""
    given: str = ""
    name: str = ""  # display name as supplied by the source

    def display(self) -> str:
        if self.family and self.given:
            return f"{self.family}, {self.given}"
        return self.family or self.name


class RetractionNotice(BaseModel):
    type: str  # retraction | withdrawal | removal | partial_retraction | correction | erratum | expression_of_concern ...
    source: str = ""  # publisher | retraction-watch | openalex | crossref
    doi: Optional[str] = None
    date: Optional[str] = None
    label: Optional[str] = None


class RetractionInfo(BaseModel):
    # retracted | concern | corrected | none_found | unknown
    status: Literal["retracted", "concern", "corrected", "none_found", "unknown"] = "unknown"
    notices: List[RetractionNotice] = Field(default_factory=list)


class PaperRecord(BaseModel):
    """The shared 'paper record' every adapter returns and every screen reads from."""

    doi: Optional[str] = None
    title: Optional[str] = None
    authors: List[Author] = Field(default_factory=list)
    journal: Optional[str] = None
    journal_aliases: List[str] = Field(default_factory=list)
    issn: List[str] = Field(default_factory=list)
    publisher: Optional[str] = None
    year: Optional[int] = None
    alt_years: List[int] = Field(default_factory=list)  # e.g. online vs print year
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    type: Optional[str] = None
    abstract: Optional[str] = None
    url: Optional[str] = None  # landing page of the version of record
    doi_url: Optional[str] = None
    is_oa: Optional[bool] = None
    oa_status: Optional[str] = None
    oa_url: Optional[str] = None
    openalex_id: Optional[str] = None  # short form, e.g. W2741809807
    cited_by_count: Optional[int] = None
    language: Optional[str] = None
    in_doaj: Optional[bool] = None
    retraction: RetractionInfo = Field(default_factory=RetractionInfo)
    sources: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Parsed citations
# --------------------------------------------------------------------------------------
class ParsedCitation(BaseModel):
    index: int
    raw: str
    doi: Optional[str] = None
    title: Optional[str] = None
    authors: List[Author] = Field(default_factory=list)
    journal: Optional[str] = None
    year: Optional[int] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    issn: Optional[str] = None
    parse_method: Literal["grobid", "heuristic", "import", "identifier"] = "heuristic"
    # what kind of work this reference seems to be (decides how it is checked and explained)
    kind: Literal["article", "preprint", "conference", "book", "unknown"] = "unknown"
    publisher: Optional[str] = None
    arxiv_id: Optional[str] = None  # e.g. 1607.06450 (its DOI is 10.48550/arXiv.1607.06450)
    # in-text linking (document mode)
    ref_id: Optional[str] = None


# --------------------------------------------------------------------------------------
# Verification results
# --------------------------------------------------------------------------------------
FieldStatus = Literal["match", "partial", "mismatch", "missing"]


class FieldMatch(BaseModel):
    submitted: Optional[str] = None
    found: Optional[str] = None
    similarity: Optional[float] = None
    status: FieldStatus = "missing"


class CandidateInfo(BaseModel):
    """A search result that was considered for a citation, and why it was or was not accepted."""

    title: Optional[str] = None
    doi: Optional[str] = None
    year: Optional[int] = None
    journal: Optional[str] = None
    title_similarity: float = 0.0
    accepted: bool = False
    note: str = ""


class SourceResult(BaseModel):
    source: str  # crossref | openalex | doaj
    label: str  # display name
    kind: Literal["record", "journal"] = "record"
    status: Literal["match", "partial", "no_record", "unavailable", "skipped"] = "skipped"
    method: Optional[Literal["doi", "search", "issn", "title"]] = None
    score: Optional[int] = None  # 0-100, None when not applicable / unavailable
    fields: Dict[str, FieldMatch] = Field(default_factory=dict)
    record: Optional[PaperRecord] = None
    doi_resolves_to: Optional[PaperRecord] = None  # set when the cited DOI points to a different paper
    candidates: List[CandidateInfo] = Field(default_factory=list)  # closest search results considered
    note: Optional[str] = None


class HeuristicFlag(BaseModel):
    code: str
    severity: Literal["high", "medium", "low"]
    message: str


class Links(BaseModel):
    doi_url: Optional[str] = None
    landing_page: Optional[str] = None
    open_access: Optional[str] = None


class Relevance(BaseModel):
    status: Literal["assessed", "not_assessed"] = "not_assessed"
    reason: Optional[str] = None
    sentence_score: Optional[float] = None
    paper_score: Optional[float] = None
    label: Optional[Literal["well_supported", "possibly_misattributed", "background", "needs_review"]] = None
    best_sentence: Optional[str] = None
    mentions: int = 0
    orphan: bool = False
    sentences: List[str] = Field(default_factory=list)


Verdict = Literal["verified", "suspicious", "ghost", "inconclusive"]


class CitationResult(BaseModel):
    index: int
    raw: str
    parsed: ParsedCitation
    verdict: Verdict = "inconclusive"
    confidence: Optional[int] = None
    sources: List[SourceResult] = Field(default_factory=list)
    record: Optional[PaperRecord] = None  # merged best record (if any)
    flags: List[HeuristicFlag] = Field(default_factory=list)
    links: Links = Field(default_factory=Links)
    doi_status: Literal["found", "mismatch", "not_found", "no_doi", "unavailable"] = "no_doi"
    journal_status: Literal["verified_in_index", "found_in_doaj", "not_found", "not_provided", "unavailable"] = "not_provided"
    reason: str = ""
    retraction: RetractionInfo = Field(default_factory=RetractionInfo)
    relevance: Optional[Relevance] = None
    # transparency-log material collected while verifying (not sent to the client)
    calls: List[Dict[str, Any]] = Field(default_factory=list, exclude=True)


class JobSummary(BaseModel):
    total: int = 0
    verified: int = 0
    suspicious: int = 0
    ghost: int = 0
    inconclusive: int = 0
    retracted: int = 0
    orphans: int = 0
    elapsed_seconds: float = 0.0


class DocumentInfo(BaseModel):
    filename: Optional[str] = None
    title: Optional[str] = None
    abstract: Optional[str] = None
    n_references: int = 0
    orphan_indices: List[int] = Field(default_factory=list)
    unmatched_markers: List[str] = Field(default_factory=list)
    embedding_backend: Optional[str] = None


class JobResult(BaseModel):
    summary: JobSummary
    results: List[CitationResult]
    document: Optional[DocumentInfo] = None
    warnings: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# API request / response bodies
# --------------------------------------------------------------------------------------
class TextBody(BaseModel):
    text: str


class VerifyCitationsBody(BaseModel):
    text: str


class CountResponse(BaseModel):
    count: int


class JobCreated(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    id: str
    mode: str
    status: str
    stage: str = ""
    total: int = 0
    completed: int = 0
    error: Optional[str] = None
    result: Optional[JobResult] = None


class FormatBody(BaseModel):
    record: PaperRecord
    style: Literal["apa", "ieee", "harvard", "vancouver", "mla"] = "apa"


class FormatResponse(BaseModel):
    style: str
    text: str
    bibtex: str
    source: Literal["csl", "local"]


class BibliographyExportBody(BaseModel):
    job_id: Optional[str] = None
    extra_papers: List[PaperRecord] = Field(default_factory=list)
    format: Literal["bibtex", "ris", "text", "csv"] = "text"
    style: Literal["apa", "ieee", "harvard", "vancouver", "mla"] = "apa"
    include_suspicious: bool = True


class PaperAccess(BaseModel):
    level: Literal["open_access", "publisher_page", "none"] = "none"
    open_access_url: Optional[str] = None
    publisher_url: Optional[str] = None
    note: str = ""


class PaperCard(BaseModel):
    record: PaperRecord
    verdict: Verdict = "inconclusive"
    confidence: Optional[int] = None
    reason: str = ""
    sources: List[SourceResult] = Field(default_factory=list)
    access: PaperAccess = Field(default_factory=PaperAccess)
    retraction: RetractionInfo = Field(default_factory=RetractionInfo)


class LookupResponse(BaseModel):
    query: str
    query_type: Literal["doi", "openalex_id", "title"]
    paper: Optional[PaperCard] = None
    candidates: List[PaperRecord] = Field(default_factory=list)
    message: str = ""


class SearchResponse(BaseModel):
    query: str
    page: int
    per_page: int
    total: Optional[int] = None
    items: List[PaperRecord]
    warnings: List[str] = Field(default_factory=list)


class NetworkResponse(BaseModel):
    kind: Literal["references", "cited_by", "related"]
    openalex_id: str
    page: int
    per_page: int
    total: int
    items: List[PaperRecord]


class LogEntry(BaseModel):
    seq: int
    timestamp: str
    citation_index: Optional[int] = None
    source: str
    query: str
    outcome: str
    verdict: Optional[str] = None
    prev_hash: str
    entry_hash: str


class LogResponse(BaseModel):
    job_id: str
    entries: List[LogEntry]
    chain_valid: bool
    final_hash: Optional[str] = None
    first_invalid_seq: Optional[int] = None
