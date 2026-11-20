"""Unit tests of the experiment and model config loading in ``runner``."""

import pytest

from mas_optimiser.benchmarks.enterpriseops import runner
from tests.unit.test_markdown import cells


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
        "all_tools",
        5,
    )
    assert settings["trace"] is True and settings["thinking_summary"] is False
    assert settings["prompt_caching"] is True
    assert settings["model_timeout"] == 300
    assert settings["harness"] == "default"
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
    bad_mode.write_text("model: haiku-5-5\ndomain: itsm\nmode: no_tools\n")
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
        runner.load_experiment(
            "smoke", no_overrides | {"split": "haiku-20-40-40:training"}
        )["split"]
        == "haiku-20-40-40:training"
    )
    # A split's name alone names all three of its sets.
    named = runner.load_experiment("smoke", no_overrides | {"split": "haiku-20-40-40"})
    assert named["split"] == "haiku-20-40-40"


def test_run_name_is_the_start_minute_and_the_label():
    no_overrides = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    started = runner.datetime(2026, 10, 27, 9, 30, 12)
    plain = runner.load_experiment("smoke", no_overrides | {"limit": None})
    assert runner.run_name(plain, started) == "2026-10-27-0930"
    assert runner.run_name(plain, started, seconds=True) == "2026-10-27-093012"
    labelled = runner.load_experiment("smoke", no_overrides | {"label": "retry"})
    assert runner.run_name(labelled, started) == "2026-10-27-0930-retry"


def test_write_index_row_adds_a_new_column_without_losing_old_rows(tmp_path):
    runner.write_index_row(tmp_path, {"evaluation": "a", "started": "1", "n": 1})
    runner.write_index_row(
        tmp_path, {"evaluation": "b", "started": "2", "n": 2, "run": "r"}
    )
    with open(tmp_path / "index.csv", newline="") as f:
        rows = list(runner.csv.DictReader(f))
    assert [r["evaluation"] for r in rows] == ["a", "b"]
    assert rows[0]["run"] == "" and rows[1]["run"] == "r"


def test_write_index_row_reads_an_index_under_the_old_names(tmp_path):
    (tmp_path / "index.csv").write_text("evaluation,upstream_successes\na,3\n")
    runner.write_index_row(tmp_path, {"evaluation": "b", "benchmark_successes": 4})
    lines = (tmp_path / "index.csv").read_text().splitlines()
    assert lines == ["evaluation,benchmark_successes", "a,3", "b,4"]


def test_write_run_summary_pools_the_domains(tmp_path):
    run_dir = tmp_path / "2026-11-03-0803"
    for domain, successes, cost in (("csm", 81, 3.1), ("itsm", 58, 2.5)):
        (run_dir / domain).mkdir(parents=True)
        (run_dir / domain / "evaluation.json").write_text(
            runner.json.dumps(
                {
                    "mode": "oracle",
                    "model": {"llm_model": "m"},
                    "label": None,
                    "overrides": {},
                    "limit": None,
                    "split": None,
                    "attempts": 3,
                    "started": f"2026-11-03T08:{'26' if domain == 'csm' else '03'}:00",
                    "finished": "2026-11-03T09:00:00",
                    "totals": {
                        "tasks": 100,
                        "attempts": 3,
                        "total_attempts": 300,
                        "successes": successes,
                        "tasks_passed_every_attempt": 10,
                        "errors": 0,
                        "checks_passed": 500,
                        "checks_total": 1000,
                        "cost_usd": cost,
                        "seconds": 1800,
                    },
                }
            )
        )
    record = runner.write_run_summary(run_dir, "baseline")
    assert record["totals"]["successes"] == 139
    assert record["totals"]["attempts"] == 3
    assert record["totals"]["cost_usd"] == pytest.approx(5.6)
    assert record["started"] == "2026-11-03T08:03:00"
    assert list(record["domains"]) == ["itsm", "csm"]
    text = (run_dir / "summary.md").read_text()
    table = [line for line in text.split("\n") if line.startswith("|")]
    assert cells(table[2])[:3] == ["all", "200", "139 of 600"]
    assert cells(table[3])[:3] == ["itsm", "100", "58 of 300"]
    assert cells(table[4])[:3] == ["csm", "100", "81 of 300"]
    # Cost and hours: two decimals from 1 upwards, two significant figures below.
    assert cells(table[2])[-2:] == ["5.60", "1.00"]
    assert cells(table[3])[-2:] == ["2.50", "0.50"]
    # The table's columns line up in the source.
    assert len({len(line) for line in table}) == 1
    assert (
        runner.json.loads((run_dir / "run.json").read_text())["run"]
        == "2026-11-03-0803"
    )
