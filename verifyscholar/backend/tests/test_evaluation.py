import json
from pathlib import Path

from evaluation.metrics import compute_metrics, format_report
from app.services.parsing import heuristic_parse


def test_metrics():
    labels = ["real"] * 4 + ["corrupted"] * 2 + ["fabricated"] * 4
    verdicts = ["verified", "verified", "verified", "suspicious", "suspicious", "verified", "ghost", "ghost", "suspicious", "inconclusive"]
    m = compute_metrics(labels, verdicts)
    assert m["false_flag_rate"] == 0.25 and m["false_ghost_rate"] == 0.0
    assert m["corrupted_caught_rate"] == 0.5
    assert m["ghost_detection_rate"] == round(2 / 3, 3) and m["fabricated_flagged_rate"] == 1.0
    assert m["ghost_precision"] == 1.0 and m["inconclusive"] == 1
    assert "Ghost detection rate" in format_report(m)


def test_dataset_is_well_formed_and_parses():
    data = json.loads((Path(__file__).parents[1] / "evaluation" / "dataset.json").read_text())["entries"]
    assert len(data) >= 35 and len({e["id"] for e in data}) == len(data)
    assert {e["label"] for e in data} == {"real", "corrupted", "fabricated"}
    for e in data:
        p = heuristic_parse(e["citation"], e["id"])
        assert p.title and p.authors, f"entry {e['id']} did not parse: {e['citation'][:60]}"
