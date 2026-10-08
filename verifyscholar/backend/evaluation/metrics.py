"""Metrics for the evaluation harness (pure functions, unit-tested)."""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Sequence


def compute_metrics(labels: Sequence[str], verdicts: Sequence[str]) -> Dict[str, object]:
    """labels: real | corrupted | fabricated; verdicts: verified | suspicious | ghost | inconclusive."""
    assert len(labels) == len(verdicts)
    table: Dict[str, Counter] = {lab: Counter() for lab in ("real", "corrupted", "fabricated")}
    for lab, v in zip(labels, verdicts):
        table[lab][v] += 1
    n = {lab: sum(c.values()) for lab, c in table.items()}

    def rate(num: int, den: int) -> float | None:
        return round(num / den, 3) if den else None

    real_flagged = n["real"] - table["real"]["verified"] - table["real"]["inconclusive"]
    ghost_total = sum(c["ghost"] for c in table.values())
    return {
        "counts": {lab: dict(c) for lab, c in table.items()},
        "n": n,
        # genuine citations wrongly marked suspicious/ghost (lower is better)
        "false_flag_rate": rate(real_flagged, n["real"] - table["real"]["inconclusive"]),
        "false_ghost_rate": rate(table["real"]["ghost"], n["real"] - table["real"]["inconclusive"]),
        # invented citations marked ghost (the headline number) and ghost-or-suspicious (caught at all)
        "ghost_detection_rate": rate(table["fabricated"]["ghost"], n["fabricated"] - table["fabricated"]["inconclusive"]),
        "fabricated_flagged_rate": rate(table["fabricated"]["ghost"] + table["fabricated"]["suspicious"], n["fabricated"] - table["fabricated"]["inconclusive"]),
        # right-paper-wrong-details citations that were not waved through as verified
        "corrupted_caught_rate": rate(table["corrupted"]["suspicious"] + table["corrupted"]["ghost"], n["corrupted"] - table["corrupted"]["inconclusive"]),
        "ghost_precision": rate(table["fabricated"]["ghost"], ghost_total),
        "inconclusive": sum(c["inconclusive"] for c in table.values()),
    }


def format_report(m: Dict[str, object]) -> str:
    def pct(x):
        return "n/a" if x is None else f"{x * 100:.1f}%"

    lines = ["", "VerifyScholar evaluation", "=" * 60]
    for lab in ("real", "corrupted", "fabricated"):
        c = m["counts"][lab]  # type: ignore[index]
        lines.append(f"{lab:<11} n={m['n'][lab]:<3} verified={c.get('verified', 0):<3} suspicious={c.get('suspicious', 0):<3} ghost={c.get('ghost', 0):<3} inconclusive={c.get('inconclusive', 0)}")  # type: ignore[index]
    lines += [
        "-" * 60,
        f"Ghost detection rate (fabricated -> ghost):          {pct(m['ghost_detection_rate'])}",
        f"Fabricated flagged at all (ghost or suspicious):     {pct(m['fabricated_flagged_rate'])}",
        f"Corrupted citations caught (not 'verified'):         {pct(m['corrupted_caught_rate'])}",
        f"False-flag rate (real flagged suspicious/ghost):     {pct(m['false_flag_rate'])}",
        f"False-ghost rate (real marked ghost):                {pct(m['false_ghost_rate'])}",
        f"Ghost precision (ghost verdicts that are fabricated): {pct(m['ghost_precision'])}",
        "=" * 60,
    ]
    return "\n".join(lines)
