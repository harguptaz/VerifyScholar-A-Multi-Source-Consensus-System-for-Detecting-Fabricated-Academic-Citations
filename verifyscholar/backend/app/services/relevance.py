"""Relevance analysis for uploaded documents (Mode C: F16-F20).

For every verified reference we compare its abstract with
  1. each sentence that cites it  -> does the cited paper support the claim it is attached to?
  2. the citing paper's title + abstract -> is the cited paper on-topic, or merely listed?
References that are never cited in the text are flagged as orphans.
Everything here is advisory: it never changes a verdict.
"""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ..config import Settings, get_settings
from ..schemas import CitationResult, DocumentInfo, Relevance
from . import embeddings
from .grobid import ParsedDocument
from .merge import same_work  # noqa: F401  (kept for symmetry with other services)

MAX_SENTENCES_PER_REF = 6


def _cited_text(r: CitationResult) -> Optional[str]:
    if r.record and r.record.abstract and r.verdict in ("verified", "suspicious"):
        return f"{r.record.title or ''}. {r.record.abstract}".strip()
    return None


def _paper_text(doc: ParsedDocument) -> Optional[str]:
    if doc.abstract:
        return f"{doc.title or ''}. {doc.abstract}".strip()
    if doc.title and doc.body_text:
        return f"{doc.title}. {doc.body_text[:1500]}"
    return None


def combined_label(sentence_score: Optional[float], paper_score: Optional[float], sent_high: float, paper_high: float) -> Optional[str]:
    if sentence_score is None and paper_score is None:
        return None
    if sentence_score is None:
        return None
    s_hi = sentence_score >= sent_high
    if paper_score is None:
        return "well_supported" if s_hi else "needs_review"
    p_hi = paper_score >= paper_high
    if s_hi and p_hi:
        return "well_supported"
    if p_hi:
        return "possibly_misattributed"
    if s_hi:
        return "background"
    return "needs_review"


async def analyze(doc: ParsedDocument, results: List[CitationResult], settings: Optional[Settings] = None) -> DocumentInfo:
    settings = settings or get_settings()
    backend = await asyncio.to_thread(embeddings.get_backend, settings)
    sent_high, paper_high = embeddings.thresholds(backend.name, settings)

    sentences_by_ref: Dict[str, List[str]] = defaultdict(list)
    for m in doc.mentions:
        s = re.sub(r"\s+", " ", m.sentence).strip()[:600]
        if s and s not in sentences_by_ref[m.ref_id]:
            sentences_by_ref[m.ref_id].append(s)

    paper_text = _paper_text(doc)
    pairs: List[Tuple[str, str]] = []
    plan: List[Tuple[CitationResult, List[str], Optional[str]]] = []
    for r in results:
        ref_id = r.parsed.ref_id or ""
        sentences = sentences_by_ref.get(ref_id, [])[:MAX_SENTENCES_PER_REF]
        cited = _cited_text(r)
        plan.append((r, sentences, cited))
        if cited:
            pairs.extend((s, cited) for s in sentences)
            if paper_text:
                pairs.append((paper_text, cited))

    scores = await asyncio.to_thread(backend.score_pairs, pairs) if pairs else []
    cursor = 0
    orphans: List[int] = []
    for r, sentences, cited in plan:
        mentions = len(sentences)
        orphan = mentions == 0
        if orphan:
            orphans.append(r.index)
        if not cited:
            reason = (
                "The reference could not be verified, so there is no abstract to compare"
                if r.verdict not in ("verified", "suspicious") else "No abstract is available for this paper in CrossRef or OpenAlex"
            )
            r.relevance = Relevance(status="not_assessed", reason=reason, mentions=mentions, orphan=orphan, sentences=sentences)
            continue
        sent_scores = scores[cursor : cursor + len(sentences)]
        cursor += len(sentences)
        paper_score = None
        if paper_text:
            paper_score = scores[cursor]
            cursor += 1
        best_sentence, sentence_score = None, None
        if sent_scores:
            k = max(range(len(sent_scores)), key=lambda i: sent_scores[i])
            best_sentence, sentence_score = sentences[k], sent_scores[k]
        label = combined_label(sentence_score, paper_score, sent_high, paper_high)
        r.relevance = Relevance(
            status="assessed", sentence_score=sentence_score, paper_score=paper_score, label=label,  # type: ignore[arg-type]
            best_sentence=best_sentence, mentions=mentions, orphan=orphan, sentences=sentences,
            reason="Listed in the bibliography but never cited in the text" if orphan else None,
        )

    return DocumentInfo(
        title=doc.title, abstract=doc.abstract, n_references=len(results), orphan_indices=orphans,
        unmatched_markers=list(dict.fromkeys(doc.unmatched_markers))[:30], embedding_backend=backend.name,
    )


def link_only(doc: ParsedDocument, results: List[CitationResult]) -> DocumentInfo:
    """In-text linking and orphan detection without the (heavier) embedding comparison."""
    counts: Dict[str, List[str]] = defaultdict(list)
    for m in doc.mentions:
        s = re.sub(r"\s+", " ", m.sentence).strip()[:600]
        if s and s not in counts[m.ref_id]:
            counts[m.ref_id].append(s)
    orphans: List[int] = []
    for r in results:
        sentences = counts.get(r.parsed.ref_id or "", [])
        orphan = not sentences
        if orphan:
            orphans.append(r.index)
        r.relevance = Relevance(status="not_assessed", reason="The relevance check was not requested for this upload", mentions=len(sentences), orphan=orphan, sentences=sentences[:MAX_SENTENCES_PER_REF])
    return DocumentInfo(
        title=doc.title, abstract=doc.abstract, n_references=len(results), orphan_indices=orphans,
        unmatched_markers=list(dict.fromkeys(doc.unmatched_markers))[:30],
    )
