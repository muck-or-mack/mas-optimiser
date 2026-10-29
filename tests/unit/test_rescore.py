import asyncio
import csv
import json
import sqlite3

import pytest

from mas_optimiser.benchmarks.enterpriseops import checks, rescore, runner, seeds

SCHEMA = """CREATE TABLE incident (
    incident_id VARCHAR(16) NOT NULL, priority VARCHAR(16), state VARCHAR(16),
    PRIMARY KEY (incident_id)
);
"""
SEED = """-- two incidents
INSERT INTO incident VALUES ('INC_001', 'low', 'new');
INSERT INTO incident VALUES ('INC_002', 'high', 'new');
"""
TASK = "task_1"
REQUIREMENTS = [
    {
        "text": "raise INC_001 to high",
        "kind": "change",
        "table": "incident",
        "anchor": {"incident_id": "INC_001"},
        "cells": {"priority": "high"},
        "sql": "SELECT COUNT(*) FROM incident WHERE incident_id = 'INC_001' "
        "AND priority = 'high'",
        "expected": 1,
        "comparison": "equals",
        "source": "new",
    },
    {
        "text": "leave INC_002 alone",
        "kind": "stay",
        "table": "incident",
        "anchor": {"incident_id": "INC_002"},
        "cells": {"state": "new"},
        "sql": "SELECT COUNT(*) FROM incident WHERE incident_id = 'INC_002' "
        "AND state = 'new'",
        "expected": 1,
        "comparison": "equals",
        "source": ["Check one", "Check, two"],
    },
]
CALLS = [
    {
        "tool_name": "update_incident",
        "arguments": {"incident_id": "INC_001", "priority": "high"},
        "result": {"success": True, "result": {"updated_at": "2026-10-27 05:30:01"}},
    }
]


@pytest.fixture
def evaluation(tmp_path, monkeypatch):
    """An evaluation folder of one task, with its seed and check file stubbed in."""
    seed_path = tmp_path / "seed.sql"
    seed_path.write_text(SEED)
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "itsm.sql").write_text(SCHEMA)
    monkeypatch.setattr(seeds, "SCHEMAS_DIR", schemas)
    config = {
        "gym_servers_config": [
            {"mcp_server_url": "http://server", "seed_database_file": str(seed_path)}
        ]
    }
    monkeypatch.setattr(runner, "load_tasks", lambda domain, mode: {TASK: config})
    monkeypatch.setattr(runner, "check_servers", lambda urls: None)
    monkeypatch.setattr(
        checks,
        "load_checks",
        lambda: {
            TASK: {
                "task_id": TASK,
                "requirements": REQUIREMENTS,
                "original_checks": {
                    "Check one": {"disposition": "kept"},
                    "Check, two": {"disposition": "kept"},
                    "Check three": {"disposition": "excluded", "evidence": "a copy"},
                },
            }
        },
    )
    folder = tmp_path / "eval"
    (folder / "tasks" / TASK).mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({"domain": "itsm", "mode": "oracle"}))
    return folder


def add_attempt(folder, attempt, run, success):
    (folder / "tasks" / TASK / f"attempt-{attempt}.json").write_text(
        json.dumps({"runs": [run]})
    )
    tasks = folder / "tasks.csv"
    new = not tasks.exists()
    with tasks.open("a", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["task_id", "attempt", "success", "upstream_success"]
        )
        if new:
            writer.writeheader()
        writer.writerow(
            {
                "task_id": TASK,
                "attempt": attempt,
                "success": success,
                "upstream_success": success,
            }
        )


def read_csv(path):
    with path.open() as handle:
        return list(csv.DictReader(handle))


def final_rows(statements):
    """The incident table after ``statements``, as the server would give it back."""
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA + SEED)
    for statement in statements:
        connection.execute(statement)
    names = ["incident_id", "priority", "state"]
    return {
        "incident": [
            dict(zip(names, row, strict=True))
            for row in connection.execute("SELECT * FROM incident ORDER BY 1")
        ]
    }


def test_masked_ignores_timestamps_and_key_order():
    assert rescore.masked({"b": 1, "at": "2026-10-27 05:30:01.123+00:00"}) == (
        rescore.masked({"at": "2026-10-29T11:02:59Z", "b": 1})
    )
    assert rescore.masked({"b": 1}) != rescore.masked({"b": 2})


def test_reissue_stops_at_the_first_result_that_differs(monkeypatch):
    issued = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def connect(self):
            return True

        async def call_tool(self, name, arguments):
            issued.append(name)
            stamp = "2026-10-29 10:00:00"
            return {"success": True, "result": {"updated_at": stamp}}

    monkeypatch.setattr(rescore, "MCPClient", Client)
    calls = CALLS + [
        {"tool_name": "second", "arguments": {}, "result": {"success": False}},
        {"tool_name": "third", "arguments": {}, "result": {}},
    ]
    gym = {"mcp_server_url": "http://server"}
    assert asyncio.run(rescore.reissue(CALLS, gym, "db")) is None
    issued.clear()
    assert asyncio.run(rescore.reissue(calls, gym, "db")) == 1
    assert issued == ["update_incident", "second"]


def test_a_reissued_attempt_is_scored_saved_and_checked_against_the_server(
    evaluation, monkeypatch
):
    state = final_rows(
        [
            "UPDATE incident SET priority = 'high' WHERE incident_id = 'INC_001'",
            "UPDATE incident SET state = 'closed' WHERE incident_id = 'INC_002'",
        ]
    )
    monkeypatch.setattr(
        rescore,
        "server_final_state",
        lambda *args: {"differs": None, "state": state, "server_counts": [1, 0]},
    )
    add_attempt(evaluation, 1, {"tool_results": CALLS}, "True")
    counts = asyncio.run(rescore.rescore(evaluation, concurrency=2))
    assert counts == {"reissued": 1, "checks fail, benchmark pass": 1}
    saved = json.loads(
        (evaluation / "tasks" / TASK / "attempt-1.changes.json").read_text()
    )
    assert saved["tool_calls"] == 1
    assert saved["changes"]["incident"]["updated"][1] == {
        "key": {"incident_id": "INC_002"},
        "changes": {"state": ["new", "closed"]},
    }
    out = json.loads(
        (evaluation / "tasks" / TASK / "attempt-1.checks.json").read_text()
    )
    assert out["server_agrees"] is True
    assert out["explanation"].splitlines()[0] == (
        "FAIL: 1 of 2 requirements met, 1 stay broken."
    )
    (row,) = read_csv(evaluation / "checks.csv")
    assert row["state"] == "reissued" and row["checks_passed"] == "False"
    assert row["stays_broken"] == "1" and row["server_agrees"] == "True"
    requirements = read_csv(evaluation / "requirements.csv")
    assert [(r["met"], r["audit"], r["server_count"]) for r in requirements] == [
        ("True", "", "1"),
        ("False", "stay_broken", "0"),
    ]
    assert requirements[0]["source"] == "new"
    assert requirements[1]["source"] == "Check one; Check, two"


def test_saved_changes_are_used_without_the_server(evaluation, monkeypatch):
    def server_final_state(*args):
        raise AssertionError("the server should not be asked")

    monkeypatch.setattr(rescore, "server_final_state", server_final_state)
    verdicts = {"Check one": {"passed": True}, "Check three": {"passed": False}}
    add_attempt(
        evaluation,
        1,
        {"tool_results": CALLS, "verification_results": verdicts},
        "False",
    )
    change = {
        "key": {"incident_id": "INC_001"},
        "changes": {"priority": ["low", "high"]},
    }
    (evaluation / "tasks" / TASK / "attempt-1.changes.json").write_text(
        json.dumps(
            {
                "changes": {
                    "incident": {"inserted": [], "deleted": [], "updated": [change]}
                }
            }
        )
    )
    counts = asyncio.run(rescore.rescore(evaluation, concurrency=1))
    assert counts == {"saved": 1, "checks pass, benchmark fail": 1}
    out = json.loads(
        (evaluation / "tasks" / TASK / "attempt-1.checks.json").read_text()
    )
    assert out["server_agrees"] is None
    assert out["explanation"] == (
        "PASS: all 2 requirements met.\nThe benchmark's own checks fail this "
        'attempt, on "Check three" (excluded here: a copy).'
    )


def test_an_attempt_that_raised_fails_and_one_not_reproduced_is_unscored(
    evaluation, monkeypatch
):
    monkeypatch.setattr(rescore, "server_final_state", lambda *args: {"differs": 0})
    add_attempt(evaluation, 1, {"error": "KeyError('tool')"}, "False")
    add_attempt(evaluation, 2, {"tool_results": CALLS}, "False")
    counts = asyncio.run(rescore.rescore(evaluation, concurrency=2))
    assert counts == {
        "raised": 1,
        "not reproduced": 1,
        "checks fail, benchmark fail": 1,
    }
    rows = read_csv(evaluation / "checks.csv")
    assert [(r["state"], r["checks_passed"]) for r in rows] == [
        ("raised", "False"),
        ("not reproduced", ""),
    ]
    assert not (evaluation / "tasks" / TASK / "attempt-2.changes.json").exists()
    assert read_csv(evaluation / "requirements.csv") == []
