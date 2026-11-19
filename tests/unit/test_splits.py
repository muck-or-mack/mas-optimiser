"""Unit tests of ``splits``: quotas, the baseline read, the draw and the committed split."""

import csv
import json

import pytest

import mas_optimiser
from mas_optimiser import cli
from mas_optimiser.benchmarks.enterpriseops import runner, splits

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
    """A fake evaluations/<experiment> with one full evaluation per domain."""
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


@pytest.mark.parametrize(
    "setting", [{"model": "model-b"}, {"mode": "plus_5_tools"}, {"harness": "other"}]
)
def test_read_baseline_refuses_domains_run_with_different_settings(tmp_path, setting):
    root = tmp_path / "exp"
    write_index(
        root,
        [
            evaluation(root, "e1", "itsm", {"a": 3}, started="2026-10-27T05:00:00"),
            evaluation(
                root, "e2", "csm", {"b": 3}, started="2026-10-27T06:00:00", **setting
            ),
        ],
    )
    with pytest.raises(ValueError, match="differ in model, mode, harness or attempts"):
        splits.read_baseline(root)


def test_read_baseline_refuses_domains_run_with_different_attempts(tmp_path):
    root = tmp_path / "exp"
    write_index(
        root,
        [
            evaluation(root, "e1", "itsm", {"a": 3}, started="2026-10-27T05:00:00"),
            evaluation(
                root, "e2", "csm", {"b": 1}, attempts=1, started="2026-10-27T06:00:00"
            ),
        ],
    )
    with pytest.raises(ValueError, match="differ in model, mode, harness or attempts"):
        splits.read_baseline(root)


def test_read_baseline_orders_evaluations_by_start_time_not_by_index_order(tmp_path):
    root = tmp_path / "exp"
    write_index(
        root,
        [
            evaluation(root, "new", "itsm", {"a": 3}, started="2026-10-27T06:00:00"),
            evaluation(root, "old", "itsm", {"a": 0}, started="2026-10-27T05:00:00"),
        ],
    )
    assert splits.read_baseline(root).evaluations == {"itsm": "new"}


def test_read_baseline_reads_start_times_with_their_offsets(tmp_path):
    # When the clocks go back, 01:10 GMT comes half an hour after 01:40 BST.
    root = tmp_path / "exp"
    write_index(
        root,
        [
            evaluation(
                root, "bst", "itsm", {"a": 0}, started="2026-10-25T01:40:00+01:00"
            ),
            evaluation(
                root, "gmt", "itsm", {"a": 3}, started="2026-10-25T01:10:00+00:00"
            ),
        ],
    )
    assert splits.read_baseline(root).evaluations == {"itsm": "gmt"}


def test_read_baseline_keeps_reruns_whose_overrides_change_nothing_measured(tmp_path):
    root = tmp_path / "exp"
    write_index(
        root,
        [
            evaluation(root, "first", "itsm", {"a": 0}, started="2026-10-27T05:00:00"),
            evaluation(
                root,
                "second",
                "itsm",
                {"a": 3},
                started="2026-10-27T06:00:00",
                overrides="domain=itsm concurrency=8 trace=False",
            ),
            evaluation(
                root,
                "other-model",
                "itsm",
                {"a": 1},
                started="2026-10-27T07:00:00",
                overrides="model=model-b",
            ),
        ],
    )
    assert splits.read_baseline(root).evaluations == {"itsm": "second"}


def test_read_baseline_refuses_a_domain_left_without_a_baseline(tmp_path):
    root = tmp_path / "exp"
    write_index(
        root,
        [
            evaluation(root, "e1", "csm", {"a": 3}, started="2026-10-27T05:00:00"),
            evaluation(
                root,
                "e2",
                "itsm",
                {"b": 3},
                started="2026-10-27T06:00:00",
                overrides="model=model-b",
            ),
        ],
    )
    with pytest.raises(ValueError, match="for itsm"):
        splits.read_baseline(root)


def test_read_baseline_names_the_columns_an_older_index_lacks(tmp_path):
    root = tmp_path / "exp"
    root.mkdir()
    (root / "index.csv").write_text("evaluation,started,domain,limit,repeats\n")
    with pytest.raises(
        ValueError, match="has no mode, model, harness, label, overrides, attempts"
    ):
        splits.read_baseline(root)


def test_draw_is_deterministic_whatever_the_order_tasks_arrive_in():
    groups = {f"t{i:02d}": ("itsm", "none") for i in range(20)}
    groups |= {f"u{i:02d}": ("csm", "all") for i in range(10)}
    first = splits.draw(groups, (0.2, 0.4, 0.4), 0, set())
    reversed_groups = dict(reversed(list(groups.items())))
    assert splits.draw(reversed_groups, (0.2, 0.4, 0.4), 0, set()) == first
    assert splits.draw(groups, (0.2, 0.4, 0.4), 1, set()) != first
    assert sorted(first) == sorted(groups)
    counts = {s: list(first.values()).count(s) for s in splits.SETS}
    assert counts == {"training": 6, "validation": 12, "test": 12}


def test_draw_shares_out_every_group_in_the_proportions():
    # Ten tasks of one domain and twenty of another: every set holds the two
    # domains one to two, as the whole does.
    groups = {f"a{i:02d}": ("itsm",) for i in range(10)}
    groups |= {f"b{i:02d}": ("csm",) for i in range(20)}
    assignment = splits.draw(groups, (0.2, 0.4, 0.4), 0, set())
    for set_name, itsm, csm in (
        ("training", 2, 4),
        ("validation", 4, 8),
        ("test", 4, 8),
    ):
        drawn = [t for t, s in assignment.items() if s == set_name]
        assert sum(t.startswith("a") for t in drawn) == itsm
        assert sum(t.startswith("b") for t in drawn) == csm


def test_draw_with_nothing_to_balance_is_one_plain_shuffle():
    # One group: only the totals follow the proportions.
    groups = {f"a{i:02d}": () for i in range(10)}
    groups |= {f"b{i:02d}": () for i in range(20)}
    assignment = splits.draw(groups, (0.2, 0.4, 0.4), 0, set())
    counts = {s: list(assignment.values()).count(s) for s in splits.SETS}
    assert counts == {"training": 6, "validation": 12, "test": 12}


def test_overflow_shares_what_is_left_in_the_validation_to_test_ratio():
    # Seven of ten tasks seen leaves three for validation and test, shared
    # 0.7 to 0.1: exactly 2.625 and 0.375, so all three go to validation.
    groups = {f"t{i}": ("hr", "all") for i in range(10)}
    seen = {f"t{i}" for i in range(7)}
    assignment = splits.draw(groups, (0.2, 0.7, 0.1), 0, seen)
    rest = sorted(assignment[t] for t in groups if t not in seen)
    assert rest == ["validation", "validation", "validation"]


def test_seen_tasks_go_to_training_and_overflow_shrinks_the_other_sets():
    groups = {f"t{i}": ("hr", "all") for i in range(5)}
    seen = {"t0", "t1", "t2"}
    assignment = splits.draw(groups, (0.2, 0.4, 0.4), 0, seen)
    assert all(assignment[t] == "training" for t in seen)
    rest = sorted(assignment[t] for t in groups if t not in seen)
    assert rest == ["test", "validation"]


KEYS = {"name", "seed", "proportions", "balance", "source", "seen", *splits.SETS}


def test_build_split_records_what_was_drawn_and_each_sets_tasks(monkeypatch):
    monkeypatch.setattr(mas_optimiser, "code_version", lambda: "v9.9.9-0-gabc")
    groups = {f"i{i:02d}": ("itsm",) for i in range(15)}
    groups |= {f"c{i:02d}": ("csm",) for i in range(10)}
    split = splits.build_split(
        "trial", groups, (0.2, 0.4, 0.4), 7, ("domain",), {"i03"}, {"dataset": "d"}
    )
    assert set(split) == KEYS
    assert split["name"] == "trial" and split["seed"] == 7
    assert split["balance"] == ["domain"]
    assert split["source"] == {"dataset": "d", "code_version": "v9.9.9-0-gabc"}
    assert split["seen"] == ["i03"] and "i03" in split["training"]
    assert [len(split[s]) for s in splits.SETS] == [5, 10, 10]
    drawn = split["training"] + split["validation"] + split["test"]
    assert sorted(drawn) == sorted(groups)


@pytest.mark.parametrize(
    "proportions",
    [(0.5, 0.4, 0.4), (1.2, -0.1, -0.1), (float("nan"),) * 3, (0.5, 0.5)],
)
def test_build_split_rejects_proportions_that_are_not_three_shares_of_one(proportions):
    with pytest.raises(ValueError, match="summing to one"):
        splits.build_split("x", {"a": ()}, proportions, 0, (), set(), {})


def test_build_split_rejects_seen_tasks_that_are_not_among_the_tasks():
    with pytest.raises(ValueError, match="not among the tasks"):
        splits.build_split("x", {"a": ()}, (0.2, 0.4, 0.4), 0, (), {"zzz"}, {})


def test_the_committed_split_puts_every_task_in_one_set_and_holds_no_result():
    split = splits.load_split("haiku-20-40-40")
    assert set(split) == KEYS
    sets = [split[s] for s in splits.SETS]
    assert [len(s) for s in sets] == [62, 125, 121]
    assert len(set().union(*sets)) == 308
    assert set(split["seen"]) <= set(split["training"])


@pytest.fixture
def command(tmp_path, monkeypatch):
    """The split command over a fake dataset and experiment, with its folders in tmp_path."""
    monkeypatch.setattr(splits, "EVALUATIONS", tmp_path / "evaluations")
    monkeypatch.setattr(splits, "SPLITS_DIR", tmp_path / "configs" / "splits")
    tasks = {"itsm": many("itsm", 2, 2, 6), "csm": many("csm", 1, 1, 3)}
    monkeypatch.setattr(
        runner, "load_tasks", lambda domain, mode: tasks.get(domain, {})
    )
    write_experiment(tmp_path / "evaluations" / "exp", tasks)

    def run(*extra):
        cli.main(["split", "--name", "trial", "--seed", "3", *extra])
        return json.loads((splits.SPLITS_DIR / "trial.json").read_text())

    return run


def test_split_command_reads_the_dataset_and_balances_the_domains_by_default(
    command, capsys
):
    written = command()
    out = capsys.readouterr().out
    assert f"wrote {splits.SPLITS_DIR / 'trial.json'}" in out
    assert "training: 3 tasks" in out and "validation: 6 tasks" in out
    assert written["balance"] == ["domain"] and written["seen"] == []
    assert written["source"]["dataset"] == runner.HF_DATASET
    assert written["proportions"] == {"training": 0.2, "validation": 0.4, "test": 0.4}
    assert sum(t.startswith("csm") for t in written["training"]) == 1
    assert splits.load_split("trial") == written


def test_split_command_shuffles_plainly_when_nothing_is_balanced(command):
    written = command("--balance")
    assert written["balance"] == []
    assert [len(written[s]) for s in splits.SETS] == [3, 6, 6]


def test_split_command_balances_difficulty_from_a_baseline_run_without_writing_it(
    command,
):
    written = command("--balance", "difficulty", "domain", "--experiment", "exp")
    assert written["balance"] == ["domain", "difficulty"]
    assert written["source"]["experiment"] == "exp"
    assert written["source"]["evaluations"] == {"itsm": "e1-itsm", "csm": "e2-csm"}
    assert set(written) == KEYS
    assert sum(len(written[s]) for s in splits.SETS) == 15


@pytest.mark.parametrize(
    "extra", [["--balance", "difficulty"], ["--experiment", "exp"]]
)
def test_split_command_wants_an_experiment_with_difficulty_and_only_then(
    command, extra
):
    with pytest.raises(SystemExit, match="only then"):
        command(*extra)
    assert not (splits.SPLITS_DIR / "trial.json").exists()


def test_split_command_places_seen_tasks_in_training(command, tmp_path):
    seen = tmp_path / "seen.yaml"
    seen.write_text("itsm_none_05: read by hand\ncsm_all_00: crashed\n")
    written = command("--seen", str(seen))
    assert written["seen"] == ["csm_all_00", "itsm_none_05"]
    assert {"csm_all_00", "itsm_none_05"} <= set(written["training"])


def test_split_command_takes_an_empty_file_of_seen_tasks_as_none(command, tmp_path):
    seen = tmp_path / "seen.yaml"
    seen.write_text("# nothing read yet\n")
    assert command("--seen", str(seen))["seen"] == []


@pytest.mark.parametrize(
    "seen, extra, message",
    [
        (None, ["--seen", "missing.yaml"], "no file of seen tasks"),
        ("- itsm_none_05\n", [], "must map each task id"),
        ("itsm_none_05:\n", [], "must map each task id"),
        ('itsm_none_05: "unclosed\n', [], "is not valid YAML"),
        ("nowhere_01: read\n", [], "not among the tasks"),
        (None, ["--proportions", "0.5", "0.5", "0.5"], "error: proportions"),
        (
            None,
            ["--balance", "difficulty", "--experiment", "nowhere"],
            "error: .*index.csv",
        ),
    ],
)
def test_split_command_stops_on_bad_input_without_writing(
    command, tmp_path, seen, extra, message
):
    if seen is not None:
        (tmp_path / "seen.yaml").write_text(seen)
        extra = ["--seen", str(tmp_path / "seen.yaml")]
    with pytest.raises(SystemExit, match=message):
        command(*extra)
    assert not (splits.SPLITS_DIR / "trial.json").exists()


@pytest.mark.parametrize("name", ["trial.json", "../escape", "_x", "a b"])
def test_split_command_refuses_a_malformed_name(command, name):
    with pytest.raises(SystemExit, match="split name"):
        cli.main(["split", "--name", name])


def test_split_command_never_redraws_an_existing_split(command):
    first = command()
    with pytest.raises(SystemExit, match="never redrawn"):
        command("--seed", "4")
    assert json.loads((splits.SPLITS_DIR / "trial.json").read_text()) == first
