"""Run the verification engine over the evaluation dataset against the live APIs.

    cd backend
    python -m evaluation.run                       # uses the settings in .env
    python -m evaluation.run --fresh               # ignore the HTTP cache (fresh database)
    python -m evaluation.run --out results.json    # also write per-citation results

It reports the ghost-detection rate and the false-flag rate, and lists every citation whose verdict
differs from what its label implies, so you can tune thresholds in .env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app import deps
from app.database import db
from app.services.http_client import http_service
from app.services.jobs import parse_raw_citations

from .metrics import compute_metrics, format_report

EXPECTED = {"real": {"verified"}, "corrupted": {"suspicious", "ghost"}, "fabricated": {"ghost"}}


async def main(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.dataset).read_text(encoding="utf-8"))["entries"]
    db.configure("sqlite://" if args.fresh else None)
    await http_service.start()
    try:
        warnings: list[str] = []
        parsed = await parse_raw_citations([e["citation"] for e in data], deps.grobid, deps.settings, warnings)
        for w in warnings:
            print("note:", w)
        print(f"Verifying {len(parsed)} citations (this calls CrossRef, OpenAlex and DOAJ)…")
        results = await deps.engine.verify_many(parsed, concurrency=args.concurrency)
    finally:
        await http_service.close()

    by_index = {r.index: r for r in results}
    labels, verdicts, rows = [], [], []
    for i, e in enumerate(data, start=1):
        r = by_index[i]
        labels.append(e["label"])
        verdicts.append(r.verdict)
        rows.append({"id": e["id"], "label": e["label"], "note": e.get("note"), "verdict": r.verdict, "confidence": r.confidence, "reason": r.reason, "flags": [f.code for f in r.flags]})

    m = compute_metrics(labels, verdicts)
    print(format_report(m))
    misses = [row for row in rows if row["verdict"] not in EXPECTED[row["label"]] and row["verdict"] != "inconclusive"]
    if misses:
        print("\nCitations whose verdict differs from the label (review these):")
        for row in misses:
            print(f"  #{row['id']:<3} {row['label']:<10} -> {row['verdict']:<10} score={row['confidence']}  {row.get('note') or ''}")
            print(f"        {row['reason'][:220]}")
    if args.out:
        Path(args.out).write_text(json.dumps({"metrics": m, "results": rows}, indent=2), encoding="utf-8")
        print(f"\nPer-citation results written to {args.out}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(Path(__file__).with_name("dataset.json")))
    ap.add_argument("--out", default=None)
    ap.add_argument("--fresh", action="store_true", help="use an empty in-memory database (no HTTP cache)")
    ap.add_argument("--concurrency", type=int, default=4)
    sys.exit(asyncio.run(main(ap.parse_args())))
