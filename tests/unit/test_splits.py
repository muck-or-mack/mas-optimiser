import csv
import json

import pytest

from mas_optimiser import cli
from mas_optimiser.benchmarks.enterpriseops import splits


def write_experiment(root, domains, attempts=3):
    """A fake runs/<experiment> with one full evaluation per domain and a limited one."""
    root.mkdir(parents=True)
    index = [
        {
            "evaluation": "e0-itsm",
            "started": "2026-10-20",
            "domain": "itsm",
            "limit": "5",
        },
    ]
    for i, (domain, outcomes) in enumerate(domains.items()):
        evaluation = f"e{i + 1}-{domain}"
        index.append(
            {
                "evaluation": evaluation,
                "started": f"2026-10-2{i + 1}",
                "domain": domain,
                "limit": "",
            }
        )
        (root / evaluation).mkdir()
        with (root / evaluation / "tasks.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["task_id", "attempt", "success"])
            writer.writeheader()
            for task_id, passed in outcomes.items():
                for attempt in range(1, attempts + 1):
                    writer.writerow(
                        {
                            "task_id": task_id,
                            "attempt": attempt,
                            "success": attempt <= passed,
                        }
                    )
    with (root / "index.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(index[0]))
        writer.writeheader()
        writer.writerows(index)


def many(domain, n_all, n_some, n_none):
    tasks = {}
    for kind, n, passed in (
        ("all", n_all, 3),
        ("some", n_some, 1),
        ("none", n_none, 0),
    ):
        for i in range(n):
            tasks[f"{domain}_{kind}_{i:02d}"] = passed
    return tasks


def test_quotas_sum_to_n_and_round_by_largest_remainder():
    assert splits.quotas(10, (0.2, 0.4, 0.4)) == [2, 4, 4]
    assert splits.quotas(11, (0.2, 0.4, 0.4)) == [2, 5, 4]
    assert splits.quotas(1, (0.2, 0.4, 0.4)) == [0, 1, 0]
    assert splits.quotas(0, (0.2, 0.4, 0.4)) == [0, 0, 0]


def test_read_baseline_takes_the_latest_full_evaluation_of_each_domain(tmp_path):
    write_experiment(tmp_path / "exp", {"itsm": {"a": 3, "b": 1, "c": 0}})
    baseline = splits.read_baseline(tmp_path / "exp")
    assert baseline.evaluations == {"itsm": "e1-itsm"}
    assert baseline.tasks == {
        "a": ("itsm", "all"),
        "b": ("itsm", "some"),
        "c": ("itsm", "none"),
    }


def test_draw_is_deterministic_and_puts_every_task_in_one_set():
    tasks = {t: ("itsm", o) for t, o in [(f"t{i}", "none") for i in range(20)]}
    first = splits.draw(tasks, (0.2, 0.4, 0.4), 0, set())
    again = splits.draw(tasks, (0.2, 0.4, 0.4), 0, set())
    other = splits.draw(tasks, (0.2, 0.4, 0.4), 1, set())
    assert first == again
    assert first != other
    assert sorted(first) == sorted(tasks)
    counts = {s: list(first.values()).count(s) for s in splits.SETS}
    assert counts == {"training": 4, "validation": 8, "test": 8}


def test_forced_tasks_go_to_training_and_overflow_shrinks_the_other_sets():
    tasks = {f"t{i}": ("hr", "all") for i in range(5)}
    forced = {"t0", "t1", "t2"}
    assignment = splits.draw(tasks, (0.2, 0.4, 0.4), 0, forced)
    assert all(assignment[t] == "training" for t in forced)
    rest = sorted(assignment[t] for t in tasks if t not in forced)
    assert rest == ["test", "validation"]


def test_build_split_records_provenance_counts_and_every_task(tmp_path):
    write_experiment(
        tmp_path / "exp",
        {"itsm": many("itsm", 2, 3, 10), "csm": many("csm", 1, 1, 8)},
    )
    split = splits.build_split(
        "trial", tmp_path / "exp", (0.2, 0.4, 0.4), 7, {"itsm_none_03": "inspected"}
    )
    assert split["name"] == "trial" and split["seed"] == 7
    assert split["source"]["evaluations"] == {"itsm": "e1-itsm", "csm": "e2-csm"}
    assert split["tasks"]["itsm_none_03"] == {
        "domain": "itsm",
        "outcome": "none",
        "set": "training",
        "forced": True,
    }
    assert sum(split["counts"]["training"].values()) == 5
    assert split["counts"]["validation"]["itsm/none"] == 4
    assert len(split["tasks"]) == 25


def test_build_split_rejects_unknown_forced_tasks_and_bad_proportions(tmp_path):
    write_experiment(tmp_path / "exp", {"itsm": {"a": 3}})
    with pytest.raises(ValueError, match="not in"):
        splits.build_split("x", tmp_path / "exp", (0.2, 0.4, 0.4), 0, {"zzz": "why"})
    with pytest.raises(ValueError, match="summing to one"):
        splits.build_split("x", tmp_path / "exp", (0.5, 0.4, 0.4), 0, {})


def test_split_command_writes_the_file_and_prints_the_sizes(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(splits, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(splits, "SPLITS_DIR", tmp_path / "configs" / "splits")
    monkeypatch.setattr(splits, "FORCED_FILE", tmp_path / "configs" / "forced.yaml")
    write_experiment(tmp_path / "runs" / "exp", {"itsm": many("itsm", 2, 2, 6)})
    cli.main(["split", "--experiment", "exp", "--name", "trial", "--seed", "3"])
    out = capsys.readouterr().out
    path = tmp_path / "configs" / "splits" / "trial.json"
    written = json.loads(path.read_text())
    assert f"wrote {path}" in out and "training: 1 task (itsm/none 1)" in out
    assert written["proportions"] == {"training": 0.2, "validation": 0.4, "test": 0.4}
    assert splits.load_split("trial") == written
