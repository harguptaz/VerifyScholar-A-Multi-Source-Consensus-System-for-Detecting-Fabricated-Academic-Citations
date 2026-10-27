"""Application settings, loaded from environment variables / a .env file."""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- identification for the "polite pool" of CrossRef / OpenAlex --------------------
    contact_email: str = Field(default="you@example.com", description="Sent to CrossRef/OpenAlex as mailto")
    openalex_api_key: str = Field(default="", description="Free key from https://openalex.org/settings/api")

    # ---- external services -------------------------------------------------------------
    crossref_base_url: str = "https://api.crossref.org"
    openalex_base_url: str = "https://api.openalex.org"
    doaj_base_url: str = "https://doaj.org/api"
    # secondary sources: free, no key. They are asked only when CrossRef and OpenAlex do not settle a citation.
    dblp_base_url: str = "https://dblp.org"  # computer-science conferences and journals
    datacite_base_url: str = "https://api.datacite.org"  # arXiv, Zenodo, figshare and other DataCite DOIs
    openlibrary_base_url: str = "https://openlibrary.org"  # books
    enable_dblp: bool = True
    enable_datacite: bool = True
    enable_openlibrary: bool = True
    doi_resolver_url: str = "https://doi.org"
    grobid_url: str = "http://localhost:8070"
    grobid_timeout_seconds: int = 180
    use_grobid_for_citations: bool = True  # falls back to the built-in heuristic parser if GROBID is down
    use_csl_formatting: bool = True  # doi.org content negotiation, falls back to local formatters

    # ---- storage -----------------------------------------------------------------------
    database_url: str = "sqlite:///./data/verifyscholar.db"
    cache_ttl_hours: int = 24
    cache_negative_ttl_hours: int = 2

    # ---- HTTP behaviour ----------------------------------------------------------------
    http_timeout_seconds: float = 12.0
    http_max_attempts: int = 3
    http_backoff_min_seconds: float = 0.5
    http_backoff_max_seconds: float = 4.0
    http_max_concurrent_requests: int = 8
    # per-service caps: OpenAlex throttles bursts, so a few citations in parallel can already trigger HTTP 429
    openalex_max_concurrent_requests: int = 3
    crossref_max_concurrent_requests: int = 6
    dblp_max_concurrent_requests: int = 2  # DBLP answers HTTP 429 to bursts
    datacite_max_concurrent_requests: int = 4
    openlibrary_max_concurrent_requests: int = 2
    # citations whose source did not answer are retried once at the end of a job
    retry_unavailable: bool = True
    retry_pause_seconds: float = 3.0
    max_parallel_citations: int = 5
    # time budgets: a slow source is reported as "unavailable" instead of holding the whole citation back
    source_time_budget_seconds: float = 25.0  # CrossRef / OpenAlex, per citation (all requests of that source together)
    side_check_budget_seconds: float = 10.0  # DOAJ, retraction check, fabrication heuristics, journal coverage check
    secondary_time_budget_seconds: float = 12.0  # DBLP / DataCite / Open Library, per citation (the three run side by side)

    # ---- limits ------------------------------------------------------------------------
    max_citations_per_job: int = 200
    max_upload_mb: int = 25

    # ---- scoring thresholds (tune on the evaluation set) --------------------------------
    verified_threshold: int = 80
    suspicious_threshold: int = 40
    penalty_high: int = 25
    penalty_medium: int = 12
    penalty_low: int = 5
    penalty_cap: int = 40
    doaj_bonus: int = 5
    agreement_bonus: int = 5
    # a real journal in which CrossRef holds this few articles for the cited year is "thinly indexed":
    # a paper missing from it is weak evidence, so the citation is "Inconclusive (not indexed)" instead of "Ghost"
    not_indexed_max_year_works: int = 25

    # ---- relevance (Mode C) ------------------------------------------------------------
    embedding_backend: str = "auto"  # auto | sentence-transformers | tfidf
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    sentence_high_st: float = 0.40
    paper_high_st: float = 0.30
    sentence_high_tfidf: float = 0.18
    paper_high_tfidf: float = 0.10

    # ---- web ---------------------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()