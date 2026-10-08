"""Field-by-field comparison between what a citation says and what a source record says (F10)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz

from ..schemas import FieldMatch, ParsedCitation, PaperRecord
from ..utils.text import arxiv_year, clean_doi, first_int, is_preprint_venue, normalize, partial_in, similarity, surname_key

STOPWORDS = {"of", "and", "the", "on", "in", "for", "a", "an", "&"}


@dataclass
class Comparison:
    fields: Dict[str, FieldMatch] = field(default_factory=dict)
    title_sim: float = 0.0
    author_sim: Optional[float] = None
    mismatched: List[str] = field(default_factory=list)


def _title_prefix(a: str, b: str) -> bool:
    """One title is the beginning of the other ("A decomposable attention model" / "... for Natural Language Inference")."""
    ta, tb = normalize(a).split(), normalize(b).split()
    if len(ta) > len(tb):
        ta, tb = tb, ta
    return len(ta) >= 3 and len(ta) < len(tb) and tb[: len(ta)] == ta


def _status(sim: float, hi: float, lo: float) -> str:
    return "match" if sim >= hi else ("partial" if sim >= lo else "mismatch")


CONFERENCE_WORDS = {"proceedings", "proc", "conference", "conf", "workshop", "symposium"}
_VENUE_FILLER = CONFERENCE_WORDS | {"international", "annual", "in", "of", "the", "on", "and", "for", "a", "an"}


def _venue_tokens(name: str, strip_conference_words: bool) -> List[str]:
    name = re.sub(r"\([^)]*\)", " ", name)  # "(CVPR)" acronyms
    out = []
    for t in normalize(name).split():
        if t in STOPWORDS or re.fullmatch(r"\d{2,4}(?:st|nd|rd|th)?", t):
            continue
        if strip_conference_words and t in _VENUE_FILLER:
            continue
        out.append(t)
    return out


def abbreviation_similarity(cited: str, full: str, force_conference: bool = False) -> float:
    """'IEEE Trans. Neural Netw. Learn. Syst.' vs 'IEEE Transactions on Neural Networks and Learning Systems'.

    If the cited venue is a conference ("In Proc. IEEE Conf. ..."), words like Proceedings/Conference are ignored on both sides.
    """
    conf = force_conference or bool(set(normalize(cited).split()) & CONFERENCE_WORDS)
    c = _venue_tokens(cited, conf)
    f = _venue_tokens(full, conf)
    if not c or not f:
        return 0.0
    j = matched = 0
    for tok in c:
        # a real abbreviation drops at least two letters (Trans -> Transactions); 'Test' vs 'Tests' is a different name
        while j < len(f) and not (f[j] == tok or (len(tok) >= 2 and f[j].startswith(tok) and len(f[j]) - len(tok) >= 2)):
            j += 1
        if j >= len(f):
            break
        matched += 1
        j += 1
    coverage = min(1.0, len(c) / len(f))
    return round((matched / len(c)) * coverage, 4)


def _venue_core(name: str) -> set:
    return {t for t in normalize(name).split() if t not in _VENUE_FILLER and not re.fullmatch(r"\d{2,4}(?:st|nd|rd|th)?", t)}


def venue_containment(a: str, b: str, force: bool = False) -> float:
    """Conference names are written many ways ("In Proc. IEEE CVPR" / "2016 IEEE Conference on Computer Vision ... (CVPR)").
    When one of them is a conference, compare the core words: all words of the shorter name must appear in the longer."""
    cited_conf = force or bool(set(normalize(a).split()) & CONFERENCE_WORDS)
    record_conf = bool(set(normalize(b).split()) & CONFERENCE_WORDS)
    if not (cited_conf or record_conf):
        return 0.0
    ca, cb = _venue_core(a), _venue_core(b)
    small, big = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    # a bare two-word name ("Machine Learning") is too ambiguous to equate with a conference unless the cited name itself says so
    if len(small) < (2 if cited_conf else 3):
        return 0.0
    return len(small & big) / len(small)


def journal_similarity(cited: Optional[str], record: PaperRecord, conference: bool = False) -> Tuple[float, Optional[str]]:
    best, best_name = 0.0, None
    names = [n for n in ([record.journal] + list(record.journal_aliases)) if n]
    for name in names:
        s = max(similarity(cited, name), abbreviation_similarity(cited or "", name, conference))
        if venue_containment(cited or "", name, force=conference) >= 0.99:
            s = max(s, 0.9)
        if s > best:
            best, best_name = s, name
    return best, best_name


def _family_match(a: str, b: str) -> bool:
    ka, kb = surname_key(a), surname_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if min(len(ka), len(kb)) >= 3 and (ka.endswith(kb) or kb.endswith(ka)):
        return True
    return fuzz.ratio(ka, kb) >= 88


def author_overlap(cited_families: List[str], record_families: List[str]) -> Tuple[float, bool]:
    if not cited_families or not record_families:
        return 0.0, False
    matched = sum(1 for c in cited_families if any(_family_match(c, r) for r in record_families))
    first_ok = any(_family_match(cited_families[0], r) for r in record_families)
    return matched / len(cited_families), first_ok


def _pages_equal(a: str, b: str) -> bool:
    fa, fb = first_int(a), first_int(b)
    if fa is not None and fb is not None:
        return fa == fb
    return normalize(a) == normalize(b)


def compare(citation: ParsedCitation, record: PaperRecord, matched_by: str = "search") -> Comparison:
    cmp = Comparison()
    f = cmp.fields

    # ---- title ---------------------------------------------------------------------------
    if record.title:
        if citation.title:
            sim = similarity(citation.title, record.title)
            if _title_prefix(citation.title, record.title):
                sim = max(sim, 0.9)  # references often drop the subtitle or the end of a long title
            f["title"] = FieldMatch(submitted=citation.title, found=record.title, similarity=sim, status=_status(sim, 0.9, 0.7))  # type: ignore[arg-type]
            cmp.title_sim = sim
        else:
            sim = partial_in(record.title, citation.raw)
            if sim:
                f["title"] = FieldMatch(submitted=None, found=record.title, similarity=sim, status=_status(sim, 0.9, 0.7))  # type: ignore[arg-type]
                cmp.title_sim = sim
    if "title" not in f:
        f["title"] = FieldMatch(submitted=citation.title, found=record.title, status="missing")

    # ---- authors -------------------------------------------------------------------------
    rec_fams = [a.family or a.name for a in record.authors if (a.family or a.name)]
    cited_fams = [a.family or a.name for a in citation.authors if (a.family or a.name)]
    rec_disp = "; ".join(a.display() for a in record.authors[:6]) + (" …" if len(record.authors) > 6 else "")
    cited_disp = "; ".join(a.display() for a in citation.authors[:6]) if citation.authors else None
    if cited_fams and rec_fams:
        overlap, first_ok = author_overlap(cited_fams, rec_fams)
        st = "match" if (overlap >= 0.8 and first_ok) else ("partial" if overlap >= 0.4 else "mismatch")
        f["authors"] = FieldMatch(submitted=cited_disp, found=rec_disp, similarity=round(overlap, 3), status=st)  # type: ignore[arg-type]
        cmp.author_sim = overlap
    elif rec_fams and not cited_fams:
        hay = normalize(citation.raw).replace(" ", "")
        hit = surname_key(rec_fams[0]) in hay if surname_key(rec_fams[0]) else False
        f["authors"] = FieldMatch(submitted=None, found=rec_disp, similarity=1.0 if hit else 0.0, status="match" if hit else "missing")
        cmp.author_sim = 1.0 if hit else None
    else:
        f["authors"] = FieldMatch(submitted=cited_disp, found=rec_disp or None, status="missing")

    # ---- journal -------------------------------------------------------------------------
    if citation.journal and is_preprint_venue(citation.journal):
        # "arXiv preprint arXiv:1607.06450", "CoRR abs/1409.0473" name a server, not a journal: nothing to compare
        f["journal"] = FieldMatch(submitted=citation.journal, found=record.journal, status="missing")
    elif citation.journal and (record.journal or record.journal_aliases):
        sim, name = journal_similarity(citation.journal, record, conference=citation.kind == "conference")
        f["journal"] = FieldMatch(submitted=citation.journal, found=name, similarity=round(sim, 3), status=_status(sim, 0.85, 0.6))  # type: ignore[arg-type]
    else:
        f["journal"] = FieldMatch(submitted=citation.journal, found=record.journal, status="missing")

    # ---- year ----------------------------------------------------------------------------
    if citation.year and record.year:
        years = {record.year, *record.alt_years}
        if citation.year in years:
            st, sim = "match", 1.0
        elif any(abs(citation.year - y) == 1 for y in years):
            st, sim = "partial", 0.5
        else:
            st, sim = "mismatch", 0.0
        if st == "mismatch" and citation.arxiv_id and arxiv_year(citation.arxiv_id) == citation.year:
            st, sim = "partial", 0.5  # the cited year is the arXiv submission year; sources often list a later version/venue year
        f["year"] = FieldMatch(submitted=str(citation.year), found=str(record.year), similarity=sim, status=st)  # type: ignore[arg-type]
    else:
        f["year"] = FieldMatch(submitted=str(citation.year) if citation.year else None, found=str(record.year) if record.year else None, status="missing")

    # ---- volume / pages ------------------------------------------------------------------
    if citation.volume and record.volume:
        same = normalize(citation.volume) == normalize(record.volume)
        f["volume"] = FieldMatch(submitted=citation.volume, found=record.volume, similarity=1.0 if same else 0.0, status="match" if same else "mismatch")
    else:
        f["volume"] = FieldMatch(submitted=citation.volume, found=record.volume, status="missing")
    if citation.pages and record.pages:
        same = _pages_equal(citation.pages, record.pages)
        f["pages"] = FieldMatch(submitted=citation.pages, found=record.pages, similarity=1.0 if same else 0.0, status="match" if same else "mismatch")
    else:
        f["pages"] = FieldMatch(submitted=citation.pages, found=record.pages, status="missing")

    # ---- DOI -----------------------------------------------------------------------------
    if citation.doi:
        if matched_by == "doi":
            f["doi"] = FieldMatch(submitted=citation.doi, found=record.doi or citation.doi, similarity=1.0, status="match")
        elif record.doi:
            same = clean_doi(citation.doi) == clean_doi(record.doi)
            f["doi"] = FieldMatch(submitted=citation.doi, found=record.doi, similarity=1.0 if same else 0.0, status="match" if same else "mismatch")
        else:
            f["doi"] = FieldMatch(submitted=citation.doi, found=None, status="missing")
    else:
        f["doi"] = FieldMatch(submitted=None, found=record.doi, status="missing")

    cmp.mismatched = [k for k, v in f.items() if v.status == "mismatch"]
    return cmp


def rank_candidates(citation: ParsedCitation, records: List[PaperRecord]) -> List[Tuple[float, PaperRecord, Comparison]]:
    """Order search candidates by how well they match the citation (best first)."""
    ranked = []
    for rec in records:
        cmp = compare(citation, rec, matched_by="search")
        author = cmp.author_sim if cmp.author_sim is not None else 0.5
        year_f = cmp.fields["year"]
        year = 1.0 if year_f.status == "match" else (0.5 if year_f.status in ("partial", "missing") else 0.0)
        ranked.append((0.6 * cmp.title_sim + 0.25 * author + 0.15 * year, rec, cmp))
    ranked.sort(key=lambda t: t[0], reverse=True)
    return ranked


def content_tokens(title: Optional[str]) -> List[str]:
    return [t for t in normalize(title or "").split() if t not in STOPWORDS and len(t) > 2]


def accept_candidate(citation: ParsedCitation, cmp: Comparison) -> Tuple[bool, str]:
    """Is a search result really the cited paper, or just a paper with a similar title?

    * A title that does not agree (similarity under 0.7) is only acceptable if the authors AND the journal or year agree:
      the AI may have garbled the title of a real paper, but unrelated papers share words like "quick guide".
    * An approximate title needs at least some agreement on authors, journal or year.
    * Short, generic titles ("Layer normalization") match many unrelated papers: they need a near-exact title and
      corroboration from the authors, journal or year.
    * A candidate that disagrees on authors, journal AND year is a different paper.
    * For books the title must match closely.
    """
    if cmp.title_sim < 0.6:
        return False, "title too different"
    f = cmp.fields
    key = ("authors", "journal", "year")
    corroborated = sum(1 for k in key if f[k].status == "match")
    contradicted = sum(1 for k in key if f[k].status == "mismatch")
    if contradicted >= 3:
        return False, "authors, journal and year all differ: a different paper"
    if cmp.title_sim < 0.7 and not (f["authors"].status == "match" and (f["journal"].status == "match" or f["year"].status == "match")):
        return False, "the title does not match, and the authors and journal/year do not make up for it"
    if cmp.title_sim < 0.9 and corroborated == 0 and f["authors"].status != "partial":
        return False, "approximate title match with no author, journal or year in common"
    if citation.title and len(content_tokens(citation.title)) < 4:
        if cmp.title_sim < 0.9:
            return False, "generic title, only a weak title match"
        if corroborated == 0:
            return False, "generic title and no author, journal or year in common"
    if citation.kind == "book" and cmp.title_sim < 0.85:
        return False, "a book's title must match closely"
    return True, ""
