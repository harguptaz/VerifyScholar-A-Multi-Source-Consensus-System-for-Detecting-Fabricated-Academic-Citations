"""Small text helpers used across the engine: DOI handling, normalisation, similarity."""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional
from urllib.parse import unquote

from rapidfuzz import fuzz

# A DOI: "10." + registrant (4-9 digits) + "/" + suffix. Trailing punctuation is trimmed afterwards.
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?", re.IGNORECASE)
OPENALEX_ID_RE = re.compile(r"^(?:https?://openalex\.org/)?(W\d+)$", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")

_TRAILING = ".,;:)]}>\"'"


def clean_doi(value: str) -> str:
    value = unquote(value.strip())
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.IGNORECASE)
    value = value.rstrip(_TRAILING)
    # unbalanced closing parenthesis left over from "(doi:10.1/xyz)"
    while value.endswith(")") and value.count("(") < value.count(")"):
        value = value[:-1].rstrip(_TRAILING)
    return value.lower()


def extract_doi(text: str) -> Optional[str]:
    """Find the first DOI inside free text (also inside URLs). Returns a lowercase bare DOI."""
    if not text:
        return None
    m = DOI_RE.search(unquote(text))
    if not m:
        return None
    doi = clean_doi(m.group(0))
    return doi if re.match(r"^10\.\d{4,9}/\S+$", doi) else None


def arxiv_to_doi(text: str) -> Optional[str]:
    m = ARXIV_RE.search(text or "")
    if m:
        return f"10.48550/arxiv.{m.group(1)}"
    return None


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize(s: Optional[str]) -> str:
    """Lowercase, accent-free, punctuation-free, single-spaced."""
    if not s:
        return ""
    s = strip_accents(s).lower()
    s = re.sub(r"[\u2010-\u2015]", "-", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def similarity(a: Optional[str], b: Optional[str]) -> float:
    """0..1 similarity between two strings, tolerant to word order and truncation."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    sort_r = fuzz.token_sort_ratio(na, nb)
    set_r = fuzz.token_set_ratio(na, nb)
    # token_set_ratio is 100 whenever one string's tokens are a subset of the other's, so only
    # trust it when the shorter string is a substantial part of the longer one.
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    coverage = len(short) / max(1, len(long_))
    if coverage < 0.5:
        set_r = min(set_r, 60)
    return round((sort_r + set_r) / 200.0, 4)


def partial_in(needle: Optional[str], haystack: Optional[str]) -> float:
    """How well `needle` (e.g. a candidate title) appears inside `haystack` (the raw citation)."""
    n, h = normalize(needle), normalize(haystack)
    if len(n) < 15 or not h:
        return 0.0
    return round(fuzz.partial_ratio(n, h) / 100.0, 4)


def surname_key(family: str) -> str:
    return normalize(family).replace(" ", "")


def first_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    m = re.search(r"\d+", str(value))
    return int(m.group(0)) if m else None


def uniq(items: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def strip_tags(text: str) -> str:
    """Remove XML/HTML tags (CrossRef abstracts are JATS XML)."""
    return squash(re.sub(r"<[^>]+>", " ", text or ""))


# ---------------------------------------------------------------------------------------------
# arXiv identifiers and preprint venues
# ---------------------------------------------------------------------------------------------
_ARXIV_NEW = re.compile(r"(?:arxiv[:\s./]*(?:abs/|pdf/)?|\babs/)(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
_ARXIV_OLD = re.compile(r"arxiv[:\s./]*(?:abs/)?([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.IGNORECASE)
PREPRINT_WORDS = {"arxiv", "preprint", "corr", "biorxiv", "medrxiv", "chemrxiv", "ssrn", "openreview", "eprint", "psyarxiv", "socarxiv"}


def extract_arxiv_id(text: Optional[str]) -> Optional[str]:
    """arXiv id from 'arXiv:1607.06450', 'abs/1607.06450', 'arxiv.org/abs/1607.06450v2' ..."""
    if not text:
        return None
    m = _ARXIV_NEW.search(text) or _ARXIV_OLD.search(text)
    return m.group(1).lower() if m else None


def arxiv_doi(arxiv_id: str) -> str:
    """arXiv-issued DOI (registered with DataCite): 1607.06450 -> 10.48550/arxiv.1607.06450."""
    return f"10.48550/arxiv.{arxiv_id.lower()}"


def arxiv_year(arxiv_id: Optional[str]) -> Optional[int]:
    """Submission year implied by a new-style arXiv id (YYMM.NNNNN)."""
    m = re.match(r"^(\d{2})\d{2}\.\d{4,5}$", arxiv_id or "")
    if not m:
        return None
    yy = int(m.group(1))
    return 2000 + yy if yy < 90 else 1900 + yy


def is_preprint_venue(name: Optional[str]) -> bool:
    return bool(name) and any(t in PREPRINT_WORDS for t in normalize(name).split())
