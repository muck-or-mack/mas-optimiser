"""The ``status`` command: one question to a model, and what its service says back."""

import argparse
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
        status.run(argparse.Namespace(model=["a", "b"]))
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
    status.run(argparse.Namespace(model=["a"]))
