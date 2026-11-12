"""Unit tests of ``stats.compare`` and ``stats.figures``: the command's outputs."""

import argparse
import csv

import numpy as np

from mas_optimiser.stats import compare, figures, gate, outcomes
from tests.unit.test_stats import write_evaluation


def sides(tmp_path):
    """Two domains of four tasks, two attempts each, with the candidate ahead."""
    rows_a = {
        "itsm": [(f"i{k}", n, "False", k % 3, 3) for k in range(4) for n in (1, 2)],
        "csm": [
            (f"c{k}", n, str(k == 0), 1 + k % 2, 2) for k in range(4) for n in (1, 2)
        ],
    }
    rows_b = {
        "itsm": [
            (f"i{k}", n, str(k < 2), min(3, k % 3 + n), 3)
            for k in range(4)
            for n in (1, 2)
        ],
        "csm": [(f"c{k}", n, str(k < 3), 2, 2) for k in range(4) for n in (1, 2)],
    }
    a = [write_evaluation(tmp_path / f"a-{d}", d, r) for d, r in rows_a.items()]
    b = [write_evaluation(tmp_path / f"b-{d}", d, r) for d, r in rows_b.items()]
    return a, b


def test_compare_writes_the_tables_the_differences_and_the_figures(tmp_path):
    a, b = sides(tmp_path)
    out = tmp_path / "cmp"
    compare.run(
        argparse.Namespace(
            a=a,
            b=b,
            split=None,
            subset="validation",
            outcome="checks",
            alpha=0.05,
            sigma=3.0,
            decisions=20,
            round_number=1,
            promotions=0,
            candidates=1,
            out=out,
        )
    )
    text = (out / "comparison.md").read_text()
    assert "## Fraction of checks passed (the gate's outcome)" in text
    assert "## Task success" in text
    assert "## Decision rules on the pooled fraction of checks passed" in text
    assert "REUSE allocation, round 1" in text
    assert text.count("| pooled |") == 2
    with open(out / "differences.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 8
    assert set(rows[0]) == {
        "task_id",
        "domain",
        "a_checks",
        "b_checks",
        "d_checks",
        "a_success",
        "b_success",
        "d_success",
    }
    for name in ("differences", "threshold", "power", "domains"):
        for ext in ("pdf", "png", "csv"):
            assert (out / "figures" / f"{name}.{ext}").exists()


def test_figures_save_the_numbers_they_plot(tmp_path):
    rng = np.random.default_rng(0)
    d = rng.choice([-1 / 3, 0.0, 1 / 3], size=40, p=[0.2, 0.5, 0.3])
    cmp = outcomes.Comparison(
        tasks=tuple(f"t{i}" for i in range(40)),
        domains=tuple("itsm" if i < 20 else "csm" for i in range(40)),
        a=np.full(40, 0.5),
        b=0.5 + d,
        outcome="success",
    )
    test = gate.mean_test(cmp.d)
    figures.threshold(test, tmp_path)
    figures.power_curve(test.se, 0.05, tmp_path)
    figures.differences(cmp, tmp_path)
    figures.domains(cmp, tmp_path)
    with open(tmp_path / "power.csv") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["gain_points"] == "0.0"
    assert abs(float(rows[0]["power"]) - 0.05) < 1e-6
    with open(tmp_path / "domains.csv") as f:
        rows = list(csv.DictReader(f))
    assert [r["set"] for r in rows] == ["csm", "itsm", "pooled"]
    assert rows[-1]["tasks"] == "40"
    with open(tmp_path / "differences.csv") as f:
        rows = list(csv.DictReader(f))
    assert sum(int(r["tasks"]) for r in rows) == 40
