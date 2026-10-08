"""Tamper-evident transparency log (F26, F27).

Every verification action is stored as a row whose SHA-256 hash covers the row's own content *and* the
hash of the previous row of the same job. Changing, deleting or re-ordering any earlier row therefore
invalidates the hash of every later row, and the final hash printed on an export lets a third party
confirm the exported log matches what the system recorded.
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict, List, Optional

from sqlalchemy import select

from ..database import LogRow, db
from ..schemas import CitationResult, LogEntry, LogResponse

GENESIS = "0" * 64


def entry_hash(prev_hash: str, job_id: str, seq: int, timestamp: str, citation_index: Optional[int], source: str, query: str, outcome: str, verdict: Optional[str]) -> str:
    payload = json.dumps(
        {"prev": prev_hash, "job": job_id, "seq": seq, "ts": timestamp, "cit": citation_index, "src": source, "q": query, "out": outcome, "verdict": verdict},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_job_log(job_id: str, results: List[CitationResult]) -> int:
    """Append the calls of every citation (in citation order) to the chain, tagged with the final verdict."""
    prev, seq = GENESIS, 0
    with db.session() as s:
        for r in sorted(results, key=lambda x: x.index):
            for call in r.calls:
                seq += 1
                h = entry_hash(prev, job_id, seq, call["timestamp"], call.get("citation_index"), call["source"], call["query"], call["outcome"], r.verdict)
                s.add(LogRow(
                    job_id=job_id, seq=seq, timestamp=call["timestamp"], citation_index=call.get("citation_index"), source=call["source"],
                    query=call["query"], outcome=call["outcome"], verdict=r.verdict, prev_hash=prev, entry_hash=h,
                ))
                prev = h
    return seq


def read_log(job_id: str) -> LogResponse:
    with db.session() as s:
        rows = list(s.scalars(select(LogRow).where(LogRow.job_id == job_id).order_by(LogRow.seq)))
    entries = [
        LogEntry(seq=r.seq, timestamp=r.timestamp, citation_index=r.citation_index, source=r.source, query=r.query, outcome=r.outcome,
                 verdict=r.verdict, prev_hash=r.prev_hash, entry_hash=r.entry_hash)
        for r in rows
    ]
    valid, bad = verify_entries(job_id, entries)
    return LogResponse(job_id=job_id, entries=entries, chain_valid=valid, final_hash=entries[-1].entry_hash if entries else None, first_invalid_seq=bad)


def verify_entries(job_id: str, entries: List[LogEntry]) -> tuple[bool, Optional[int]]:
    """Recompute the chain from the stored content. Returns (valid, first_invalid_seq)."""
    prev = GENESIS
    for n, e in enumerate(entries, start=1):
        expected = entry_hash(prev, job_id, e.seq, e.timestamp, e.citation_index, e.source, e.query, e.outcome, e.verdict)
        if e.seq != n or e.prev_hash != prev or e.entry_hash != expected:
            return False, e.seq
        prev = e.entry_hash
    return True, None
