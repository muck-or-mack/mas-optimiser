"""The report of an experiment of the loop, on a made-up experiment's files."""

import argparse
import csv
import json
import math
import re
import shutil

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


def write_job(folder, job, passed, lost=(), failed=()):
    """A job's one evaluation: a task per entry of ``passed`` (checks of 4), each attempt 1,000 tokens in, a cent and 36 seconds.

    Args:
        folder: The experiment's folder.
        job: The job's name.
        passed: The checks each task's attempt passed.
        lost: The tasks whose attempt the model service never answered.
        failed: The error each task's attempt ended in, in the order of
            the tasks, for as many tasks as it has entries.
    """
    evaluation = folder / "enterpriseops" / "jobs" / job / "itsm"
    evaluation.mkdir(parents=True)
    (evaluation / "evaluation.json").write_text(json.dumps({"domain": "itsm"}))
    with (evaluation / "tasks.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for n, checks in enumerate(passed, 1):
            task = f"h{n}"
            said = failed[n - 1] if n <= len(failed) else ""
            writer.writerow(
                [
                    task,
                    1,
                    checks == 4,
                    checks,
                    4,
                    said,
                    task in lost,
                    1000,
                    100,
                    0.01,
                    36,
                ]
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
    # The first round's change: two lines added and one removed, in one file.
    (domain / "r0" / "A").mkdir()
    (domain / "r0" / "A" / "diff.patch").write_text(
        "diff --git a/harness/agent.py b/harness/agent.py\n"
        "--- a/harness/agent.py\n+++ b/harness/agent.py\n"
        "@@ -1,2 +1,3 @@\n unchanged\n-old\n+new\n+newer\n"
    )
    (domain / "test.csv").write_text(
        "round,commit,validation,test,missing_attempts\n"
        "0,aaa,0.5,0.5,0\n"
        "1,bbb,0.75,0.75,0\n"
    )
    return folder


def test_parts_add_up_each_part_and_price_the_search_roles(experiment):
    spent = report.parts(experiment).set_index("part")
    # Every part but one: no candidate was run on the training tasks.
    fed = "the candidates on the training tasks, for the proposer"
    assert list(spent.index) == [part for part, _ in report.PARTS if part != fed] + [
        report.SEARCH
    ]
    assert spent.loc["each harness kept, on the training tasks", "runs"] == 2
    # A candidate that was not kept, run there for what the proposer is told.
    write_job(experiment, "r1A_training", [1, 1, 1])
    spent = report.parts(experiment).set_index("part")
    assert spent.loc[fed, "runs"] == 3
    assert spent.loc["each harness kept, on the training tasks", "runs"] == 2
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
    # The gain the decision needed: its bar of 4.5 standard errors of 5 points.
    assert first["gain_needed_points"] == pytest.approx(22.5)
    assert first["false_acceptances_allowed_percent"] == pytest.approx(1.25)
    # What it proposed: the hypothesis, and its change read from the diff.
    assert (first["hypothesis"], first["files"]) == ("it helps", "harness/agent.py")
    assert (first["lines_added"], first["lines_removed"]) == (2, 1)
    assert first["diff"] == "enterpriseops/r0/A/diff.patch"
    assert (second["files"], second["lines_added"]) == ("", 0)
    assert not isinstance(second["diff"], str)
    # A candidate that did not reach the gate has no numbers of it.
    assert (second["round"], second["ended_at"]) == (2, "its one-task trial")
    assert (
        second["gain_points"] is None or second["gain_points"] != second["gain_points"]
    )


def test_a_candidate_of_several_edits_shows_the_kind_and_hypothesis_of_each(experiment):
    second = {"t": 0, "variant": "A", "edit_id": "C2"}
    second |= {"component": "control_flow", "hypothesis": "one more turn"}
    with (experiment / "enterpriseops" / "history.jsonl").open("a") as f:
        f.write(json.dumps(second) + "\n")
    first, _ = report.rounds(experiment).to_dict("records")
    assert first["changed"] == "prompt, control_flow"
    assert first["hypothesis"] == (
        "Edit 1 (prompt): it helps Edit 2 (control_flow): one more turn"
    )


def test_rounds_give_the_proposers_own_record_of_each_drafting(experiment):
    folder = experiment / "enterpriseops" / "r0" / "A"
    # A first drafting whose first "done" was refused, and a second after the
    # reviewer sent the proposal back.
    first = ["read_trace", "edit_file", "done", "done"]
    again = ["read_file", "edit_file", "done"]
    for name, steps in (("proposal.json", first), ("proposal_r1.json", again)):
        record = {"status": "done", "log": [{"action": step} for step in steps]}
        (folder / name).write_text(json.dumps(record))
    made, bare = report.rounds(experiment).to_dict("records")
    assert (made["actions"], made["traces_opened"], made["files_read"]) == (7, 1, 1)
    assert (made["done_refused"], made["sent_back"], made["edits"]) == (1, 1, 1)
    # A candidate that left no record of its drafting has none of these.
    assert math.isnan(bare["actions"]) and math.isnan(bare["sent_back"])
    # One that declared no edit has the row RRSI's record gives it all the
    # same, of no kind: the report shows no edit, not one called "None".
    (experiment / "enterpriseops" / "r1" / "A").mkdir()
    (experiment / "enterpriseops" / "r1" / "A" / "proposal.json").write_text(
        json.dumps({"status": "no_edits", "log": [{"action": "done"}]})
    )
    history = experiment / "enterpriseops" / "history.jsonl"
    lines = [json.loads(line) for line in history.read_text().splitlines()]
    lines[2] |= {"component": None, "hypothesis": None, "bundle": 0}
    history.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    _, none = report.rounds(experiment).to_dict("records")
    assert none["edits"] == 0
    assert not isinstance(none["changed"], str)
    assert not isinstance(none["hypothesis"], str)
    report.run(argparse.Namespace(folder=experiment))
    page = (experiment / "report" / "report.md").read_text()
    section = page[page.index("## How each proposal was made") :]
    assert "| 1       | 7 " in section.split("## ")[1]


def test_rounds_give_the_gain_on_the_training_tasks_where_candidates_were_run_there(
    experiment,
):
    report.run(argparse.Namespace(folder=experiment))
    page = (experiment / "report" / "report.md").read_text()
    assert "gain on the training tasks" not in page
    record = {"delta_S_training": 0.125, "standard_error": 0.05}
    record |= {"delta_C_training": 0.0}
    folder = experiment / "enterpriseops" / "r0" / "A"
    (folder / "training.json").write_text(json.dumps(record))
    first, second = report.rounds(experiment).to_dict("records")
    assert first["training_gain_points"] == 12.5
    assert math.isnan(second["training_gain_points"])
    report.run(argparse.Namespace(folder=experiment))
    page = (experiment / "report" / "report.md").read_text()
    assert "| gain on the training tasks   |" in page and "| +12.5 " in page


def test_the_further_attempts_at_the_filters_tasks_are_a_job_the_report_knows():
    part = [name for name, pattern in report.PARTS if re.fullmatch(pattern, "r3A_more")]
    assert part == ["the candidates on the validation tasks"]
    what = [
        words.format(int(found.group(1)) + 1)
        for pattern, words, _ in report.JOBS
        if (found := re.fullmatch(pattern, "r3A_more"))
    ]
    assert what == ["round 4: further attempts at the filter's tasks"]


def test_errors_list_each_job_with_attempts_in_error_and_their_kinds(experiment):
    # Nothing in error, and an attempt its service never answered is not one.
    assert report.errors(experiment).empty
    write_job(
        experiment,
        "r1A_filter",
        [0, 0, 0, 3],
        failed=[
            "the attempt ran past its limit of 3600 seconds",
            "the attempt made more than 100 calls to the model",
            "RuntimeError('something else')",
        ],
    )
    write_job(experiment, "heldout_t2", [0, 4], failed=["KeyError('a')"])
    write_job(experiment, "r9A_rest", [0], failed=["KeyError('a')"])
    # The first round's candidate was kept and the second's was not.
    for job in ("r0A_training", "r1A_training"):
        shutil.rmtree(experiment / "enterpriseops" / "jobs" / job, ignore_errors=True)
        write_job(experiment, job, [0], failed=["KeyError('a')"])
    table = report.errors(experiment)
    # In the order of the experiment, rounds by number and the test step last.
    assert list(table["job"]) == [
        "r0A_training",
        "r1A_filter",
        "r1A_training",
        "r9A_rest",
        "heldout_t2",
    ]
    assert list(table["what"]) == [
        "round 1: the kept harness, training tasks",
        "round 2: the filter",
        "round 2: a candidate, training tasks",
        "round 10: the rest of the validation tasks",
        "the harness kept in round 2, test tasks",
    ]
    first = table.set_index("job").loc["r1A_filter"]
    assert (first.attempts, first.in_error) == (4, 3)
    assert (first.past_seconds, first.past_calls, first.other) == (1, 1, 1)


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
        "errors.csv",
        "parts.csv",
        "scores.csv",
        "scores.pdf",
        "scores.png",
    }
    page = (out / "report.md").read_text()
    assert "2 of 2 rounds are decided; changes kept: 1." in page
    assert "| +25.0 " in page and "-7.5 to +57.5" in page
    assert "| gain needed   |" in page and "| +22.5 " in page
    assert "its one-task trial" in page
    assert (
        "**Round 1** (accepted by the gate; harness/agent.py, +2 −1 lines, "
        "`enterpriseops/r0/A/diff.patch`). it helps" in page
    )
    assert "**Round 2** (its one-task trial; no change kept). it helps more" in page
    assert "No attempt of any job ended in an error." in page
    # With an attempt in error, its job is a row of the page's table.
    write_job(experiment, "r1A_filter", [0, 3], failed=["KeyError('a')"])
    report.run(argparse.Namespace(folder=experiment))
    page = (out / "report.md").read_text()
    assert "| round 2: the filter | 2          | 1          |" in page
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
