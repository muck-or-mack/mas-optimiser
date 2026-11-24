"""The report of an experiment of the loop, on a made-up experiment's files."""

import argparse
import csv
import json

import pytest

from mas_optimiser.evolve import report

COLUMNS = [
    "task_id",
    "attempt",
    "success",
    "checks_passed",
    "checks_total",
    "error",
    "service_error",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "seconds",
]


def write_job(folder, job, passed, lost=()):
    """A job's one evaluation: a task per entry of ``passed`` (checks of 4), each attempt 1,000 tokens in, a cent and 36 seconds.

    Args:
        folder: The experiment's folder.
        job: The job's name.
        passed: The checks each task's attempt passed.
        lost: The tasks whose attempt the model service never answered.
    """
    evaluation = folder / "enterpriseops" / "jobs" / job / "itsm"
    evaluation.mkdir(parents=True)
    (evaluation / "evaluation.json").write_text(json.dumps({"domain": "itsm"}))
    with (evaluation / "tasks.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for n, checks in enumerate(passed, 1):
            task = f"h{n}"
            writer.writerow(
                [task, 1, checks == 4, checks, 4, "", task in lost, 1000, 100, 0.01, 36]
            )


@pytest.fixture
def experiment(tmp_path):
    """An experiment of two rounds: a change kept in the first, a candidate that no longer ran in the second."""
    folder = tmp_path / "evolutions" / "an-experiment" / "a-split"
    domain = folder / "enterpriseops"
    for job, passed in {
        "base_training": [2, 2],
        "base_validation": [2, 2, 2, 2],
        "r0A_smoke": [4],
        "r0A_filter": [3, 3],
        "r0A_rest": [4, 2],
        "r0A_training": [3, 3],
        "r1A_smoke": [0],
        "heldout_t0": [2, 2, 2, 2],
        "heldout_t1": [3, 3, 4, 2],
    }.items():
        write_job(folder, job, passed, lost={"h1"} if job == "r1A_smoke" else ())
    settings = {
        "experiment": "an-experiment",
        "split": "a-split",
        "model": "haiku-5-5",
        "proposer_model": None,
        "rounds": 2,
        "mas_optimiser_version": "v0",
    }
    (folder / "experiment.json").write_text(json.dumps(settings))
    # A million tokens read and a hundred thousand written, at the model
    # file's prices of 0.10 and 0.50 dollars a million.
    calls = [
        {
            "model": "haiku-5-5",
            "input_tokens": 60_000,
            "output_tokens": 0,
            "seconds": 90,
        },
        {
            "model": "haiku-5-5",
            "input_tokens": 0,
            "output_tokens": 100_000,
            "seconds": 90,
        },
    ]
    calls += [dict(calls[0], input_tokens=94_000, seconds=0)] * 10
    (folder / "search_usage.jsonl").write_text(
        "\n".join(json.dumps(call) for call in calls) + "\n"
    )
    state = {
        "promotions": 1,
        "trajectory": [
            {"t": 0, "S": 0.5, "commit": "aaa"},
            {"t": 1, "S": 0.75, "commit": "bbb"},
            {"t": 2, "S": 0.75, "commit": "bbb"},
        ],
    }
    (domain / "frontier.json").write_text(json.dumps(state))
    history = [
        {"t": 0, "variant": "-", "edit_id": None, "outcome": "BASELINE"},
        {
            "t": 0,
            "variant": "A",
            "edit_id": "C1",
            "component": "prompt",
            "hypothesis": "it helps",
        },
        {
            "t": 1,
            "variant": "A",
            "edit_id": "C1",
            "component": "agent",
            "hypothesis": "it helps more",
        },
    ]
    (domain / "history.jsonl").write_text(
        "\n".join(json.dumps(record) for record in history) + "\n"
    )
    kept = {"variant": "A", "level": 0.0125, "accept": True, "n": 4}
    kept |= {"mean": 0.25, "sd": 0.1, "se": 0.05, "t": 5.0, "x0": 4.5, "p": 0.008}
    kept |= {"filter": {"tasks": 2, "gain_points": 25.0, "passed": True}}
    stopped = {"variant": "A", "level": 0.003, "accept": False, "stopped": "smoke_fail"}
    for name, decision in (("r0", kept), ("r1", stopped)):
        (domain / name).mkdir()
        (domain / name / "decisions.json").write_text(json.dumps([decision]))
    (domain / "test.csv").write_text(
        "round,commit,validation,test,missing_attempts\n"
        "0,aaa,0.5,0.5,0\n"
        "1,bbb,0.75,0.75,0\n"
    )
    return folder


def test_parts_add_up_each_part_and_price_the_search_roles(experiment):
    spent = report.parts(experiment).set_index("part")
    assert list(spent.index) == [part for part, _ in report.PARTS] + [report.SEARCH]
    # Two candidates were tried on one task each, and the attempt the model
    # service never answered is no run.
    assert spent.loc["the one-task trial of each candidate", "runs"] == 1
    assert spent.loc["the candidates on the validation tasks", "runs"] == 4
    assert spent.loc[
        "each harness kept, on the test tasks", "dollars"
    ] == pytest.approx(0.08)
    search = spent.loc[report.SEARCH]
    assert (search["runs"], search["input_tokens"]) == (12, 1_000_000)
    assert search["dollars"] == pytest.approx(0.10 + 0.05)
    assert search["hours"] == pytest.approx(0.05)
    assert spent["share_of_dollars_percent"].sum() == pytest.approx(100)


def test_rounds_say_where_each_candidate_ended_and_give_the_gates_numbers(experiment):
    first, second = report.rounds(experiment).to_dict("records")
    assert (first["round"], first["changed"]) == (1, "prompt")
    assert first["ended_at"] == "accepted by the gate" and first["accepted"]
    assert first["filter_gain_points"] == 25.0
    assert (first["gain_points"], first["standard_error_points"]) == (25.0, 5.0)
    assert first["false_acceptances_allowed_percent"] == pytest.approx(1.25)
    # A candidate that did not reach the gate has no numbers of it.
    assert (second["round"], second["ended_at"]) == (2, "its one-task trial")
    assert (
        second["gain_points"] is None or second["gain_points"] != second["gain_points"]
    )


def test_harnesses_give_each_ones_scores_and_its_gain_on_the_test_tasks(experiment):
    start, changed = report.harnesses(experiment).to_dict("records")
    assert (start["test_score"], start["test_tasks"]) == (50.0, 4)
    assert (
        start["test_gain_points"] is None
        or start["test_gain_points"] != start["test_gain_points"]
    )
    assert start["test_dollars_an_attempt"] == pytest.approx(0.01)
    # The four test tasks rose by 25, 25, 50 and 0 points: a mean of 25, a
    # standard error of 10.2, and 3.18 standard errors either way at 95%.
    assert changed["test_gain_points"] == pytest.approx(25.0)
    assert changed["test_gain_interval_low"] == pytest.approx(-7.48, abs=0.01)
    assert changed["test_gain_interval_high"] == pytest.approx(57.48, abs=0.01)
    assert changed["test_gain_p"] == pytest.approx(0.046, abs=0.001)
    assert changed["test_attempts_fully_passed_percent"] == 25.0


def test_the_command_writes_the_tables_the_figure_and_a_page_to_read(
    experiment, capsys
):
    report.run(argparse.Namespace(folder=experiment))
    out = experiment / "report"
    assert f"Report in {out / 'report.md'}" in capsys.readouterr().out
    written = {path.name for path in out.rglob("*") if path.is_file()}
    assert written == {
        "report.md",
        "harnesses.csv",
        "rounds.csv",
        "parts.csv",
        "scores.csv",
        "scores.pdf",
        "scores.png",
    }
    page = (out / "report.md").read_text()
    assert "2 of 2 rounds are decided; changes kept: 1." in page
    assert "| +25.0 " in page and "-7.5 to +57.5" in page
    assert "its one-task trial" in page
    # The figure's numbers: the score steps up where the change was kept.
    scores = list(csv.DictReader((out / "figures" / "scores.csv").open()))
    assert [row["validation_score"] for row in scores] == ["50.0", "75.0", "75.0"]
    assert [row["test_score"] for row in scores] == ["50.0", "75.0", "75.0"]


def test_an_experiment_still_going_is_reported_without_its_test_scores(experiment):
    (experiment / "enterpriseops" / "test.csv").unlink()
    report.run(argparse.Namespace(folder=experiment))
    page = (experiment / "report" / "report.md").read_text()
    assert "The test step has not run yet" in page
    assert not (experiment / "report" / "harnesses.csv").exists()
    assert "## What it cost" in page


def test_a_folder_that_holds_no_experiment_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="no experiment of the loop"):
        report.run(argparse.Namespace(folder=tmp_path))
