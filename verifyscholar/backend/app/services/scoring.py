"""Per-source and overall confidence, verdicts and plain-English explanations (F13)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..config import Settings
from ..schemas import FieldMatch, HeuristicFlag, ParsedCitation, PaperRecord, SourceResult
from .adapters.base import SECONDARY_SOURCES

FIELD_WEIGHTS = {"title": 40, "authors": 25, "journal": 15, "year": 10, "doi": 10, "volume": 5, "pages": 5}
SHORT_NAMES = {"crossref": "CrossRef", "openalex": "OpenAlex", "doaj": "DOAJ", "dblp": "DBLP", "datacite": "DataCite", "openlibrary": "Open Library"}

# Set by the journal-coverage check when the cited journal is real but CrossRef indexes very little of it.
# It explains a verdict, it is not a warning, so it never lowers the confidence score.
COVERAGE_CODE = "journal_sparse_coverage"


def _name(r: SourceResult) -> str:
    return SHORT_NAMES.get(r.source, r.label)


FIELD_LABELS = {"title": "title", "authors": "authors", "journal": "journal", "year": "year", "doi": "DOI", "volume": "volume", "pages": "pages"}


def source_score(fields: Dict[str, FieldMatch], *, lookup_only: bool = False, doi_mismatch: bool = False, min_comparable: int = 30) -> int:
    """0-100 score for one record source, from the field comparison."""
    total = 0.0
    got = 0.0
    for name, w in FIELD_WEIGHTS.items():
        fm = fields.get(name)
        if fm is None or fm.status == "missing":
            continue
        total += w
        if fm.status == "match":
            value = 1.0
        elif name in ("title", "authors", "journal") and fm.similarity is not None:
            value = fm.similarity
        else:
            value = {"partial": 0.5, "mismatch": 0.0}.get(fm.status, 0.0)
        got += w * value
    if total == 0:
        return 100 if lookup_only else 50
    score = 100.0 * got / total
    if any(fields[k].status == "mismatch" for k in fields if k in FIELD_WEIGHTS):
        score = min(score, 79.0)  # any hard disagreement means "Suspicious" at best
    if total < min_comparable and not lookup_only:
        score = min(score, 60.0)  # too little evidence to call it verified
    if doi_mismatch:
        score = min(score, 35.0)
    return int(round(max(0.0, min(100.0, score))))


def source_cap(r: SourceResult) -> int:
    """The highest overall score one source's evidence can support (see source_score).

    The bonuses added in `_score` (two sources agree, journal listed in DOAJ) reward corroboration. They must never
    lift a result over a cap that its best source hit, otherwise a hard mismatch could still end up "Verified".
    """
    if r.method == "doi" and r.doi_resolves_to is not None:
        return 35  # the cited DOI points to a different paper
    if any(fm.status == "mismatch" for k, fm in r.fields.items() if k in FIELD_WEIGHTS):
        return 79
    return 100


@dataclass
class Outcome:
    confidence: Optional[int]
    verdict: str
    base: int


def penalty_for(flags: List[HeuristicFlag], s: Settings) -> int:
    per = {"high": s.penalty_high, "medium": s.penalty_medium, "low": s.penalty_low}
    return min(s.penalty_cap, sum(per[f.severity] for f in flags if f.code != COVERAGE_CODE))


BOOK_TYPES = {"book", "monograph", "edited-book", "reference-book", "book-set", "book-series"}


def non_book_record(kind: Optional[str], record_type: Optional[str]) -> bool:
    """A book citation whose match is a journal article is usually a review OF the book, not the book itself."""
    return kind == "book" and bool(record_type) and record_type not in BOOK_TYPES


def not_indexed(sources: List[SourceResult], flags: List[HeuristicFlag]) -> bool:
    """Nothing was found, but the cited journal is real and thinly indexed, so "not found" is weak evidence.

    Any other warning sign (a near-miss journal name, a volume or year the journal cannot have, an unknown first author,
    a template citation ...) keeps the citation a Ghost.
    """
    if not any(f.code == COVERAGE_CODE for f in flags):
        return False
    if any(f.severity in ("medium", "high") or f.code == "author_not_found" for f in flags):
        return False
    return not any(r.kind == "record" and r.status in ("match", "partial") for r in sources)


def score_result(sources: List[SourceResult], flags: List[HeuristicFlag], s: Settings, kind: Optional[str] = None, record_type: Optional[str] = None) -> Outcome:
    out = _score(sources, flags, s, prefer_books=(kind == "book"))
    if kind == "book":
        # CrossRef and OpenAlex cover books poorly, so "not found" is not evidence that a book is fabricated ...
        if out.verdict == "ghost" and not any(f.severity == "high" for f in flags):
            return Outcome(None, "inconclusive", 0)
        # ... and a match that is a journal article (e.g. "Choice Reviews Online") is a review of the book, not the book
        if out.verdict in ("verified", "suspicious") and non_book_record(kind, record_type):
            return Outcome(None, "inconclusive", 0)
    elif out.verdict == "ghost" and not_indexed(sources, flags):
        return Outcome(None, "inconclusive", 0)  # a real journal that CrossRef/OpenAlex barely cover: not found is not "fabricated"
    return out


def _score(sources: List[SourceResult], flags: List[HeuristicFlag], s: Settings, prefer_books: bool = False) -> Outcome:
    record_sources = [r for r in sources if r.kind == "record"]
    # Only CrossRef and OpenAlex can make a citation "inconclusive" by not answering. DBLP, DataCite and Open Library are
    # asked as a second opinion: their silence is not a reason to withhold a verdict, and their "no record" is not evidence.
    required = [r for r in record_sources if r.source not in SECONDARY_SOURCES]
    matched = [r for r in record_sources if r.score is not None and r.status in ("match", "partial")]
    if prefer_books:
        # for a book, a book record outweighs a journal article with a similar title (usually a review of the book)
        books = [r for r in matched if r.record is not None and r.record.type in BOOK_TYPES]
        matched = books or matched
    if required and all(r.status == "unavailable" for r in required) and not matched:
        return Outcome(None, "inconclusive", 0)
    scores = [r.score for r in matched if r.score is not None]
    best = max(matched, key=lambda r: r.score or 0) if matched else None
    base = max(scores) if scores else 0
    if sum(1 for sc in scores if sc >= 70) >= 2:
        base = min(100, base + s.agreement_bonus)
    doaj = next((r for r in sources if r.kind == "journal"), None)
    if doaj and doaj.status == "match" and base >= s.suspicious_threshold:
        base = min(100, base + s.doaj_bonus)
    if best is not None:
        base = min(base, source_cap(best))  # bonuses come first, then the cap of the best source is applied
    conf = max(0, min(100, base - penalty_for(flags, s)))

    if not scores:  # nothing found in any record source
        if any(r.status == "unavailable" for r in required):
            return Outcome(None, "inconclusive", 0)  # cannot call it a ghost if a source did not answer
        return Outcome(conf, "ghost", 0)
    if conf >= s.verified_threshold:
        return Outcome(conf, "verified", base)
    if conf >= s.suspicious_threshold:
        return Outcome(conf, "suspicious", base)
    return Outcome(conf, "ghost", base)


def _fmt_field(name: str, fm: FieldMatch) -> str:
    sub = fm.submitted if fm.submitted else "not given"
    found = f"the record has “{fm.found}”" if fm.found else f"the record has no {FIELD_LABELS[name]}"
    return f"{FIELD_LABELS[name]} (you cited “{sub}”, {found})"


def _join_words(items: List[str], last: str = "and") -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {last} " + items[-1]


def _venue_year(record: PaperRecord) -> str:
    """" (Journal, 2017)" with whatever is known; empty when the record has neither journal nor year."""
    parts = [p for p in (record.journal, str(record.year) if record.year else None) if p]
    return f" ({', '.join(parts)})" if parts else ""


def _brief(record: PaperRecord) -> str:
    """“Title”, Journal, 2017 - only the parts that exist, so a missing journal never shows up as 'None'."""
    parts = [f"“{record.title}”" if record.title else "an untitled record"]
    if record.journal:
        parts.append(record.journal)
    if record.year:
        parts.append(str(record.year))
    return ", ".join(parts)


def build_reason(
    parsed: ParsedCitation,
    verdict: str,
    sources: List[SourceResult],
    flags: List[HeuristicFlag],
    record: Optional[PaperRecord],
    doi_status: str,
    best: Optional[SourceResult],
) -> str:
    record_sources = [r for r in sources if r.kind == "record"]
    found_in = [_name(r) for r in record_sources if r.status in ("match", "partial")]
    flag_text = " ".join(f"{f.message}." if not f.message.endswith(".") else f.message for f in flags if f.code != COVERAGE_CODE)

    if verdict == "inconclusive":
        down = [_name(r) + (f" ({r.note[:60]})" if r.note else "") for r in record_sources if r.status == "unavailable"]
        if not down and record is not None and non_book_record(parsed.kind, record.type):
            return (
                f"A record with the same title was found (“{record.title}”, {record.journal or 'unknown venue'}, {record.year or 'n.d.'}), but it is a "
                f"{record.type}, which for a book is usually a review of the book rather than the book itself. That suggests the book exists, but "
                "VerifyScholar cannot check a book's details, so no verdict is given. Check the publisher's catalogue, WorldCat or Google Books."
            )
        if not down and parsed.kind == "book" and any(r.source == "openlibrary" for r in record_sources):
            who = f" (publisher: {parsed.publisher})" if parsed.publisher else ""
            searched = _join_words([_name(r) for r in record_sources])
            return (
                f"This looks like a book{who}. VerifyScholar searched {searched} and found no matching book. No catalogue lists every book "
                "(older, regional and small-press titles are often missing), so this does not mean the book is fake, and no verdict is given. "
                "Check the publisher's catalogue, WorldCat or Google Books."
            )
        if not down and parsed.kind == "book":
            who = f" (publisher: {parsed.publisher})" if parsed.publisher else ""
            return (
                f"This looks like a book{who}. VerifyScholar is designed for journal articles and conference papers; CrossRef and OpenAlex "
                "cover books poorly, so “not found” does not mean the book is fake, and no verdict can be given. "
                "Check the publisher's catalogue, WorldCat or Google Books."
            )
        coverage = next((f for f in flags if f.code == COVERAGE_CODE), None)
        if not down and coverage is not None:
            where = _join_words([_name(r) for r in record_sources], "or") or "the sources"
            return (
                f"Not indexed: no paper matching this citation was found in {where}, but the journal itself appears to be real. {coverage.message}. "
                "Genuine papers from thinly indexed journals are often missing from these databases, so no verdict is given "
                "(this is not evidence that the citation is fake). Check the journal's own website, Google Scholar or the publisher's page."
            )
        return (
            f"Verification could not be completed because {', '.join(down) or 'a data source'} did not respond. "
            "This is not evidence that the citation is fake. Please try again in a moment."
        )

    if verdict == "verified" and record:
        agree = [FIELD_LABELS[k] for k, v in (best.fields if best else {}).items() if v.status == "match" and k != "doi"]
        where = _join_words(found_in) if found_in else "the sources"
        txt = f"Matched to “{record.title}”{_venue_year(record)} in {where}."
        if agree:
            txt += f" The {_join_words(agree)} {'agrees' if len(agree) == 1 else 'agree'} with your citation."
        year = best.fields.get("year") if best else None
        if year is not None and year.status == "partial" and year.submitted and year.found:
            txt += f" The year differs slightly (you cited {year.submitted}, the record says {year.found}); records often carry the date of a later version or re-registration."
        return (txt + " " + flag_text).strip()

    if verdict == "suspicious" and record:
        if doi_status == "mismatch" and best and best.doi_resolves_to:
            other = best.doi_resolves_to
            txt = (
                f"A paper matching your title exists (“{record.title}”, DOI {record.doi or 'n/a'}), but the DOI you cited "
                f"({parsed.doi}) points to a different paper: “{other.title}”."
            )
            return (txt + " " + flag_text).strip()
        diffs = [_fmt_field(k, v) for k, v in (best.fields if best else {}).items() if v.status == "mismatch"]
        partial = [FIELD_LABELS[k] for k, v in (best.fields if best else {}).items() if v.status == "partial"]
        txt = f"A real paper was found ({_brief(record)}) but your citation differs from it"
        if diffs:
            txt += ": " + "; ".join(diffs) + "."
        elif partial:
            txt += f" only approximately ({', '.join(partial)} are close but not exact)."
        else:
            txt += "."
        txt += " The AI may have cited the right paper with wrong details; review the comparison and use the corrected citation if appropriate."
        return (txt + " " + flag_text).strip()

    # ghost
    if doi_status == "mismatch" and best and best.doi_resolves_to:
        other = best.doi_resolves_to
        txt = (
            f"The DOI you cited ({parsed.doi}) resolves to a different paper (“{other.title}”), and no paper with your title and authors "
            "was found."
        )
    elif parsed.doi:
        txt = f"The DOI {parsed.doi} could not be found, and no paper matching the cited title and authors was found in {_join_words([_name(r) for r in record_sources], 'or') or 'the sources'}."
    else:
        txt = f"No paper matching this citation was found in {_join_words([_name(r) for r in record_sources], 'or') or 'the sources'}."
    doaj = next((r for r in sources if r.kind == "journal"), None)
    if any(f.code == "template_placeholder" for f in flags):
        txt = "This does not look like a real reference: it contains placeholder text from a citation style guide, and no matching paper was found."
        return (txt + " " + flag_text).strip()
    if parsed.journal and doaj and doaj.status == "no_record":
        txt += " The journal was not found in DOAJ either (DOAJ lists open-access journals only)."
    txt += " This citation may have been fabricated by an AI tool. Genuine papers from older, regional or non-indexed venues can also be missing from these databases, so check manually before deciding. "
    return (txt + flag_text).strip()