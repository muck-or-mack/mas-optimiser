"""The ``status`` command: one question to a model, and what its service says back."""

import argparse
import json
import os
import time
from types import SimpleNamespace

import pytest

from mas_optimiser import status

STATED = {
    "x-ratelimit-limit-tokens": "444000",
    "x-ratelimit-remaining-tokens": "-7841",
    "x-ratelimit-limit-requests": "2664",
    "x-ratelimit-remaining-requests": "2663",
}


def client(reply):
    """A stand-in for a model's client of the OpenAI style, which gives ``reply`` or raises it."""

    def invoke(prompt):
        if isinstance(reply, Exception):
            raise reply
        return reply

    return SimpleNamespace(root_client=SimpleNamespace(), invoke=invoke)


def test_ask_gives_the_answer_and_the_allowance_the_service_states(monkeypatch):
    llm = client(SimpleNamespace(response_metadata={"headers": STATED}))
    monkeypatch.setattr(status.models, "client", lambda name: llm)
    row = status.ask("a-model")
    assert (row["model"], row["answers"], row["said"]) == ("a-model", True, "")
    assert (row["tokens_a_minute"], row["tokens_left"]) == ("444000", "-7841")
    assert (row["requests_a_minute"], row["requests_left"]) == ("2664", "2663")
    # One try, a limit on it, and the reply's headers asked for.
    assert (llm.root_client.max_retries, llm.root_client.timeout) == (0, status.LIMIT)
    assert llm.include_response_headers is True


def test_ask_says_what_was_raised_without_its_address(monkeypatch):
    refusal = RuntimeError("429 at https://a-resource.example/openai\nrate limit")
    refusal.response = SimpleNamespace(headers=STATED)
    monkeypatch.setattr(status.models, "client", lambda name: client(refusal))
    row = status.ask("a-model")
    assert row["answers"] is False
    assert row["said"] == "RuntimeError: 429 at <address> rate limit"
    # A refusal for the allowance being used up still states the allowance.
    assert row["tokens_a_minute"] == "444000"


def test_ask_reports_a_model_file_that_cannot_be_used(monkeypatch):
    def missing(name):
        raise RuntimeError("A_KEY is not set; add it to .env (see .env.example)")

    monkeypatch.setattr(status.models, "client", missing)
    row = status.ask("a-model")
    assert row["answers"] is False and "A_KEY is not set" in row["said"]
    assert row["tokens_a_minute"] is None


def test_a_service_that_states_no_allowance_leaves_it_empty(monkeypatch):
    plain = SimpleNamespace(invoke=lambda prompt: SimpleNamespace(response_metadata={}))
    monkeypatch.setattr(status.models, "client", lambda name: plain)
    row = status.ask("a-model")
    assert row["answers"] is True and row["tokens_a_minute"] is None
    assert (plain.max_retries, plain.default_request_timeout) == (0, status.LIMIT)


def test_the_command_prints_one_table_and_ends_in_an_error_for_a_silent_model(
    monkeypatch, capsys
):
    rows = {
        "a": {"model": "a", "answers": True, "said": "", "seconds": 1.2}
        | {"tokens_a_minute": "444000", "tokens_left": "-7841"}
        | {"requests_a_minute": None, "requests_left": None},
        "b": {"model": "b", "answers": False, "said": "Refused: no", "seconds": 0.3}
        | dict.fromkeys(status.ALLOWANCE),
    }
    monkeypatch.setattr(status, "load_dotenv", lambda path: None)
    monkeypatch.setattr(status, "ask", rows.__getitem__)
    with pytest.raises(SystemExit, match="no answer from b"):
        status.run(argparse.Namespace(target=["a", "b"]))
    table = [
        line for line in capsys.readouterr().out.split("\n") if line.startswith("|")
    ]
    assert [cell.strip() for cell in table[2].split("|")[1:6]] == [
        "a",
        "yes",
        "1.2",
        "444,000",
        "-7,841",
    ]
    assert "| b " in table[3] and "Refused: no" in table[3]
    # With every model answering the command ends without an error.
    status.run(argparse.Namespace(target=["a"]))


def experiment(tmp_path):
    """A made-up experiment at its second job, with four of its six attempts' records.

    Args:
        tmp_path: Where to make it.

    Returns:
        The experiment's folder.
    """
    folder = tmp_path / "evolutions" / "an-experiment" / "a-split-first"
    tasks = folder / "enterpriseops" / "jobs" / "base_validation" / "itsm" / "tasks"
    settings = {"experiment": "an-experiment", "split": "a-split", "label": "first"}
    settings |= {"model": "a-model", "proposer_model": None, "rounds": 5}
    settings |= {"mas_optimiser_version": "v0", "candidates": 1, "attempts": 2}
    settings |= {"filter_share": 0.5, "feedback": "outcome"}
    settings |= {"tasks": {"training": 2, "validation": 3, "test": 2}}
    tasks.mkdir(parents=True)
    (folder / "experiment.json").write_text(json.dumps(settings))
    planned = {"tasks": 3, "attempts": 2}
    (tasks.parent.parent / "job.json").write_text(json.dumps(planned))
    # Each answered call used 900 tokens in and 100 out.
    call = {"type": "ai_message", "usage_metadata": {"input_tokens": 900}}
    call["usage_metadata"]["output_tokens"] = 100
    now = time.time()
    for task, attempt, calls, error, age in (
        ("t1", 1, 3, None, 30),
        ("t1", 2, 2, "the attempt ran past its limit of 60 seconds", 300),
        ("t2", 1, 5, None, 2 * status.RECENT),
    ):
        execution = {"conversation_flow": [call] * calls}
        execution |= {"error": error} if error else {}
        (tasks / task).mkdir(exist_ok=True)
        path = tasks / task / f"attempt-{attempt}.json"
        path.write_text(json.dumps({"runs": [execution]}))
        os.utime(path, (now - age, now - age))
    # An attempt whose record is still being written, and its changes file.
    (tasks / "t2" / "attempt-2.json").write_text('{"runs": [')
    (tasks / "t2" / "attempt-1.changes.json").write_text("{}")
    # One call by a search role in the last hour, and one long before.
    stamp = "%Y-%m-%d %H:%M:%S"
    (folder / "search_usage.jsonl").write_text(
        "".join(
            json.dumps({"time": time.strftime(stamp, time.localtime(now - age))} | used)
            + "\n"
            for age, used in (
                (60, {"input_tokens": 4000, "output_tokens": 1000}),
                (9000, {"input_tokens": 7, "output_tokens": 7}),
            )
        )
    )
    return folder


def test_an_experiments_progress_is_read_from_its_files(tmp_path):
    state = status.progress(experiment(tmp_path))
    assert state["experiment"] == "an-experiment on a-split (first)"
    assert (state["models"], state["version"]) == (["a-model"], "v0")
    # No frontier yet: the baseline is not over.
    assert state["where"] == "the starting harness, validation tasks"
    assert (state["rounds"], state["of"], state["kept"]) == (0, 5, 0)
    # Four attempt records, one of them half-written; the job is to have six.
    assert (state["done"], state["expected"]) == (4, 6)
    assert (state["last_10"], state["last_60"]) == (3, 3)
    # Of the hour's attempts that could be read: five calls of 1,000 tokens,
    # with the search role's 5,000; one attempt ended in an error.
    assert (state["tokens"], state["errors"], state["unanswered"]) == (10_000, 1, 0)
    assert (state["search_calls"], state["idle_minutes"]) == (1, 0)
    # What is still to run. The baseline lacks its 4 training attempts and 2
    # of its 6 validation ones. A round is 1 trial attempt and the filter's
    # 2 tasks twice, 5, if its candidate is dropped there; kept, it is the
    # trial, the 6 validation attempts and the 4 training ones, 11. The test
    # is 4 attempts for the starting harness and for each harness kept.
    assert state["rest"] == (6 + 5 * 5 + 4, 6 + 5 * 11 + 4 * 6)


def test_the_status_of_an_experiment_sets_its_traffic_against_the_services_limit(
    tmp_path, monkeypatch, capsys
):
    folder = experiment(tmp_path)
    row = {"model": "a-model", "answers": False, "said": "Refused", "seconds": 0.3}
    row |= dict.fromkeys(status.ALLOWANCE) | {"tokens_a_minute": "1000"}
    monkeypatch.setattr(status, "load_dotenv", lambda path: None)
    monkeypatch.setattr(status, "ask", lambda name: row)
    # The experiment's model did not answer, which is reported and no more.
    status.run(argparse.Namespace(target=[str(folder)]))
    said = capsys.readouterr().out
    lines = {
        cells[1].strip(): cells[2].strip()
        for cells in (line.split("|") for line in said.splitlines())
        if len(cells) == 4
    }
    assert lines["experiment"] == "an-experiment on a-split (first)"
    assert lines["this job"] == "4 of 6 attempts"
    # Two attempts to go at three an hour.
    assert lines["time left for this job"] == "0 h 40 min at the last hour's pace"
    # The rest at three attempts an hour: 35 attempts at the least, 85 at
    # the most.
    assert lines["the rest, at the least"].startswith("11 h 40 min (35 attempts): ")
    assert lines["the rest, at the most"].startswith("28 h 20 min (85 attempts): ")
    assert lines["attempts finished"] == "3 in the last 10 minutes, 3 in the last hour"
    assert lines["last file written"] == "less than a minute ago"
    assert lines["in the last hour"] == (
        "ended in an error: 1; unanswered by a service: 0; calls by the search roles: 1"
    )
    # 10,000 tokens in the hour are 167 a minute, of the 1,000 allowed.
    assert lines["its traffic"] == (
        "167 tokens a minute over the last hour, 17% of the 1,000 its service allows"
    )
    assert "| a-model " in said and "Refused" in said
    # No task is named.
    assert "t1" not in said and "t2" not in said


def test_a_folder_that_holds_no_experiment_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="holds no experiment of the loop"):
        status.run(argparse.Namespace(target=[str(tmp_path)]))
