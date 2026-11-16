"""Unit tests of ``stats``: per-task scores, pairing, the gate's tests and the noise floor."""

import csv
import json
import math

import numpy as np
import pytest

from mas_optimiser.stats import gate, outcomes

COLUMNS = [
    "task_id",
    "attempt",
    "success",
    "checks_passed",
    "checks_total",
    "error",
    "cost_usd",
    "seconds",
]


def write_evaluation(folder, domain, rows):
    """Write a minimal evaluation: ``evaluation.json`` and ``tasks.csv`` rows.

    The run is the folder's name up to its first hyphen; every attempt costs
    a cent and takes ten seconds.
    """
    folder.mkdir(parents=True)
    settings = {
        "domain": domain,
        "run": folder.name.split("-")[0],
        "mode": "oracle",
        "model": {"llm_model": "m"},
        "harness": "default",
        "attempts": 2,
    }
    (folder / "evaluation.json").write_text(json.dumps(settings))
    with open(folder / "tasks.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for task, attempt, success, passed, total in rows:
            writer.writerow(
                {
                    "task_id": task,
                    "attempt": attempt,
                    "success": success,
                    "checks_passed": passed,
                    "checks_total": total,
                    "error": "",
                    "cost_usd": 0.01,
                    "seconds": 10.0,
                }
            )
    return folder


ROWS = [
    ("t1", 1, "True", 3, 3),
    ("t1", 2, "False", 2, 3),
    ("t1", 3, "False", 1, 3),
    ("t2", 1, "False", 0, 2),
    ("t2", 2, "False", 1, 2),
    ("t2", 3, "True", 2, 2),
    ("t3", 1, "True", 0, 0),
]


def test_task_scores_averages_each_outcome_over_attempts(tmp_path):
    folder = write_evaluation(tmp_path / "e", "itsm", ROWS)
    success = outcomes.task_scores(folder, "success")
    checks = outcomes.task_scores(folder, "checks")
    assert success == {"t1": pytest.approx(1 / 3), "t2": pytest.approx(1 / 3)}
    assert checks == {"t1": pytest.approx(2 / 3), "t2": pytest.approx(0.5)}


def test_task_scores_skips_tasks_with_no_checks(tmp_path):
    folder = write_evaluation(tmp_path / "e", "itsm", ROWS)
    assert "t3" not in outcomes.task_scores(folder, "success")


def test_task_scores_rejects_an_unknown_outcome(tmp_path):
    folder = write_evaluation(tmp_path / "e", "itsm", ROWS)
    with pytest.raises(ValueError, match="outcome"):
        outcomes.task_scores(folder, "latency")


def two_sides(tmp_path):
    a = [
        write_evaluation(
            tmp_path / "a-itsm",
            "itsm",
            [("i1", 1, "False", 1, 2), ("i2", 1, "True", 2, 2)],
        ),
        write_evaluation(tmp_path / "a-csm", "csm", [("c1", 1, "False", 0, 2)]),
    ]
    b = [
        write_evaluation(
            tmp_path / "b-itsm",
            "itsm",
            [("i1", 1, "True", 2, 2), ("i2", 1, "True", 2, 2)],
        ),
        write_evaluation(tmp_path / "b-csm", "csm", [("c1", 1, "False", 1, 2)]),
    ]
    return a, b


def test_paired_orders_tasks_by_domain_and_keeps_differences(tmp_path):
    a, b = two_sides(tmp_path)
    cmp = outcomes.paired(a, b, "checks")
    assert cmp.tasks == ("c1", "i1", "i2")
    assert cmp.domains == ("csm", "itsm", "itsm")
    assert cmp.d == pytest.approx([0.5, 0.5, 0.0])
    assert cmp.domain("itsm").tasks == ("i1", "i2")
    assert cmp.domain("itsm").d == pytest.approx([0.5, 0.0])


def test_paired_filters_by_the_split_subset(tmp_path):
    a, b = two_sides(tmp_path)
    split = tmp_path / "split.json"
    split.write_text(
        json.dumps(
            {
                "tasks": {
                    "i1": {"set": "validation"},
                    "i2": {"set": "test"},
                    "c1": {"set": "validation"},
                }
            }
        )
    )
    cmp = outcomes.paired(a, b, "success", split=split, subset="validation")
    assert cmp.tasks == ("c1", "i1")
    assert cmp.d == pytest.approx([0.0, 1.0])


def test_paired_refuses_sides_with_different_tasks(tmp_path):
    a, b = two_sides(tmp_path)
    b.append(write_evaluation(tmp_path / "b-hr", "hr", [("h1", 1, "True", 1, 1)]))
    with pytest.raises(ValueError, match="one side only"):
        outcomes.paired(a, b, "checks")


D = np.array([1 / 3, 0.0, -1 / 3, 1 / 3, 0.0])


def test_mean_test_is_the_one_sided_t_test_of_the_differences():
    result = gate.mean_test(D, alpha=0.05)
    assert result.n == 5
    assert result.mean == pytest.approx(1 / 15)
    assert result.se == pytest.approx(0.27889 / math.sqrt(5), rel=1e-3)
    assert result.t == pytest.approx(0.5345, rel=1e-3)
    # Student's t with four degrees of freedom: the 0.95 quantile is 2.132 in
    # any table, and the tail beyond 0.5345 is 0.3107.
    assert result.x0 == pytest.approx(2.1318, rel=1e-4)
    assert result.p == pytest.approx(0.3107, rel=1e-3)
    assert result.lower == pytest.approx(result.mean - result.x0 * result.se)
    assert not result.accept


def test_mean_test_accepts_a_clear_gain():
    assert gate.mean_test(np.array([0.2, 0.3, 0.25, 0.3, 0.2, 0.25])).accept


def test_mean_test_refuses_degenerate_input():
    with pytest.raises(ValueError, match="two tasks"):
        gate.mean_test(np.array([0.1]))
    with pytest.raises(ValueError, match="standard error is zero"):
        gate.mean_test(np.zeros(4))


def test_sign_test_is_the_binomial_upper_tail():
    result = gate.sign_test(np.array([1.0, 0.5, 0.2, -0.1, 0.0, 0.0]))
    assert (result.n_plus, result.n_minus) == (3, 1)
    assert result.p == pytest.approx(5 / 16)
    assert not result.accept
    assert gate.sign_test(np.zeros(3)).p == 1.0


def test_bernstein_bound_sits_below_the_mean_and_tightens_with_delta():
    rng = np.random.default_rng(0)
    d = rng.choice([-1 / 3, 0.0, 1 / 3], size=125, p=[0.1, 0.8, 0.1])
    loose = gate.bernstein_bound(d, delta=0.05)
    tight = gate.bernstein_bound(d, delta=0.01)
    assert tight < loose < d.mean()
    assert tight >= -1
    assert gate.bernstein_bound(np.ones(125), delta=0.05) == pytest.approx(
        0.737, abs=0.002
    )
    with pytest.raises(ValueError, match="in \\[-1, 1\\]"):
        gate.bernstein_bound(np.array([0.0, 1.5]), delta=0.05)


def test_the_noise_floor_is_the_gain_found_with_the_chosen_power():
    se, n = 0.0196, 125
    floor = gate.detectable(se, n)
    assert floor == pytest.approx(2.500 * se, rel=1e-3)
    assert gate.power(se, n, floor) == pytest.approx(0.8, abs=1e-6)
    assert gate.power(se, n, 0.0) == pytest.approx(0.05, abs=1e-6)
    assert gate.detectable(se, n, power=0.9) > floor
    # With very many tasks Student's t becomes the normal curve, and the floor
    # the sum of two normal quantiles.
    assert gate.detectable(se, 10**6) == pytest.approx(2.4865 * se, rel=1e-3)
