"""Reference formatting and bibliography export (F15, F35, F36).

Reference text can come from doi.org content negotiation (real CSL styles, via CrossRef/DataCite) or from
the local formatters below, which are used as the fallback and are fully deterministic. BibTeX and RIS are
always generated locally from the verified record.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..config import Settings, get_settings
from ..schemas import Author, PaperRecord
from ..utils.text import first_int, normalize, strip_accents
from .http_client import HttpService

STYLES = ("apa", "ieee", "harvard", "vancouver", "mla")
NUMBERED_STYLES = ("ieee", "vancouver")
CSL_STYLE = {"apa": "apa", "ieee": "ieee", "harvard": "harvard-cite-them-right", "vancouver": "vancouver", "mla": "modern-language-association"}


# ------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------
def initials(given: str, dotted: bool = True, spaced: bool = True) -> str:
    parts = [p for p in re.split(r"[\s\-]+", given.replace(".", " ").strip()) if p]
    letters = [p[0].upper() for p in parts]
    if not letters:
        return ""
    if dotted:
        return (" " if spaced else "").join(f"{l}." for l in letters)
    return "".join(letters)


def _pages(pages: Optional[str], dash: str = "–") -> str:
    return re.sub(r"\s*[-–—]\s*", dash, pages) if pages else ""


def _join_and(items: List[str], conj: str = "and", oxford: bool = False) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} {conj} {items[1]}"
    return ", ".join(items[:-1]) + ("," if oxford else "") + f" {conj} {items[-1]}"


def _title_end(title: str) -> str:
    return title if title.endswith(("?", "!", ".")) else title + "."


def _doi_url(r: PaperRecord) -> str:
    return f"https://doi.org/{r.doi}" if r.doi else (r.url or "")


# ------------------------------------------------------------------------------------------
# local formatters
# ------------------------------------------------------------------------------------------
def format_local(r: PaperRecord, style: str) -> str:
    title = (r.title or "Untitled").strip()
    journal = r.journal or ""
    year = str(r.year) if r.year else "n.d."
    vol, iss, pages = r.volume or "", r.issue or "", r.pages or ""
    authors = [a for a in r.authors if a.family or a.name]

    if style == "apa":
        names = [f"{a.family}, {initials(a.given)}".strip(", ") if a.given else a.family or a.name for a in authors]
        if len(names) > 20:
            names = names[:19] + ["…", names[-1]]
        auth = (", ".join(names[:-1]) + ", & " + names[-1]) if len(names) > 2 else " & ".join(names)
        out = f"{auth} ({year}). {_title_end(title)}"
        if journal:
            out += f" {journal}"
            if vol:
                out += f", {vol}" + (f"({iss})" if iss else "")
            if pages:
                out += f", {_pages(pages)}"
            out += "."
        if r.doi:
            out += f" {_doi_url(r)}"
        return out.strip()

    if style == "ieee":
        names = [f"{initials(a.given)} {a.family}".strip() if a.given else a.family or a.name for a in authors]
        auth = "et al." if len(names) > 6 else _join_and(names, "and", oxford=True)
        if len(names) > 6:
            auth = f"{names[0]} et al."
        out = f"{auth}, “{title},”" if auth else f"“{title},”"
        if journal:
            out += f" {journal}"
            if vol:
                out += f", vol. {vol}"
            if iss:
                out += f", no. {iss}"
            if pages:
                out += f", {'pp.' if '-' in pages or '–' in pages else 'Art.'} {_pages(pages)}"
        out += f", {year}"
        if r.doi:
            out += f", doi: {r.doi}"
        return out + "."

    if style == "harvard":
        names = [f"{a.family}, {initials(a.given, spaced=False)}" if a.given else a.family or a.name for a in authors]
        auth = _join_and(names, "and")
        out = f"{auth} ({year}) ‘{title}’"
        if journal:
            out += f", {journal}"
            if vol:
                out += f", {vol}" + (f"({iss})" if iss else "")
            if pages:
                out += f", pp. {_pages(pages)}" if re.search(r"[-–]", pages) else f", p. {pages}"
        out += "."
        if r.doi:
            out += f" doi:{r.doi}."
        return out

    if style == "vancouver":
        names = [f"{a.family} {initials(a.given, dotted=False)}".strip() if a.given else a.family or a.name for a in authors]
        auth = ", ".join(names[:6]) + (", et al" if len(names) > 6 else "")
        out = f"{auth}. {_title_end(title)}" if auth else _title_end(title)
        if journal:
            out += f" {journal}. {year}"
            if vol:
                out += f";{vol}" + (f"({iss})" if iss else "")
            if pages:
                out += f":{_pages(pages, '-')}"
            out += "."
        if r.doi:
            out += f" doi:{r.doi}"
        return out.strip()

    # mla
    if not authors:
        auth = ""
    else:
        first = authors[0]
        first_s = f"{first.family}, {first.given}".strip(", ") if first.given else first.family or first.name
        if len(authors) == 1:
            auth = first_s
        elif len(authors) == 2:
            second = authors[1]
            auth = f"{first_s}, and {(second.given + ' ' + second.family).strip() if second.given else second.family or second.name}"
        else:
            auth = f"{first_s}, et al"
    out = (auth.rstrip(".") + ". " if auth else "") + f"“{_title_end(title)}”"
    if journal:
        out += f" {journal}"
        if vol:
            out += f", vol. {vol}"
        if iss:
            out += f", no. {iss}"
        out += f", {year}"
        if pages:
            out += f", pp. {_pages(pages, '-')}"
        out += "."
    if r.doi:
        out += f" {_doi_url(r)}."
    return out.strip()


# ------------------------------------------------------------------------------------------
# BibTeX / RIS
# ------------------------------------------------------------------------------------------
_BIB_ESC = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_"}


def _bib_escape(s: str) -> str:
    return "".join(_BIB_ESC.get(c, c) for c in s)


def _bib_type(r: PaperRecord) -> str:
    return {"journal-article": "article", "book": "book", "book-chapter": "incollection", "proceedings-article": "inproceedings",
            "posted-content": "misc", "dissertation": "phdthesis"}.get(r.type or "", "article" if r.journal else "misc")


def bibtex_key(r: PaperRecord, used: set[str]) -> str:
    fam = normalize(strip_accents(r.authors[0].family)).replace(" ", "") if r.authors and r.authors[0].family else "anon"
    word = next((w for w in normalize(r.title or "").split() if len(w) > 3 and w not in ("with", "from", "using", "their", "which")), "paper")
    base = f"{fam}{r.year or ''}{word}"
    key, n = base, 0
    while key in used:
        n += 1
        key = base + chr(ord("a") + (n - 1) % 26)
    used.add(key)
    return key


def to_bibtex(r: PaperRecord, key: str) -> str:
    fields = []
    if r.authors:
        fields.append(("author", " and ".join(f"{a.family}, {a.given}" if a.given else (a.family or a.name) for a in r.authors)))
    if r.title:
        fields.append(("title", "{" + r.title + "}"))
    if r.journal:
        fields.append(("journal" if _bib_type(r) == "article" else "booktitle", r.journal))
    if r.year:
        fields.append(("year", str(r.year)))
    if r.volume:
        fields.append(("volume", r.volume))
    if r.issue:
        fields.append(("number", r.issue))
    if r.pages:
        fields.append(("pages", re.sub(r"\s*[-–—]\s*", "--", r.pages)))
    if r.publisher:
        fields.append(("publisher", r.publisher))
    if r.doi:
        fields.append(("doi", r.doi))
    if r.retraction.status == "retracted":
        fields.append(("note", "RETRACTED"))
    body = ",\n".join(f"  {k} = {{{_bib_escape(v) if k not in ('title',) else v}}}" for k, v in fields)
    return f"@{_bib_type(r)}{{{key},\n{body}\n}}"


def to_ris(r: PaperRecord) -> str:
    ty = {"journal-article": "JOUR", "book": "BOOK", "book-chapter": "CHAP", "proceedings-article": "CONF", "posted-content": "UNPB"}.get(r.type or "", "JOUR")
    lines = [f"TY  - {ty}"]
    lines += [f"AU  - {a.family}, {a.given}" if a.given else f"AU  - {a.family or a.name}" for a in r.authors]
    if r.title:
        lines.append(f"TI  - {r.title}")
    if r.journal:
        lines.append(f"JO  - {r.journal}")
    if r.year:
        lines.append(f"PY  - {r.year}")
    if r.volume:
        lines.append(f"VL  - {r.volume}")
    if r.issue:
        lines.append(f"IS  - {r.issue}")
    if r.pages:
        parts = re.split(r"\s*[-–—]\s*", r.pages)
        lines.append(f"SP  - {parts[0]}")
        if len(parts) > 1:
            lines.append(f"EP  - {parts[1]}")
    if r.doi:
        lines.append(f"DO  - {r.doi}")
        lines.append(f"UR  - https://doi.org/{r.doi}")
    if r.abstract:
        lines.append(f"AB  - {r.abstract}")
    if r.retraction.status == "retracted":
        lines.append("N1  - RETRACTED")
    lines.append("ER  - ")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------
# CSL via doi.org content negotiation, with local fallback
# ------------------------------------------------------------------------------------------
async def format_reference(r: PaperRecord, style: str, http: Optional[HttpService], settings: Optional[Settings] = None) -> Tuple[str, str]:
    """Return (formatted reference, 'csl' | 'local')."""
    settings = settings or get_settings()
    if http is not None and settings.use_csl_formatting and r.doi and style in CSL_STYLE:
        try:
            res = await http.get_text(
                f"{settings.doi_resolver_url.rstrip('/')}/{r.doi}",
                headers={"Accept": f"text/x-bibliography; style={CSL_STYLE[style]}; locale=en-US"},
            )
            text = (res.data or "").strip() if res.status == "ok" else ""
            if text and not text.lstrip().startswith("<") and len(text) > 20:
                return re.sub(r"\s+", " ", text), "csl"
        except Exception:
            pass
    return format_local(r, style), "local"


# ------------------------------------------------------------------------------------------
# Bibliography export
# ------------------------------------------------------------------------------------------
@dataclass
class ExportEntry:
    record: PaperRecord
    origin: str  # verified | review | extra
    index: Optional[int] = None  # position in the user's original list
    note: str = ""


@dataclass
class RemovedEntry:
    index: int
    raw: str
    verdict: str
    reason: str


@dataclass
class ExportBundle:
    entries: List[ExportEntry] = field(default_factory=list)
    removed: List[RemovedEntry] = field(default_factory=list)


def _sort_key(e: ExportEntry) -> tuple:
    a = e.record.authors[0].family if e.record.authors else ""
    return (normalize(a) or "zzz", e.record.year or 0)


async def build_export(bundle: ExportBundle, fmt: str, style: str, http: Optional[HttpService], settings: Optional[Settings] = None) -> Tuple[str, str, str]:
    """Return (content, media_type, filename)."""
    entries = list(bundle.entries)
    if style not in NUMBERED_STYLES:
        entries.sort(key=_sort_key)

    if fmt == "bibtex":
        used: set[str] = set()
        blocks = []
        for e in entries:
            if e.origin == "review":
                blocks.append("% REVIEW: details in your original citation differed from the source record; verify before using")
            blocks.append(to_bibtex(e.record, bibtex_key(e.record, used)))
        if bundle.removed:
            blocks.append("% Removed (not verified): " + "; ".join(f"#{r.index}" for r in bundle.removed))
        return "\n\n".join(blocks) + "\n", "application/x-bibtex", "bibliography.bib"

    if fmt == "ris":
        return "\n".join(to_ris(e.record) + "\n" for e in entries), "application/x-research-info-systems", "bibliography.ris"

    formatted: List[Tuple[ExportEntry, str, str]] = []
    for e in entries:
        text, src = await format_reference(e.record, style, http, settings)
        formatted.append((e, text, src))

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["status", "original_index", "reference", "doi", "title", "authors", "journal", "year", "volume", "issue", "pages", "retraction_status", "url"])
        for e, text, _ in formatted:
            r = e.record
            w.writerow([e.origin, e.index or "", text, r.doi or "", r.title or "", "; ".join(a.display() for a in r.authors), r.journal or "", r.year or "", r.volume or "", r.issue or "", r.pages or "", r.retraction.status, r.doi_url or r.url or ""])
        for rem in bundle.removed:
            w.writerow(["removed", rem.index, rem.raw, "", "", "", "", "", "", "", "", "", ""])
        return buf.getvalue(), "text/csv", "bibliography.csv"

    # plain text
    lines: List[str] = []
    for n, (e, text, _) in enumerate(formatted, start=1):
        tag = ""
        if e.origin == "review":
            tag += " [REVIEW: details differ from your original citation]"
        if e.record.retraction.status == "retracted":
            tag += " [RETRACTED]"
        elif e.record.retraction.status == "concern":
            tag += " [EXPRESSION OF CONCERN]"
        prefix = f"[{n}] " if style == "ieee" else (f"{n}. " if style == "vancouver" else "")
        lines.append(f"{prefix}{text}{tag}")
    out = "\n\n".join(lines)
    if bundle.removed:
        out += "\n\n" + "-" * 60 + "\nRemoved from this list (could not be verified)\n" + "-" * 60
        for rem in bundle.removed:
            out += f"\n#{rem.index} [{rem.verdict}] {rem.raw}\n    {rem.reason}\n"
    return out.strip() + "\n", "text/plain", "bibliography.txt"
