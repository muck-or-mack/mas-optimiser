import csv
import json

import pytest

import mas_optimiser
from mas_optimiser import cli
from mas_optimiser.benchmarks.enterpriseops import splits

INDEX_COLUMNS = [
    "evaluation",
    "started",
    "domain",
    "mode",
    "model",
    "harness",
    "label",
    "overrides",
    "limit",
    "attempts",
]


def evaluation(root, name, domain, outcomes, attempts=3, **index):
    """Write one evaluation's tasks.csv and return its index row."""
    (root / name).mkdir(parents=True)
    with (root / name / "tasks.csv").open("w", newline="") as f:
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
    row = {
        "evaluation": name,
        "domain": domain,
        "mode": "oracle",
        "model": "model-a",
        "harness": "default",
        "label": "",
        "overrides": "",
        "limit": "",
        "attempts": str(attempts),
    }
    return row | index


def write_index(root, rows):
    with (root / "index.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=INDEX_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_experiment(root, domains):
    """A fake runs/<experiment> with one full evaluation per domain."""
    write_index(
        root,
        [
            evaluation(
                root,
                f"e{i}-{domain}",
                domain,
                outcomes,
                started=f"2026-10-27T0{i}:00:00",
            )
            for i, (domain, outcomes) in enumerate(domains.items(), 1)
        ],
    )


def many(domain, n_all, n_some, n_none):
    tasks = {}
    for outcome, n, passed in (
        ("all", n_all, 3),
        ("some", n_some, 1),
        ("none", n_none, 0),
    ):
        for i in range(n):
            tasks[f"{domain}_{outcome}_{i:02d}"] = passed
    return tasks


def test_quotas_sum_to_n_and_round_by_largest_remainder():
    assert splits.quotas(10, (0.2, 0.4, 0.4)) == [2, 4, 4]
    assert splits.quotas(11, (0.2, 0.4, 0.4)) == [2, 5, 4]
    assert splits.quotas(1, (0.2, 0.4, 0.4)) == [0, 1, 0]
    assert splits.quotas(0, (0.2, 0.4, 0.4)) == [0, 0, 0]


def test_quotas_break_exact_ties_towards_the_earlier_share():
    # 4 x (0.6, 0.1, 0.3) is exactly (2.4, 0.4, 1.2): training and validation
    # tie on 0.4 and training, the earlier, takes the seat. In floating point
    # 4 x 0.6 is 2.3999..., which used to hand the seat to validation.
    assert splits.quotas(4, (0.6, 0.1, 0.3)) == [3, 0, 1]


def test_read_baseline_takes_the_latest_full_evaluation_without_label_or_overrides(
    tmp_path,
):
    root = tmp_path / "exp"
    old = {"a": 0, "b": 0}
    new = {"a": 3, "b": 1}
    later = {"a": 0, "b": 3}
    write_index(
        root,
        [
            evaluation(root, "full-old", "itsm", old, started="2026-10-27T05:00:00"),
            evaluation(root, "full-new", "itsm", new, started="2026-10-27T06:00:00"),
            evaluation(
                root, "limited", "itsm", later, started="2026-10-27T07:00:00", limit="5"
            ),
            evaluation(
                root, "trial", "itsm", later, started="2026-10-27T08:00:00", label="fix"
            ),
            evaluation(
                root,
                "sonnet",
                "itsm",
                later,
                started="2026-10-27T09:00:00",
                overrides="model=sonnet",
            ),
        ],
    )
    baseline = splits.read_baseline(root)
    assert baseline.evaluations == {"itsm": "full-new"}
    assert baseline.tasks == {"a": ("itsm", "all"), "b": ("itsm", "some")}


def test_read_baseline_refuses_when_no_evaluation_qualifies(tmp_path):
    root = tmp_path / "exp"
    write_index(
        root,
        [
            evaluation(
                root, "limited", "itsm", {"a": 3}, started="2026-10-27", limit="5"
            )
        ],
    )
    with pytest.raises(ValueError, match="no full evaluation"):
        splits.read_baseline(root)


def test_read_baseline_refuses_domains_run_with_different_settings(tmp_path):
    root = tmp_path / "exp"
    write_index(
        root,
        [
            evaluation(root, "e1", "itsm", {"a": 3}, started="2026-10-27T05:00:00"),
            evaluation(
                root,
                "e2",
                "csm",
                {"b": 3},
                started="2026-10-27T06:00:00",
                model="model-b",
            ),
        ],
    )
    with pytest.raises(ValueError, match="differ in model"):
        splits.read_baseline(root)


def test_draw_is_deterministic_whatever_the_order_tasks_arrive_in():
    tasks = {f"t{i:02d}": ("itsm", "none") for i in range(20)}
    tasks |= {f"u{i:02d}": ("csm", "all") for i in range(10)}
    first = splits.draw(tasks, (0.2, 0.4, 0.4), 0, set())
    reversed_tasks = dict(reversed(list(tasks.items())))
    assert splits.draw(reversed_tasks, (0.2, 0.4, 0.4), 0, set()) == first
    assert splits.draw(tasks, (0.2, 0.4, 0.4), 1, set()) != first
    assert sorted(first) == sorted(tasks)
    counts = {s: list(first.values()).count(s) for s in splits.SETS}
    assert counts == {"training": 6, "validation": 12, "test": 12}


def test_forced_tasks_go_to_training_and_overflow_shrinks_the_other_sets():
    tasks = {f"t{i}": ("hr", "all") for i in range(5)}
    forced = {"t0", "t1", "t2"}
    assignment = splits.draw(tasks, (0.2, 0.4, 0.4), 0, forced)
    assert all(assignment[t] == "training" for t in forced)
    rest = sorted(assignment[t] for t in tasks if t not in forced)
    assert rest == ["test", "validation"]


def test_build_split_records_provenance_counts_and_every_task(tmp_path, monkeypatch):
    monkeypatch.setattr(mas_optimiser, "code_version", lambda: "v9.9.9-0-gabc")
    write_experiment(
        tmp_path / "exp",
        {"itsm": many("itsm", 2, 3, 10), "csm": many("csm", 1, 1, 8)},
    )
    split = splits.build_split(
        "trial", tmp_path / "exp", (0.2, 0.4, 0.4), 7, {"itsm_none_03": "inspected"}
    )
    assert split["name"] == "trial" and split["seed"] == 7
    assert split["strata"] == splits.STRATA_RULE
    assert split["source"] == {
        "experiment": "exp",
        "evaluations": {"itsm": "e1-itsm", "csm": "e2-csm"},
        "code_version": "v9.9.9-0-gabc",
    }
    assert split["forced_training"] == {"itsm_none_03": "inspected"}
    assert [t for t, row in split["tasks"].items() if row["forced"]] == ["itsm_none_03"]
    assert split["tasks"]["itsm_none_03"]["set"] == "training"
    assert sum(split["counts"]["training"].values()) == 5
    assert split["counts"]["validation"]["itsm/none"] == 4
    assert len(split["tasks"]) == 25


@pytest.mark.parametrize(
    "proportions",
    [(0.5, 0.4, 0.4), (1.2, -0.1, -0.1), (float("nan"),) * 3, (0.5, 0.5)],
)
def test_build_split_rejects_proportions_that_are_not_three_shares_of_one(
    tmp_path, proportions
):
    write_experiment(tmp_path / "exp", {"itsm": {"a": 3}})
    with pytest.raises(ValueError, match="summing to one"):
        splits.build_split("x", tmp_path / "exp", proportions, 0, {})


def test_build_split_rejects_unknown_forced_tasks(tmp_path):
    write_experiment(tmp_path / "exp", {"itsm": {"a": 3}})
    with pytest.raises(ValueError, match="not in"):
        splits.build_split("x", tmp_path / "exp", (0.2, 0.4, 0.4), 0, {"zzz": "why"})


def test_the_committed_split_follows_from_its_own_record():
    # A clean clone has no runs/, but each task's stratum and forced flag,
    # the seed and the proportions are in the file, and they fix the draw.
    split = splits.load_split("haiku-20-40-40")
    tasks = {t: (row["domain"], row["outcome"]) for t, row in split["tasks"].items()}
    forced = {t for t, row in split["tasks"].items() if row["forced"]}
    proportions = tuple(split["proportions"][s] for s in splits.SETS)
    assignment = splits.draw(tasks, proportions, split["seed"], forced)
    assert assignment == {t: row["set"] for t, row in split["tasks"].items()}
    assert forced == set(split["forced_training"])


@pytest.fixture
def command(tmp_path, monkeypatch):
    """The split command over a fake experiment, with its folders in tmp_path."""
    monkeypatch.setattr(splits, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(splits, "SPLITS_DIR", tmp_path / "configs" / "splits")
    monkeypatch.setattr(splits, "FORCED_FILE", tmp_path / "configs" / "forced.yaml")
    write_experiment(tmp_path / "runs" / "exp", {"itsm": many("itsm", 2, 2, 6)})
    (tmp_path / "configs").mkdir()
    splits.FORCED_FILE.write_text("itsm_none_05: read by hand\n")

    def run(*extra):
        cli.main(
            ["split", "--experiment", "exp", "--name", "trial", "--seed", "3", *extra]
        )
        return json.loads((splits.SPLITS_DIR / "trial.json").read_text())

    return run


def test_split_command_writes_the_file_and_prints_the_sizes(command, capsys):
    written = command()
    out = capsys.readouterr().out
    assert f"wrote {splits.SPLITS_DIR / 'trial.json'}" in out
    assert "training: 1 task (itsm/none 1)" in out
    assert written["proportions"] == {"training": 0.2, "validation": 0.4, "test": 0.4}
    assert splits.load_split("trial") == written


def test_split_command_reads_the_forced_list_by_default(command):
    written = command()
    assert written["forced_training"] == {"itsm_none_05": "read by hand"}
    assert written["tasks"]["itsm_none_05"] == {
        "domain": "itsm",
        "outcome": "none",
        "set": "training",
        "forced": True,
    }


def test_split_command_reads_a_forced_list_named_on_the_command_line(command, tmp_path):
    other = tmp_path / "other.yaml"
    other.write_text("itsm_all_01: crashed\n")
    written = command("--forced", str(other))
    assert written["forced_training"] == {"itsm_all_01": "crashed"}
    assert written["tasks"]["itsm_all_01"]["set"] == "training"


def test_split_command_takes_an_empty_forced_file_as_no_forced_tasks(command):
    splits.FORCED_FILE.write_text("# nothing forced\n")
    assert command()["forced_training"] == {}


@pytest.mark.parametrize(
    "setup, extra, message",
    [
        (lambda tmp: None, ["--forced", "missing.yaml"], "no forced-training file"),
        (lambda tmp: splits.FORCED_FILE.unlink(), [], "no forced-training file"),
        (
            lambda tmp: splits.FORCED_FILE.write_text("- itsm_none_05\n"),
            [],
            "must map each task id",
        ),
        (
            lambda tmp: None,
            ["--proportions", "0.5", "0.5", "0.5"],
            "error: proportions",
        ),
    ],
)
def test_split_command_stops_on_bad_input_without_writing(
    command, tmp_path, setup, extra, message
):
    setup(tmp_path)
    with pytest.raises(SystemExit, match=message):
        command(*extra)
    assert not (splits.SPLITS_DIR / "trial.json").exists()


@pytest.mark.parametrize("name", ["trial.json", "../escape", "_x", "a b"])
def test_split_command_refuses_a_malformed_name(command, name):
    with pytest.raises(SystemExit, match="split name"):
        cli.main(["split", "--experiment", "exp", "--name", name])


def test_split_command_never_redraws_an_existing_split(command):
    first = command()
    with pytest.raises(SystemExit, match="never redrawn"):
        command("--seed", "4")
    assert json.loads((splits.SPLITS_DIR / "trial.json").read_text()) == first
