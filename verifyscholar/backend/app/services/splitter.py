"""Split a pasted block of text into individual citations."""
from __future__ import annotations

import re
from typing import List

from ..utils.text import DOI_RE, YEAR_RE, squash

_BRACKET = re.compile(r"^\s*\[(\d{1,3})\]\s*")
_NUMBERED = re.compile(r"^\s*(\d{1,3})[.)]\s+(?=\S)")
_PAREN_NUM = re.compile(r"^\s*\((\d{1,3})\)\s+")


def _marker_starts(lines: List[str], pattern: re.Pattern[str]) -> List[int]:
    return [i for i, ln in enumerate(lines) if pattern.match(ln)]


def _split_on_markers(lines: List[str], starts: List[int], pattern: re.Pattern[str]) -> List[str]:
    chunks: List[str] = []
    for n, s in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        chunk = " ".join(l.strip() for l in lines[s:end] if l.strip())
        chunk = pattern.sub("", chunk, count=1)
        chunks.append(squash(chunk))
    return chunks


def _looks_complete(chunk: str) -> bool:
    c = chunk.rstrip()
    if not c:
        return False
    has_year = bool(YEAR_RE.search(c))
    ends_ok = c.endswith((".", ")", "]", '"', "\u201d")) or bool(DOI_RE.search(c[-80:]))
    return has_year and ends_ok


def split_citations(text: str) -> List[str]:
    """Return one string per citation.

    Handles numbered lists ([1] / 1. / (1)), blank-line separated blocks, and one-citation-per-line
    input where long citations are wrapped over several lines.
    """
    if not text or not text.strip():
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = text.split("\n")

    # 1) numbered markers (need at least two to be sure it is a numbered list)
    for pattern in (_BRACKET, _NUMBERED, _PAREN_NUM):
        starts = _marker_starts(lines, pattern)
        if len(starts) >= 2 or (len(starts) == 1 and starts[0] == 0 and len(lines) >= 1 and pattern is _BRACKET):
            return [c for c in _split_on_markers(lines, starts, pattern) if c]

    # 2) blank-line separated blocks
    blocks = [squash(b.replace("\n", " ")) for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) >= 2:
        return blocks

    # 3) line based: a new citation starts on an un-indented line once the previous one looks complete
    chunks: List[str] = []
    current = ""
    for ln in lines:
        if not ln.strip():
            continue
        is_indented = ln.startswith((" ", "\t"))
        if current and not is_indented and _looks_complete(current):
            chunks.append(squash(current))
            current = ln.strip()
        else:
            current = f"{current} {ln.strip()}".strip()
    if current:
        chunks.append(squash(current))
    return chunks


def count_citations(text: str) -> int:
    return len(split_citations(text))


# -------------------------------------------------------------------------------------
# BibTeX / RIS import (F5)
# -------------------------------------------------------------------------------------
def _bib_entries(text: str) -> List[dict]:
    entries: List[dict] = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]*)\s*,", text):
        etype = m.group(1).lower()
        if etype in ("comment", "string", "preamble"):
            continue
        i, depth = m.end(), 1
        start = i
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[start : i - 1]
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*", body):
            name, j = fm.group(1).lower(), fm.end()
            if j >= len(body):
                continue
            if body[j] == "{":
                d, k = 1, j + 1
                while k < len(body) and d > 0:
                    d += (body[k] == "{") - (body[k] == "}")
                    k += 1
                value = body[j + 1 : k - 1]
            elif body[j] == '"':
                k = body.find('"', j + 1)
                value = body[j + 1 : k if k != -1 else len(body)]
            else:
                mm = re.match(r"[^,\n]+", body[j:])
                value = mm.group(0) if mm else ""
            fields[name] = re.sub(r"[{}]", "", squash(value))
        fields["_type"] = etype
        entries.append(fields)
    return entries


def parse_bibtex(text: str) -> List[dict]:
    """Return dicts with title/authors/journal/year/volume/issue/pages/doi/raw."""
    out = []
    for f in _bib_entries(text):
        authors = [a.strip() for a in re.split(r"\s+and\s+", f.get("author", "")) if a.strip()]
        out.append(
            {
                "title": f.get("title"), "authors": authors, "journal": f.get("journal") or f.get("booktitle"),
                "year": f.get("year"), "volume": f.get("volume"), "issue": f.get("number"), "pages": f.get("pages"),
                "doi": f.get("doi"), "raw": squash(f"{f.get('author', '')}. {f.get('title', '')}. {f.get('journal', f.get('booktitle', ''))}. {f.get('year', '')}"),
            }
        )
    return out


def parse_ris(text: str) -> List[dict]:
    out, cur = [], {}
    authors: List[str] = []

    def flush() -> None:
        nonlocal cur, authors
        if cur or authors:
            cur["authors"] = authors
            cur["raw"] = squash(f"{'; '.join(authors)}. {cur.get('title', '')}. {cur.get('journal', '')}. {cur.get('year', '')}")
            out.append(cur)
        cur, authors = {}, []

    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^([A-Z][A-Z0-9])\s{1,2}-\s?(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            flush()
        elif tag in ("AU", "A1"):
            authors.append(val)
        elif tag in ("TI", "T1"):
            cur["title"] = val
        elif tag in ("JO", "JF", "T2", "JA"):
            cur.setdefault("journal", val)
        elif tag in ("PY", "Y1"):
            cur["year"] = val[:4]
        elif tag == "VL":
            cur["volume"] = val
        elif tag == "IS":
            cur["issue"] = val
        elif tag == "SP":
            cur["start"] = val
        elif tag == "EP":
            cur["end"] = val
        elif tag == "DO":
            cur["doi"] = val
        elif tag == "ER":
            if cur.get("start"):
                cur["pages"] = f"{cur['start']}-{cur['end']}" if cur.get("end") else cur["start"]
            flush()
    if cur or authors:
        if cur.get("start"):
            cur["pages"] = f"{cur['start']}-{cur['end']}" if cur.get("end") else cur["start"]
        flush()
    return out
