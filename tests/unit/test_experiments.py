"""Unit tests of the experiment and model config loading in ``runner``."""

import argparse

import pytest

from mas_optimiser.benchmarks.enterpriseops import runner


def test_config_names_resolve_to_files_in_configs():
    path = runner.config_path("experiments", "smoke")
    assert path == runner.CONFIGS / "experiments" / "smoke.yaml"
    assert path.exists()
    assert runner.config_path("models", "haiku-5-5").exists()


def test_config_paths_are_used_as_given(tmp_path):
    given = tmp_path / "mine.yaml"
    assert runner.config_path("experiments", str(given)) == given
    assert runner.config_path("experiments", "sub/other.yaml") == runner.Path(
        "sub/other.yaml"
    )


def test_unknown_config_name_lists_the_known_ones():
    with pytest.raises(SystemExit, match="smoke"):
        runner.config_path("experiments", "nope")


def test_experiment_file_defaults_and_overrides():
    no_overrides = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    settings = runner.load_experiment("smoke", no_overrides)
    assert settings["model"] == "haiku-5-5"
    assert (settings["domain"], settings["mode"], settings["limit"]) == (
        ["itsm"],
        "oracle",
        5,
    )
    assert settings["trace"] is True and settings["thinking_summary"] is False
    assert settings["experiment"] == "smoke" and settings["overrides"] == {}

    overridden = runner.load_experiment(
        "smoke", no_overrides | {"limit": 1, "trace": False}
    )
    assert (overridden["limit"], overridden["trace"]) == (1, False)
    assert overridden["domain"] == ["itsm"]
    assert overridden["overrides"] == {"limit": 1, "trace": False}


def test_experiment_file_is_validated(tmp_path):
    no_overrides = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    typo = tmp_path / "typo.yaml"
    typo.write_text("model: haiku-5-5\ndomain: itsm\nlimti: 3\n")
    with pytest.raises(SystemExit, match="limti"):
        runner.load_experiment(str(typo), no_overrides)

    no_domain = tmp_path / "no_domain.yaml"
    no_domain.write_text("model: haiku-5-5\n")
    with pytest.raises(SystemExit, match="domain"):
        runner.load_experiment(str(no_domain), no_overrides)
    # The command line can supply what the file leaves out.
    assert runner.load_experiment(str(no_domain), no_overrides | {"domain": "hr"})[
        "domain"
    ] == ["hr"]

    bad_mode = tmp_path / "bad_mode.yaml"
    bad_mode.write_text("model: haiku-5-5\ndomain: itsm\nmode: all_tools\n")
    with pytest.raises(SystemExit, match="mode"):
        runner.load_experiment(str(bad_mode), no_overrides)

    domains = tmp_path / "domains.yaml"
    domains.write_text("model: haiku-5-5\ndomain: [itsm, csm, hrr]\n")
    with pytest.raises(SystemExit, match="hrr"):
        runner.load_experiment(str(domains), no_overrides)
    domains.write_text("model: haiku-5-5\ndomain: [itsm, csm]\n")
    assert runner.load_experiment(str(domains), no_overrides)["domain"] == [
        "itsm",
        "csm",
    ]

    with pytest.raises(SystemExit, match="label"):
        runner.load_experiment("smoke", no_overrides | {"label": "after fix"})
    with pytest.raises(SystemExit, match="attempts"):
        runner.load_experiment("smoke", no_overrides | {"attempts": 0})
    settings = runner.load_experiment("smoke", no_overrides | {"concurrency": 2})
    assert (settings["attempts"], settings["concurrency"]) == (1, 2)

    with pytest.raises(SystemExit, match="split"):
        runner.load_experiment("smoke", no_overrides | {"split": "haiku-20-40-40:all"})
    with pytest.raises(SystemExit, match="split"):
        runner.load_experiment("smoke", no_overrides | {"split": "training"})
    assert (
        runner.load_experiment("smoke", no_overrides | {"split": "s:training"})["split"]
        == "s:training"
    )


def test_evaluation_name_says_what_was_run():
    no_overrides = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    started = runner.datetime(2026, 10, 27, 9, 30, 12)
    plain = runner.load_experiment("smoke", no_overrides | {"limit": None})
    assert (
        runner.evaluation_name(plain, "itsm", started) == "20261027-093012-itsm-limit5"
    )
    changed = runner.load_experiment(
        "smoke",
        no_overrides | {"model": "configs/models/haiku-5-5.yaml", "label": "retry"},
    )
    assert (
        runner.evaluation_name(changed, "csm", started)
        == "20261027-093012-csm-retry-model=haiku-5-5-limit5"
    )
    one_set = runner.load_experiment(
        "smoke", no_overrides | {"split": "haiku-20-40-40:training", "limit": None}
    )
    assert (
        runner.evaluation_name(one_set, "hr", started)
        == "20261027-093012-hr-training-limit5"
    )
    chosen = runner.load_experiment(
        "smoke", no_overrides | {"task": "task_a", "limit": None}
    )
    assert chosen["task"] == ["task_a"]
    assert (
        runner.evaluation_name(chosen, "itsm", started)
        == "20261027-093012-itsm-tasks1-limit5"
    )


def test_a_task_setting_must_name_tasks():
    no_overrides = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    with pytest.raises(SystemExit, match="task must be"):
        runner.load_experiment("smoke", no_overrides | {"task": [7]})


def test_index_gets_one_row_per_evaluation_and_a_rescore_replaces_it(tmp_path):
    runner.write_index_row(tmp_path, {"evaluation": "a", "successes": 1})
    runner.write_index_row(tmp_path, {"evaluation": "b", "successes": 2})
    lines = (tmp_path / "index.csv").read_text().splitlines()
    assert lines == ["evaluation,successes", "a,1", "b,2"]
    # The row of an evaluation rescored later replaces its old row, in the
    # file's own columns; a column the row lacks stays empty.
    runner.write_index_row(tmp_path, {"evaluation": "a", "successes": 3, "new": 9})
    lines = (tmp_path / "index.csv").read_text().splitlines()
    assert lines == ["evaluation,successes", "b,2", "a,3"]


def test_totals_count_attempts_tasks_errors_and_sum_the_rest():
    rows = [
        {"task_id": "a", "success": True, "upstream_success": True, "error": ""},
        {"task_id": "a", "success": False, "upstream_success": True, "error": ""},
        {"task_id": "b", "success": True, "upstream_success": False, "error": "down"},
    ]
    for row in rows:
        row |= {k: 1 for k in runner.SUMMED if k != "upstream_successes"}
    counted = runner.totals(rows, tasks=2, attempts=2)
    assert counted["total_attempts"] == 3 and counted["successes"] == 2
    assert counted["tasks_passed_every_attempt"] == 1 and counted["errors"] == 1
    assert counted["upstream_successes"] == 2 and counted["checks_passed"] == 3
    assert list(counted) == [
        "tasks",
        "attempts",
        "total_attempts",
        "successes",
        "tasks_passed_every_attempt",
        "errors",
        *runner.SUMMED,
    ]


def test_index_row_takes_the_settings_as_evaluation_json_keeps_them():
    settings = {
        "evaluation": "20261103-080328-itsm",
        "started": "2026-11-03T08:03:28+00:00",
        "domain": "itsm",
        "mode": "oracle",
        "model": {"llm_model": "claude-haiku-5-5", "provider": "anthropic"},
        "label": None,
        "overrides": {"limit": 5},
        "limit": 5,
        "mas_optimiser_version": "v0.7.1",
    }
    row = runner.index_row(settings, {"successes": 58})
    assert row == {
        "evaluation": "20261103-080328-itsm",
        "started": "2026-11-03T08:03:28+00:00",
        "domain": "itsm",
        "mode": "oracle",
        "model": "claude-haiku-5-5",
        "harness": "default",
        "label": "",
        "overrides": "limit=5",
        "limit": 5,
        "code_version": "v0.7.1",
        "successes": 58,
    }


def test_servers_that_do_not_answer_stop_the_run_with_the_compose_command():
    with pytest.raises(SystemExit, match="docker compose"):
        runner.check_servers({"http://127.0.0.1:9"})


def test_run_passes_the_chosen_tasks_from_the_command_line(monkeypatch):
    seen = []
    monkeypatch.setattr(runner, "load_model", lambda path: (None, {}))
    monkeypatch.setattr(runner.tracing, "configured", lambda: False)
    monkeypatch.setattr(runner.mas_optimiser, "code_version", lambda: "v0")
    monkeypatch.setattr(
        runner, "evaluate", lambda experiment, *a, **k: seen.append(experiment["task"])
    )
    parser = argparse.ArgumentParser()
    runner.add_arguments(parser)
    runner.run(parser.parse_args(["smoke", "--task", "task_a", "--task", "task_b"]))
    assert seen == [["task_a", "task_b"]]


def test_run_takes_the_code_version_once_before_any_evaluation(monkeypatch):
    calls, versions = [], iter(["v0.5.0-1-gaaaaaaa", "v0.5.0-2-gbbbbbbb-dirty"])
    monkeypatch.setattr(runner.mas_optimiser, "code_version", lambda: next(versions))
    monkeypatch.setattr(runner, "load_model", lambda path: (None, {}))
    monkeypatch.setattr(runner.tracing, "configured", lambda: False)
    monkeypatch.setattr(
        runner, "evaluate", lambda *args, **kwargs: calls.append(kwargs["version"])
    )
    parser = argparse.ArgumentParser()
    runner.add_arguments(parser)
    runner.run(parser.parse_args(["baseline-haiku"]))
    assert calls == ["v0.5.0-1-gaaaaaaa"] * 3
