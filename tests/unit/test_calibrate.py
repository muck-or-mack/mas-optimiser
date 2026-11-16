"""Unit tests of ``stats.calibrate``: the gate's statistic over random splits of one harness's attempts."""

import argparse
import json

import numpy as np
import pandas as pd
import pytest

from mas_optimiser.stats import calibrate
from tests.unit.test_stats import write_evaluation


def test_pulls_of_attempts_drawn_alike_have_mean_zero_and_unit_width():
    scores = np.random.default_rng(0).normal(size=(60, 6))
    t = calibrate.pulls(scores, splits=4000, seed=1)
    assert t.shape == (4000,)
    # Every attempt comes from one distribution, so T is Student's t with 59
    # degrees of freedom: mean 0, standard deviation 1.02.
    assert t.mean() == pytest.approx(0.0, abs=0.06)
    assert t.std(ddof=1) == pytest.approx(1.02, abs=0.05)
    assert np.array_equal(calibrate.pulls(scores, splits=4000, seed=1), t)


def runs(tmp_path):
    """Two runs of one harness on six ITSM tasks, two attempts each."""
    first = [(f"i{k}", n, str(k < 2), (k + n) % 4, 3) for k in range(6) for n in (1, 2)]
    second = [
        (f"i{k}", n, str(k < 3), (k * n) % 4, 3) for k in range(6) for n in (1, 2)
    ]
    a = write_evaluation(tmp_path / "a-itsm", "itsm", first)
    b = write_evaluation(tmp_path / "b-itsm", "itsm", second)
    return a, b


def options(a, b, out):
    return argparse.Namespace(
        experiment=None,
        a=str(a),
        b=str(b),
        split=None,
        subset="validation",
        outcome="checks",
        alpha=0.05,
        splits=300,
        seed=0,
        out=out,
    )


def test_calibrate_writes_its_results_as_data_and_a_report_of_them(tmp_path):
    a, b = runs(tmp_path)
    out = tmp_path / "cal"
    calibrate.run(options(a, b, out))
    scores = pd.read_csv(out / "scores.csv")
    assert list(scores.columns) == [
        "task_id",
        "domain",
        "outcome",
        "first_run_percent",
        "second_run_percent",
    ]
    assert len(scores) == 6
    pulls = pd.read_csv(out / "pulls.csv")
    assert list(pulls.columns) == ["t"] and 0 < len(pulls) <= 300
    table = pd.read_csv(out / "calibration.csv")
    assert list(table["quantity"]) == [
        "mean of T",
        "width of T",
        "splits accepted at one decision at 5% (%)",
        "splits accepted at 5% shared over a run (REUSE), decision 1 (%)",
        "splits accepted at 5% shared equally over 20 decisions (%)",
        "splits accepted at 3 sigma (%)",
        "splits accepted by any gain (%)",
        "T of the two runs as they were run",
    ]
    # Only the rules have a bar, and a stricter rule has a higher one.
    assert table["bar"].notna().sum() == 4
    assert table["bar"].dropna().is_monotonic_increasing
    text = (out / "calibration.md").read_text()
    assert text.startswith("# Calibration: a and b\n")
    for heading in ("## Is the test calibrated?", "## Limits", "## Data"):
        assert heading in text
    for name in ("calibration", "scatter", "distributions"):
        assert f"![{name}](figures/{name}.png)" in text
        assert (out / "figures" / f"{name}.png").exists()
        assert (out / "figures" / f"{name}.pdf").exists()


def test_calibrate_refuses_two_runs_that_are_not_one_harness(tmp_path):
    a, b = runs(tmp_path)
    settings = json.loads((b / "evaluation.json").read_text())
    (b / "evaluation.json").write_text(json.dumps(settings | {"mode": "all_tools"}))
    with pytest.raises(SystemExit, match="one harness; these differ in mode"):
        calibrate.run(options(a, b, tmp_path / "cal"))
