"""Text similarity backends for the relevance check (F18, F19).

* sentence-transformers (all-MiniLM-L6-v2 by default): meaning-based, free, runs locally on CPU.
  Needs `pip install -r requirements-ml.txt`; the model (~90 MB) is downloaded on first use.
* tfidf: a small pure-Python fallback (word overlap). It keeps the app working with no ML dependencies, but
  it only sees shared vocabulary - use it for development and demos, not for final results.

With EMBEDDING_BACKEND=auto the app tries sentence-transformers first and falls back to tfidf.
"""
from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from typing import Dict, List, Sequence, Tuple

from ..config import Settings, get_settings
from ..utils.text import normalize

log = logging.getLogger("verifyscholar.embeddings")

_STOP = set(
    "the a an and or of to in on for with by as is are was were be been this that these those from at it its into than then which who whom "
    "can may might should would could not no also such we our their they them has have had using used use based via over under between "
    "within across about more most other some any each both many much very".split()
)


class TfidfBackend:
    name = "tfidf"

    @staticmethod
    def _tokens(text: str) -> List[str]:
        return [t for t in normalize(text).split() if len(t) > 2 and t not in _STOP]

    def score_pairs(self, pairs: Sequence[Tuple[str, str]]) -> List[float]:
        docs: Dict[str, Counter] = {}
        for a, b in pairs:
            for t in (a, b):
                if t not in docs:
                    docs[t] = Counter(self._tokens(t))
        n = max(1, len(docs))
        df: Counter = Counter()
        for c in docs.values():
            df.update(c.keys())
        idf = {w: math.log((1 + n) / (1 + d)) + 1.0 for w, d in df.items()}

        def vec(c: Counter) -> Dict[str, float]:
            return {w: (1 + math.log(tf)) * idf[w] for w, tf in c.items()}

        vecs = {t: vec(c) for t, c in docs.items()}
        norms = {t: math.sqrt(sum(v * v for v in vv.values())) or 1.0 for t, vv in vecs.items()}
        out = []
        for a, b in pairs:
            va, vb = vecs[a], vecs[b]
            if len(va) > len(vb):
                va, vb = vb, va
            dot = sum(v * vb.get(w, 0.0) for w, v in va.items())
            out.append(round(dot / (norms[a] * norms[b]), 4))
        return out


class SentenceTransformerBackend:
    name = "sentence-transformers"

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415 - optional dependency

        self.model = SentenceTransformer(model_name)

    def score_pairs(self, pairs: Sequence[Tuple[str, str]]) -> List[float]:
        uniq = list({t: None for p in pairs for t in p})
        emb = self.model.encode(uniq, normalize_embeddings=True, show_progress_bar=False)
        idx = {t: i for i, t in enumerate(uniq)}
        return [round(float(emb[idx[a]] @ emb[idx[b]]), 4) for a, b in pairs]


_lock = threading.Lock()
_backend = None


def get_backend(settings: Settings | None = None):
    global _backend
    settings = settings or get_settings()
    with _lock:
        if _backend is not None:
            return _backend
        choice = settings.embedding_backend.lower()
        if choice in ("auto", "sentence-transformers", "sentence_transformers"):
            try:
                _backend = SentenceTransformerBackend(settings.embedding_model)
                log.info("Using sentence-transformers (%s)", settings.embedding_model)
            except Exception as exc:  # ImportError, no network for the model download, ...
                if choice != "auto":
                    raise
                log.warning("sentence-transformers unavailable (%s); falling back to TF-IDF similarity", exc)
                _backend = TfidfBackend()
        else:
            _backend = TfidfBackend()
        return _backend


def reset_backend() -> None:  # used by tests
    global _backend
    with _lock:
        _backend = None


def thresholds(backend_name: str, settings: Settings | None = None) -> Tuple[float, float]:
    """(sentence-level high, paper-level high) for the active backend."""
    s = settings or get_settings()
    if backend_name == "sentence-transformers":
        return s.sentence_high_st, s.paper_high_st
    return s.sentence_high_tfidf, s.paper_high_tfidf
