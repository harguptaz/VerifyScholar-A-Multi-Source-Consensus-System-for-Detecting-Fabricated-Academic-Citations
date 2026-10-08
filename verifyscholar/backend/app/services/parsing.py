"""Turn raw citation strings into ParsedCitation objects.

Primary parser: GROBID's processCitationList (handles APA, MLA, IEEE, Vancouver, Harvard ...).
Fallback: the regex heuristics below, used when GROBID is not reachable. The DOI regex always runs
as a safety net.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ..schemas import Author, ParsedCitation
from ..utils.text import YEAR_RE, arxiv_doi, clean_doi, extract_arxiv_id, extract_doi, is_preprint_venue, normalize, squash

_QUOTED = re.compile(r"[\u201c\"]([^\u201d\"]{8,}?)[\u201d\"]|(?:(?<=\s)|^)[\u2018']([^\u2019']{8,}?)[\u2019'](?=[\s,.;])")
_APA = re.compile(r"^(?P<authors>.+?)\(\s*(?P<year>(?:1[5-9]|20)\d{2})[a-z]?\s*\)\s*[.,]?\s*(?P<rest>.+)$")
_VANCOUVER_TAIL = re.compile(r"(?P<year>(?:1[5-9]|20)\d{2})\s*[A-Za-z]{0,3}\s*;\s*(?P<vol>\d+)\s*(?:\((?P<iss>[\w\-]+)\))?\s*:\s*(?P<pages>[\w\-\u2013]+)")
_VOL = re.compile(r"\b(?:vol(?:ume)?\.?\s*)(\d+)", re.IGNORECASE)
_ISS = re.compile(r"\b(?:no\.?|issue|iss\.?)\s*(\d+[\w\-]*)", re.IGNORECASE)
_PAGES = re.compile(r"\b(?:pp?\.?\s*)?(\d{1,6}\s*[-\u2013\u2014]\s*\d{1,6})\b")
_VOL_ISS = re.compile(r",\s*(\d{1,4})\s*\(\s*([\w\-]+)\s*\)")
_INITIALS = r"(?:[A-Z]\.?\s*){1,4}"


# a year that is not part of a longer number, a page range (1600-1650) or an arXiv id (1607.06450)
_YEAR_TOKEN = re.compile(r"(?<![\d\-\u2013\u2014.:/])((?:1[5-9]|20)\d{2})(?![\d\-\u2013\u2014]|\.\d)")


def pick_year(work: str) -> Optional[int]:
    """Prefer a year in parentheses; otherwise the last standalone year-like number."""
    m = re.search(r"\(\s*((?:1[5-9]|20)\d{2})[a-z]?\s*\)", work)
    if m:
        return int(m.group(1))
    found = [int(y) for y in _YEAR_TOKEN.findall(work)]
    return found[-1] if found else None


def _strip_ids(text: str) -> str:
    text = re.sub(r"arxiv\s*:?\s*(?:abs/)?\d{4}\.\d{4,5}(?:v\d+)?|\babs/\d{4}\.\d{4,5}(?:v\d+)?", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)\S+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"10\.\d{4,9}/\S+", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    return squash(text)


_SURNAME = r"[A-Z][\w'\u2019\-]+(?:\s(?:van|von|de|der|da|di|del|la|le|bin|al|el)?\s?[A-Z][\w'\u2019\-]+)?"
_RE_IEEE = re.compile(rf"^{_INITIALS}\s*({_SURNAME})$")
_RE_INITIALS_ONLY = re.compile(rf"^{_INITIALS}$")
_RE_VANCOUVER = re.compile(rf"^({_SURNAME})\s+([A-Z]{{1,3}})$")
_RE_FULLNAME = re.compile(rf"^((?:[A-Z][\w'\u2019\-]*\.?\s+)+)({_SURNAME})$")


def parse_authors(text: str) -> List[Author]:
    """Best-effort author extraction from the author segment of a reference.

    Recognises IEEE ("R. Kumar, A. Sharma"), APA/Harvard ("Kumar, R., Sharma, A."), Vancouver
    ("Kumar R, Sharma A") and MLA ("Kumar, Rahul, and Priya Singh").
    """
    t = squash(text).strip(" ,.;")
    if not t:
        return []
    t = re.sub(r"\bet\s+al\.?", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*(?:,\s*)?(?:and|&)\s+", ", ", t)
    chunks = [c.strip() for c in t.split(",") if c.strip()]
    if not chunks:
        return []

    # IEEE: every chunk is "I. Surname"
    if all(_RE_IEEE.match(c) for c in chunks):
        out = []
        for c in chunks:
            m = _RE_IEEE.match(c)
            fam = m.group(1)
            given = c[: -len(fam)].strip()
            out.append(Author(family=fam, given=given, name=c))
        return out

    # APA / Harvard: "Surname, I." pairs
    if len(chunks) >= 2 and len(chunks) % 2 == 0 and all(_RE_INITIALS_ONLY.match(chunks[i]) for i in range(1, len(chunks), 2)):
        return [Author(family=chunks[i], given=chunks[i + 1].strip(" ."), name=f"{chunks[i]}, {chunks[i + 1]}") for i in range(0, len(chunks), 2)]

    # Vancouver: every chunk is "Surname II"
    if all(_RE_VANCOUVER.match(c) for c in chunks):
        return [Author(family=_RE_VANCOUVER.match(c).group(1), given=_RE_VANCOUVER.match(c).group(2), name=c) for c in chunks]

    # "Given Family" lists without initials: "Ankur Parikh, Oscar Täckström, and Jakob Uszkoreit"
    if len(chunks) >= 2 and all(
        len(c.split()) >= 2 and not _RE_INITIALS_ONLY.match(c) and not re.fullmatch(r"[A-Z]{1,3}\.?", c.split()[-1]) for c in chunks
    ):  # (a trailing initial, as in "van der Berg J", is Vancouver style)
        return [Author(family=c.split()[-1], given=" ".join(c.split()[:-1]), name=c) for c in chunks]

    # MLA: "Surname, Given" first, remaining authors as "Given Surname"
    if len(chunks) >= 2 and re.match(rf"^{_SURNAME}$", chunks[0]) and re.match(r"^[A-Z][a-z\u00c0-\u024f'\-]+(?:\s[A-Z][\w\.]*)*$", chunks[1]):
        authors = [Author(family=chunks[0], given=chunks[1], name=f"{chunks[0]}, {chunks[1]}")]
        for c in chunks[2:]:
            m = _RE_FULLNAME.match(c + "")
            if m:
                authors.append(Author(family=m.group(2), given=m.group(1).strip(), name=c))
            else:
                toks = c.split()
                authors.append(Author(family=toks[-1], given=" ".join(toks[:-1]), name=c))
        return authors

    # fall back: last token of every chunk is the surname
    authors = []
    for c in chunks:
        toks = c.split()
        if not toks:
            continue
        if len(toks) >= 2 and re.fullmatch(r"[A-Z]{1,3}\.?", toks[-1]):  # "van der Berg J"
            authors.append(Author(family=" ".join(toks[:-1]), given=toks[-1].strip("."), name=c))
        else:
            authors.append(Author(family=toks[-1].strip("."), given=" ".join(toks[:-1]), name=c))
    return authors[:12]


def _venue(after: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """journal, volume, issue, pages from the tail of a reference."""
    after = squash(after).strip(" ,.;")
    pages = None
    pm = _PAGES.search(after)
    if pm:
        pages = re.sub(r"\s", "", pm.group(1)).replace("\u2013", "-").replace("\u2014", "-")
    volume = issue = None
    vm = _VOL.search(after)
    if vm:
        volume = vm.group(1)
    im = _ISS.search(after)
    if im:
        issue = im.group(1)
    if not volume:
        vi = _VOL_ISS.search(", " + after)
        if vi:
            volume, issue = vi.group(1), issue or vi.group(2)
    # journal = text before the first comma that is followed by volume/pages/year info
    journal = re.split(r",\s*(?:vol|no|pp?|\d)", after, maxsplit=1, flags=re.IGNORECASE)[0]
    journal = re.sub(r"\s*\(\s*\d{4}\s*\)\s*$", "", journal).strip(" ,.;")
    journal = re.sub(r"^(?:in|In)\s*:?\s*", "", journal).strip()
    if len(journal) < 3 or journal.replace(".", "").replace(" ", "").isdigit():
        journal = None
    return journal, volume, issue, pages


def heuristic_parse(raw: str, index: int) -> ParsedCitation:
    text = squash(raw)
    doi = extract_doi(text)
    work = _strip_ids(text)
    year = pick_year(work)

    title = journal = volume = issue = pages = None
    authors: List[Author] = []

    qm = _QUOTED.search(work)
    vt = _VANCOUVER_TAIL.search(work)
    am = _APA.match(work)

    if qm:  # IEEE / MLA / Harvard: title in quotes
        title = squash(qm.group(1) or qm.group(2)).strip(" .,;")
        before = work[: qm.start()]
        before = re.sub(r"\(\s*(?:1[5-9]|20)\d{2}[a-z]?\s*\)", "", before)
        authors = parse_authors(before)
        journal, volume, issue, pages = _venue(work[qm.end():])
    elif vt and not am:  # Vancouver: Authors. Title. Journal. 2020;17(3):1-10.
        head = work[: vt.start()]
        parts = [p.strip() for p in re.split(r"\.\s+", head) if p.strip()]
        if len(parts) >= 3:
            authors = parse_authors(parts[0])
            title = parts[1].strip(" .")
            journal = parts[2].strip(" .")
        elif len(parts) == 2:
            authors, title = parse_authors(parts[0]), parts[1].strip(" .")
        year, volume, issue, pages = int(vt.group("year")), vt.group("vol"), vt.group("iss"), vt.group("pages").replace("\u2013", "-")
    elif am:  # APA / Harvard (no quotes): Authors (Year). Title. Journal, vol(issue), pages.
        authors = parse_authors(am.group("authors"))
        year = int(am.group("year"))
        rest = am.group("rest").strip()
        parts = re.split(r"(?<=[a-z0-9?\)])\.\s+(?=[A-Z])", rest, maxsplit=1)
        title = parts[0].strip(" .")
        if len(parts) > 1:
            journal, volume, issue, pages = _venue(parts[1])
    else:  # last resort: authors up to the first period-delimited segment containing a year
        parts = [p.strip() for p in re.split(r"(?<=[.?!])\s+", work) if p.strip()]  # titles may end in "?" or "!"
        if len(parts) >= 3:
            authors = parse_authors(parts[0])
            title = parts[1].rstrip(".").strip()
            journal, volume, issue, pages = _venue(" ".join(parts[2:]))

    return finalize(ParsedCitation(
        index=index, raw=text, doi=doi, title=title or None, authors=authors, journal=journal,
        year=year, volume=volume, issue=issue, pages=pages, parse_method="heuristic",
    ))


# ---------------------------------------------------------------------------------------------
# Post-processing shared by every parser: arXiv ids, kind of work (article / preprint / book)
# ---------------------------------------------------------------------------------------------
_EDITION_IN_TITLE = re.compile(r"\s*[(\[]\s*(?:\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|revised|rev\.?)\s+ed(?:ition|\.)?\s*[)\]]\.?", re.IGNORECASE)
_EDITION_IN_RAW = re.compile(r"[(\[]\s*(?:\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|revised|rev\.?)\s+ed(?:ition|\.)?\s*[)\]]|\b\d+(?:st|nd|rd|th)\s+ed\b", re.IGNORECASE)
_PUBLISHER_WORDS = re.compile(r"\b(press|publishing|publishers?|books|media|university press)\b")
KNOWN_PUBLISHERS = {
    "wiley", "john wiley sons", "wiley blackwell", "blackwell", "springer", "springer nature", "elsevier", "pearson", "mcgraw hill",
    "routledge", "sage", "sage publications", "taylor francis", "academic press", "crc press", "cengage", "palgrave macmillan", "macmillan",
    "de gruyter", "addison wesley", "morgan kaufmann", "packt", "apress", "manning", "emerald", "o reilly", "o reilly media", "penguin",
    "harpercollins", "hachette", "jones bartlett", "oxford university press", "cambridge university press", "mit press",
}

# Venues that are conferences or books, not journals. ("Proceedings" alone is not a cue: PNAS and Proc. R. Soc. are journals.)
_CONFERENCE_VENUE = re.compile(
    r"\b(?:conference|conf|workshop|symposium|annual meeting|advances in neural information processing systems|"
    r"empirical methods in natural language processing|findings of|neurips|nips|icml|iclr|cvpr|iccv|eccv|emnlp|naacl|aaai|ijcai|sigir|interspeech|icassp)\b",
    re.IGNORECASE,
)
_IN_VENUE = re.compile(r"[.,?!]\s+In\s+(?:[A-Z]|the\s)")  # "... Title. In Proceedings of ..." / "... In Advances in ..."

_PLACEHOLDER = [
    re.compile(r"\bauthor,\s*[a-z]\.\s*[a-z]\.", re.IGNORECASE),
    re.compile(r"\b(?:surname|last\s?name|family name),\s*[a-z]\.", re.IGNORECASE),
    re.compile(r"\btitle of (?:the |an? )?(?:article|chapter|book|periodical|journal|work|paper|webpage|website|report)\b", re.IGNORECASE),
    re.compile(r"\b(?:publisher|journal|periodical) name\b", re.IGNORECASE),
    re.compile(r"\byour (?:name|title)\b", re.IGNORECASE),
]


def looks_like_template(text: str) -> bool:
    """Placeholder text copied from a citation-style guide ("Author, A. A.", "Title of article" ...)."""
    return any(p.search(text or "") for p in _PLACEHOLDER)


def _is_publisher(name: str) -> bool:
    n = normalize(name)
    return n in KNOWN_PUBLISHERS or bool(_PUBLISHER_WORDS.search(n))


def finalize(p: ParsedCitation) -> ParsedCitation:
    """Derive arXiv id / DOI, and decide whether this is an article, a preprint or a book."""
    p.arxiv_id = p.arxiv_id or extract_arxiv_id(p.raw) or extract_arxiv_id(p.doi) or extract_arxiv_id(p.journal)
    if p.arxiv_id and not p.doi:
        p.doi = arxiv_doi(p.arxiv_id)  # arXiv papers have a DOI (via DataCite); OpenAlex indexes them by it

    has_article_markers = bool(p.volume or p.pages)
    if p.arxiv_id or is_preprint_venue(p.journal):
        p.kind = "preprint"
        return p

    if (p.journal and _CONFERENCE_VENUE.search(p.journal)) or _IN_VENUE.search(p.raw):
        p.kind = "conference"
        return p

    edition = bool(_EDITION_IN_RAW.search(p.raw)) or bool(p.title and _EDITION_IN_TITLE.search(p.title))
    publisher_in_journal_slot = bool(p.journal and _is_publisher(p.journal) and not has_article_markers)
    if publisher_in_journal_slot or (edition and not has_article_markers):
        p.kind = "book"
        if publisher_in_journal_slot:
            p.publisher, p.journal = p.journal, None
        if p.title:
            p.title = _EDITION_IN_TITLE.sub("", p.title).strip(" .") or p.title
        return p

    p.kind = "article" if (p.journal or has_article_markers) else "unknown"
    return p
