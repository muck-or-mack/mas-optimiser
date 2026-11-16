"""Unit tests of ``stats.compare`` and ``stats.figures``: the command's outputs."""

import argparse
import csv

import numpy as np
import pandas as pd
import pytest

from mas_optimiser.stats import compare, figures, outcomes
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


def compared(a, b, out):
    """Run the command on two sides with its default options."""
    compare.run(
        argparse.Namespace(
            experiment=None,
            a=[str(p) for p in a],
            b=[str(p) for p in b],
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
    return out


def test_compare_writes_its_results_as_data_and_a_report_of_them(tmp_path):
    out = compared(*sides(tmp_path), tmp_path / "cmp")
    tests = pd.read_csv(out / "tests.csv")
    assert list(tests.columns) == compare.TEST_COLUMNS
    # Two outcomes, each on all eight tasks and on each of the two domains.
    assert list(tests["set"]) == ["all", "csm", "itsm"] * 2
    assert set(tests["outcome"]) == {"checks passed", "attempts fully passed"}
    whole = tests[(tests["set"] == "all") & (tests["outcome"] == "checks passed")].iloc[
        0
    ]
    assert whole["tasks"] == 8
    assert whole["gain_percentage_points"] == pytest.approx(
        whole["candidate_percent"] - whole["current_percent"]
    )
    assert whole["tasks_up"] + whole["tasks_down"] + whole["tasks_unchanged"] == 8

    rules = pd.read_csv(out / "rules.csv")
    assert list(rules.columns) == compare.RULE_COLUMNS
    assert list(rules["rule"]) == [
        "one decision at 5%",
        "5% shared over a run (REUSE), decision 1",
        "5% shared equally over 20 decisions",
        "3 sigma",
    ]
    # A stricter rule allows fewer false acceptances and sets a higher bar.
    assert rules["bar"].is_monotonic_increasing

    both = pd.read_csv(out / "sides.csv")
    assert list(both["side"]) == ["current", "candidate"]
    assert list(both["run"]) == ["a", "b"]
    assert list(both["attempts"]) == [16, 16]
    assert both["total_cost_usd"].tolist() == pytest.approx([0.16, 0.16])

    differences = pd.read_csv(out / "differences.csv")
    assert len(differences) == 8
    assert list(differences.columns) == [
        "task_id",
        "domain",
        "checks_passed_current_percent",
        "checks_passed_candidate_percent",
        "checks_passed_difference_percentage_points",
        "attempts_fully_passed_current_percent",
        "attempts_fully_passed_candidate_percent",
        "attempts_fully_passed_difference_percentage_points",
    ]

    text = (out / "comparison.md").read_text()
    assert text.startswith("# Comparison: b against a\n")
    for heading in (
        "## What is compared",
        "## Result",
        "## Cost and time",
        "## By domain (checks passed)",
        "## The test in full (all 8 tasks)",
        "## Decision rules (checks passed;",
        "## Data",
    ):
        assert heading in text
    # The report shows what the data file holds.
    assert f"{whole['gain_percentage_points']:+.1f} percentage points" in text
    for name in ("gains", "differences", "decisions", "detectable"):
        assert f"![{name}](figures/{name}.png)" in text
        assert (out / "figures" / f"{name}.png").exists()
        assert (out / "figures" / f"{name}.pdf").exists()


def test_a_set_whose_differences_are_all_equal_has_no_test():
    cmp = outcomes.Comparison(
        tasks=("t1", "t2", "t3"),
        domains=("itsm",) * 3,
        a=np.full(3, 0.5),
        b=np.full(3, 0.75),
        outcome="checks",
    )
    row = compare._row("all", cmp, 0.05)
    assert row["gain_percentage_points"] == pytest.approx(25.0)
    assert "verdict" not in row


def test_the_permutation_check_is_the_exact_sign_flip_test_and_is_reproducible():
    # Three non-zero differences of equal size: the observed sum, +1/3 - 1/3 + 1/3,
    # is matched or beaten by 4 of the 8 sign patterns, so p is exactly 0.5.
    d = np.array([1 / 3, 0.0, -1 / 3, 1 / 3, 0.0])
    cmp = outcomes.Comparison(
        tasks=tuple(f"t{i}" for i in range(5)),
        domains=("itsm",) * 5,
        a=np.full(5, 0.5),
        b=0.5 + d,
        outcome="checks",
    )
    row = compare._row("all", cmp, 0.05)
    assert row["permutation_p"] == pytest.approx(0.5)
    assert compare._row("all", cmp, 0.05) == row


def test_figures_are_redrawn_from_the_data_files_alone(tmp_path):
    out = compared(*sides(tmp_path), tmp_path / "cmp")
    for drawn in (out / "figures").iterdir():
        drawn.unlink()
    assert figures.draw(out) == ["gains", "differences", "detectable", "decisions"]
    # Each figure as PDF and PNG, and no file of plotted numbers beside them.
    assert sorted(p.name for p in (out / "figures").iterdir()) == sorted(
        f"{name}.{ext}"
        for name in ("gains", "differences", "detectable", "decisions")
        for ext in ("pdf", "png")
    )


def test_identical_sides_give_a_report_without_a_test(tmp_path):
    rows = [(f"i{k}", n, "False", k % 3, 3) for k in range(4) for n in (1, 2)]
    a = [write_evaluation(tmp_path / "a-itsm", "itsm", rows)]
    b = [write_evaluation(tmp_path / "b-itsm", "itsm", rows)]
    out = compared(a, b, tmp_path / "cmp")
    tests = pd.read_csv(out / "tests.csv")
    assert tests["gain_percentage_points"].eq(0).all() and tests["verdict"].isna().all()
    assert "n/a" in (out / "comparison.md").read_text()
    # Without a test there is no bar to draw: two figures, not four.
    assert sorted(p.stem for p in (out / "figures").glob("*.png")) == [
        "differences",
        "gains",
    ]


def test_evaluations_resolve_a_day_or_a_name_through_the_index(tmp_path):
    a, b = sides(tmp_path)
    experiment = tmp_path / "exp"
    experiment.mkdir()
    names = {
        a[0]: ("itsm-2026-10-27-0528", "2026-10-27T05:28:20"),
        a[1]: ("csm-2026-10-27-0550", "2026-10-27T05:50:13"),
        b[0]: ("itsm-2026-11-03-0803", "2026-11-03T08:03:28"),
        b[1]: ("csm-2026-11-03-0826", "2026-11-03T08:26:42"),
    }
    for folder, (name, _) in names.items():
        folder.rename(experiment / name)
    with open(experiment / "index.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["evaluation", "started", "domain"])
        writer.writeheader()
        for name, started in names.values():
            writer.writerow(
                {"evaluation": name, "started": started, "domain": name.split("-")[0]}
            )
    by_day = compare.evaluations(str(experiment), "20261027")
    assert sorted(p.name for p in by_day) == [
        "csm-2026-10-27-0550",
        "itsm-2026-10-27-0528",
    ]
    assert compare.evaluations(str(experiment), "2026-10-27") == by_day
    assert [p.name for p in compare.evaluations(str(experiment), "itsm-2026-11")] == [
        "itsm-2026-11-03-0803"
    ]
    folder = experiment / "itsm-2026-11-03-0803"
    assert compare.evaluations(None, str(folder)) == [folder]
    run_dir = experiment / "2026-11-03-0803"
    run_dir.mkdir()
    (experiment / "itsm-2026-11-03-0803").rename(run_dir / "itsm")
    (experiment / "csm-2026-11-03-0826").rename(run_dir / "csm")
    assert compare.evaluations(None, str(run_dir)) == [
        run_dir / "csm",
        run_dir / "itsm",
    ]
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit, match="holds no evaluation"):
        compare.evaluations(None, str(empty))
    with pytest.raises(SystemExit, match="more than one evaluation"):
        compare.evaluations(str(experiment), "itsm")
    with pytest.raises(SystemExit, match="no evaluation"):
        compare.evaluations(str(experiment), "20270101")
    with pytest.raises(SystemExit, match="give --experiment"):
        compare.evaluations(None, "20261027")
