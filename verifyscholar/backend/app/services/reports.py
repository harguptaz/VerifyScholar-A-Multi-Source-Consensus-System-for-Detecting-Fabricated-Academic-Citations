"""CSV and PDF exports of the verification report (F25) and the transparency log (F27)."""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from typing import List, Sequence
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..schemas import JobResult, LogResponse

_VERDICT_LABEL = {"verified": "Verified", "suspicious": "Suspicious", "ghost": "Ghost", "inconclusive": "Inconclusive"}
_DOI_LABEL = {"found": "Found", "mismatch": "Mismatch", "not_found": "Not found", "no_doi": "No DOI in citation", "unavailable": "Source unavailable"}
_JOURNAL_LABEL = {"verified_in_index": "Verified in CrossRef/OpenAlex", "found_in_doaj": "Found in DOAJ only", "not_found": "Not found", "not_provided": "Not provided", "unavailable": "Source unavailable"}
_RELEVANCE_LABEL = {"well_supported": "Well supported", "possibly_misattributed": "Possibly misattributed", "background": "Background / off-topic", "needs_review": "Needs review"}


# ------------------------------------------------------------------------------------------
# fonts
# ------------------------------------------------------------------------------------------
def _register_fonts() -> tuple[str, str]:
    try:
        import reportlab

        folder = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
        if "VerifyVera" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("VerifyVera", os.path.join(folder, "Vera.ttf")))
            pdfmetrics.registerFont(TTFont("VerifyVera-Bold", os.path.join(folder, "VeraBd.ttf")))
        return "VerifyVera", "VerifyVera-Bold"
    except Exception:  # fall back to the built-in Latin-1 fonts
        return "Helvetica", "Helvetica-Bold"


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(text if text is not None else "")), style)


def _truncate(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _pdf(title: str, intro: Sequence[str], header: List[str], rows: List[List[str]], widths: List[float], footer: Sequence[str]) -> bytes:
    regular, bold = _register_fonts()
    styles = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=styles["Title"], fontName=bold, fontSize=16, alignment=0, spaceAfter=4)
    body = ParagraphStyle("b", parent=styles["BodyText"], fontName=regular, fontSize=8.5, leading=11)
    cell = ParagraphStyle("c", parent=body, fontSize=7.5, leading=9.5)
    head = ParagraphStyle("hd", parent=cell, fontName=bold, textColor=colors.white)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=title)
    story: list = [Paragraph(escape(title), h)]
    story += [Paragraph(escape(line), body) for line in intro]
    story.append(Spacer(1, 6))
    data = [[Paragraph(escape(x), head) for x in header]] + [[_p(c, cell) for c in row] for row in rows]
    total = sum(widths)
    avail = landscape(A4)[0] - 28 * mm
    tbl = Table(data, colWidths=[w / total * avail for w in widths], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2d3d")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c5ccd3")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f8")]),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 8))
    story += [Paragraph(escape(line), body) for line in footer]
    doc.build(story)
    return buf.getvalue()


# ------------------------------------------------------------------------------------------
# verification report
# ------------------------------------------------------------------------------------------
def _summary_line(jr: JobResult) -> str:
    s = jr.summary
    parts = [f"{s.verified} Verified", f"{s.suspicious} Suspicious", f"{s.ghost} Ghost"]
    if s.inconclusive:
        parts.append(f"{s.inconclusive} Inconclusive")
    line = f"{s.total} citations checked: " + ", ".join(parts) + "."
    if s.retracted:
        line += f" {s.retracted} retracted."
    return line


def report_csv(jr: JobResult) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["#", "citation", "verdict", "confidence", "doi_status", "journal_status", "retraction_status", "matched_title", "matched_doi", "link", "open_access_link", "reason", "relevance", "sentence_score", "paper_score", "orphan"])
    for r in jr.results:
        rec = r.record
        rel = r.relevance
        w.writerow([
            r.index, r.raw, r.verdict, "" if r.confidence is None else r.confidence, r.doi_status, r.journal_status, r.retraction.status,
            rec.title if rec else "", rec.doi if rec and rec.doi else "", r.links.doi_url or "", r.links.open_access or "", r.reason,
            (rel.label or rel.status) if rel else "", rel.sentence_score if rel and rel.sentence_score is not None else "",
            rel.paper_score if rel and rel.paper_score is not None else "", ("yes" if rel and rel.orphan else "") if rel else "",
        ])
    return buf.getvalue()


def report_pdf(jr: JobResult, job_id: str) -> bytes:
    rows = []
    for r in jr.results:
        match = ""
        if r.record:
            match = _truncate(r.record.title or "", 90) + (f"\n{r.links.doi_url}" if r.links.doi_url else "")
        retr = {"retracted": "RETRACTED", "concern": "Concern", "corrected": "Correction", "none_found": "None found", "unknown": "—"}[r.retraction.status]
        rows.append([str(r.index), _truncate(r.raw, 220), _VERDICT_LABEL[r.verdict], "—" if r.confidence is None else str(r.confidence),
                     _DOI_LABEL[r.doi_status], _JOURNAL_LABEL[r.journal_status], retr, match])
    intro = [_summary_line(jr), f"Job {job_id} · generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
    footer = ["Verdicts are based on CrossRef, OpenAlex and DOAJ records and are advisory. A missing record can mean the paper is not indexed, not only that it is fabricated."]
    if jr.document and jr.document.orphan_indices:
        footer.append("References never cited in the text: " + ", ".join(f"#{i}" for i in jr.document.orphan_indices))
    return _pdf("VerifyScholar verification report", intro, ["#", "Citation (as submitted)", "Verdict", "Score", "DOI", "Journal", "Retraction", "Matched paper"], rows, [4, 38, 8, 5, 9, 12, 8, 26], footer)


# ------------------------------------------------------------------------------------------
# transparency log
# ------------------------------------------------------------------------------------------
def log_csv(log: LogResponse) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["seq", "timestamp_utc", "citation", "source", "query", "response", "verdict", "prev_hash", "entry_hash"])
    for e in log.entries:
        w.writerow([e.seq, e.timestamp, e.citation_index or "", e.source, e.query, e.outcome, e.verdict or "", e.prev_hash, e.entry_hash])
    w.writerow([])
    w.writerow(["chain_valid", log.chain_valid, "final_hash", log.final_hash or ""])
    return buf.getvalue()


def log_pdf(log: LogResponse) -> bytes:
    rows = [[str(e.seq), e.timestamp.replace("T", " ").replace("+00:00", " UTC"), str(e.citation_index or ""), e.source, _truncate(e.query, 110), _truncate(e.outcome, 70), _VERDICT_LABEL.get(e.verdict or "", e.verdict or ""), e.entry_hash[:12] + "…"] for e in log.entries]
    intro = [
        f"Job {log.job_id}: {len(log.entries)} recorded actions.",
        "Each row's hash covers its own content and the previous row's hash, so any later change to the log can be detected.",
    ]
    footer = [f"Chain integrity: {'VALID' if log.chain_valid else 'INVALID (first broken entry: #' + str(log.first_invalid_seq) + ')'}", f"Final hash (SHA-256): {log.final_hash or 'n/a'}"]
    return _pdf("VerifyScholar source transparency log", intro, ["#", "Timestamp", "Cit.", "Source", "Query sent", "Response", "Verdict", "Hash"], rows, [4, 15, 4, 12, 36, 16, 8, 9], footer)
