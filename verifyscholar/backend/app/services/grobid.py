"""GROBID client + TEI XML parsing.

GROBID (https://github.com/kermitt2/grobid) turns a PDF into structured TEI XML. We use:
  * /api/processFulltextDocument  - whole paper: header, body sentences, in-text citation markers, bibliography
  * /api/processCitationList      - a list of raw reference strings -> structured biblStruct entries

consolidateCitations is deliberately left at 0. Consolidation makes GROBID query CrossRef and "repair"
the metadata, which would silently fix hallucinated citations before we get to check them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx
from lxml import etree

from ..config import Settings, get_settings
from ..schemas import Author, ParsedCitation
from ..utils.text import clean_doi, extract_arxiv_id, extract_doi, squash
from .parsing import finalize

TEI = "http://www.tei-c.org/ns/1.0"
NS = {"tei": TEI}
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


class GrobidError(Exception):
    pass


@dataclass
class InTextMention:
    ref_id: str
    marker: str
    sentence: str


@dataclass
class ParsedDocument:
    title: Optional[str] = None
    abstract: Optional[str] = None
    body_text: str = ""
    references: List[ParsedCitation] = field(default_factory=list)
    mentions: List[InTextMention] = field(default_factory=list)
    unmatched_markers: List[str] = field(default_factory=list)


def _text(el: Optional[etree._Element]) -> str:
    return squash("".join(el.itertext())) if el is not None else ""


def parse_bibl_struct(b: etree._Element, index: int) -> ParsedCitation:
    """Convert one TEI <biblStruct> into a ParsedCitation."""
    analytic = b.find("tei:analytic", NS)
    monogr = b.find("tei:monogr", NS)

    title_el = None
    if analytic is not None:
        title_el = analytic.find("tei:title[@type='main']", NS)
        if title_el is None:
            title_el = analytic.find("tei:title", NS)
    mono_title = monogr.find("tei:title", NS) if monogr is not None else None
    if title_el is not None and _text(title_el):
        title, journal = _text(title_el), _text(mono_title) or None
    else:  # books / reports have only a monograph title
        title, journal = _text(mono_title) or None, None

    authors: List[Author] = []
    author_parent = analytic if analytic is not None and analytic.findall("tei:author", NS) else monogr
    if author_parent is not None:
        for a in author_parent.findall("tei:author", NS):
            sur = _text(a.find("tei:persName/tei:surname", NS))
            given = " ".join(_text(f) for f in a.findall("tei:persName/tei:forename", NS))
            if sur or given:
                authors.append(Author(family=sur, given=given, name=f"{sur}, {given}".strip(", ")))

    doi = None
    for idno in b.iterfind(".//tei:idno[@type='DOI']", NS):
        if _text(idno):
            doi = clean_doi(_text(idno))
            break

    year = None
    d = b.find(".//tei:imprint/tei:date", NS)
    if d is not None:
        when = d.get("when") or _text(d)
        m = re.search(r"(1[5-9]\d{2}|20\d{2})", when or "")
        year = int(m.group(1)) if m else None

    volume = issue = pages = None
    for bs in b.iterfind(".//tei:imprint/tei:biblScope", NS):
        unit = bs.get("unit")
        if unit == "volume":
            volume = _text(bs) or None
        elif unit == "issue":
            issue = _text(bs) or None
        elif unit == "page":
            frm, to = bs.get("from"), bs.get("to")
            pages = f"{frm}-{to}" if frm and to and frm != to else (frm or _text(bs) or None)

    raw_el = b.find("tei:note[@type='raw_reference']", NS)
    raw = _text(raw_el)
    if not raw:  # rebuild something readable when raw strings were not requested
        raw = squash(f"{'; '.join(a.display() for a in authors)}. ({year or 'n.d.'}). {title or ''}. {journal or ''}")
    if not doi:
        doi = extract_doi(raw)

    arxiv = None
    for idno in b.iterfind(".//tei:idno", NS):
        if (idno.get("type") or "").lower() == "arxiv" and _text(idno):
            arxiv = extract_arxiv_id(_text(idno)) or extract_arxiv_id("arXiv:" + _text(idno))
            break
    publisher = _text(b.find(".//tei:imprint/tei:publisher", NS)) or None

    return finalize(ParsedCitation(
        index=index, raw=raw, doi=doi, title=title, authors=authors, journal=journal, year=year,
        volume=volume, issue=issue, pages=pages, parse_method="grobid", ref_id=b.get(XML_ID), arxiv_id=arxiv, publisher=publisher,
    ))


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(])")


def _sentence_for(ref: etree._Element) -> str:
    anc = ref.xpath("ancestor::tei:s[1]", namespaces=NS)
    if anc:
        return _text(anc[0])
    para = ref.xpath("ancestor::tei:p[1]", namespaces=NS)
    if not para:
        return _text(ref.getparent())
    marker = _text(ref)
    ptext = _text(para[0])
    for sent in _SENT_SPLIT.split(ptext):
        if marker and marker in sent:
            return sent
    return ptext[:500]


def parse_fulltext_tei(xml_bytes: bytes) -> ParsedDocument:
    root = etree.fromstring(xml_bytes)
    doc = ParsedDocument()
    doc.title = _text(root.find(".//tei:teiHeader//tei:titleStmt/tei:title", NS)) or None
    abstract = root.find(".//tei:teiHeader//tei:profileDesc/tei:abstract", NS)
    doc.abstract = _text(abstract) or None

    body = root.find(".//tei:text/tei:body", NS)
    doc.body_text = _text(body)

    bibls = root.xpath("//tei:text/tei:back//tei:listBibl/tei:biblStruct", namespaces=NS)
    id_to_index: Dict[str, int] = {}
    for i, b in enumerate(bibls, start=1):
        pc = parse_bibl_struct(b, i)
        doc.references.append(pc)
        if pc.ref_id:
            id_to_index[pc.ref_id] = i

    if body is not None:
        for ref in body.iterfind(".//tei:ref[@type='bibr']", NS):
            marker = _text(ref)
            target = (ref.get("target") or "").lstrip("#")
            if target and target in id_to_index:
                doc.mentions.append(InTextMention(ref_id=target, marker=marker, sentence=_sentence_for(ref)))
            elif marker:
                doc.unmatched_markers.append(marker)
    return doc


def parse_citation_list_tei(xml_bytes: bytes, raws: List[str]) -> List[ParsedCitation]:
    root = etree.fromstring(xml_bytes)
    bibls = root.xpath("//tei:biblStruct", namespaces=NS)
    out: List[ParsedCitation] = []
    for i, raw in enumerate(raws, start=1):
        if i - 1 < len(bibls):
            pc = parse_bibl_struct(bibls[i - 1], i)
            pc.raw = raw  # always keep the text the user pasted
            pc.doi = pc.doi or extract_doi(raw)
            out.append(finalize(pc))  # re-run now that the pasted text is available (arXiv ids, books)
        else:
            out.append(finalize(ParsedCitation(index=i, raw=raw, doi=extract_doi(raw), parse_method="heuristic")))
    return out


class GrobidClient:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._transport: Optional[httpx.AsyncBaseTransport] = None  # test hook

    @property
    def base(self) -> str:
        return self.settings.grobid_url.rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(self.settings.grobid_timeout_seconds, connect=5.0), transport=self._transport)

    async def status(self) -> dict:
        """Is GROBID reachable from here, and if not, why? (shown in /health and in the UI banner)"""
        try:
            async with httpx.AsyncClient(timeout=3.0, transport=self._transport) as c:
                r = await c.get(f"{self.base}/api/isalive")
        except httpx.HTTPError as exc:
            return {"alive": False, "url": self.base, "detail": f"Cannot connect to {self.base} ({type(exc).__name__}). It may still be starting, or GROBID_URL is wrong."}
        if r.status_code == 200 and "true" in r.text.lower():
            return {"alive": True, "url": self.base, "detail": "GROBID is up"}
        return {"alive": False, "url": self.base, "detail": f"GROBID answered HTTP {r.status_code}; it may still be starting."}

    async def is_alive(self) -> bool:
        return bool((await self.status())["alive"])

    async def process_fulltext(self, pdf_bytes: bytes, filename: str = "document.pdf") -> ParsedDocument:
        files = {"input": (filename, pdf_bytes, "application/pdf")}
        data = {"consolidateCitations": "0", "consolidateHeader": "0", "includeRawCitations": "1", "segmentSentences": "1"}
        try:
            async with self._client() as c:
                r = await c.post(f"{self.base}/api/processFulltextDocument", files=files, data=data, headers={"Accept": "application/xml"})
        except httpx.HTTPError as exc:
            raise GrobidError(f"Could not reach GROBID at {self.base}: {exc}") from exc
        if r.status_code == 204:
            raise GrobidError("GROBID could not extract any content from this PDF (is it a scanned image?)")
        if r.status_code != 200:
            raise GrobidError(f"GROBID returned HTTP {r.status_code}: {r.text[:200]}")
        try:
            return parse_fulltext_tei(r.content)
        except etree.XMLSyntaxError as exc:
            raise GrobidError(f"GROBID returned invalid XML: {exc}") from exc

    async def process_citation_list(self, raws: List[str]) -> List[ParsedCitation]:
        data = {"citations": list(raws), "consolidateCitations": "0"}  # a list value repeats the form field
        try:
            async with self._client() as c:
                r = await c.post(f"{self.base}/api/processCitationList", data=data, headers={"Accept": "application/xml"})
        except httpx.HTTPError as exc:
            raise GrobidError(f"Could not reach GROBID at {self.base}: {exc}") from exc
        if r.status_code != 200:
            raise GrobidError(f"GROBID returned HTTP {r.status_code}: {r.text[:200]}")
        try:
            parsed = parse_citation_list_tei(r.content, raws)
        except etree.XMLSyntaxError as exc:
            raise GrobidError(f"GROBID returned invalid XML: {exc}") from exc
        if len(parsed) != len(raws):  # pragma: no cover - defensive
            raise GrobidError("GROBID returned an unexpected number of references")
        return parsed


grobid_client = GrobidClient()
