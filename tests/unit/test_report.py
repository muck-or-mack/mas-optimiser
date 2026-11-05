"""Unit tests of ``report``: the trace from a failed check to the agent's turn, the page and the summary."""

import json

from mas_optimiser.benchmarks.enterpriseops import report


def wrapped(text):
    return {"success": True, "result": {"content": [{"type": "text", "text": text}]}}


EXECUTION = {
    "overall_success": False,
    "conversation_flow": [
        {"type": "system_message", "content": "policy"},
        {
            "type": "user_message",
            "content": "Log a critical phone incident for Benjamin",
        },
        {
            "type": "ai_message",
            "content": "",
            "tool_calls": [{"name": "list_users", "args": {"first_name": "Benjamin"}}],
        },
        {
            "type": "tool_result",
            "tool_name": "list_users",
            "result": wrapped('[{"user_id": 7}]'),
        },
        {
            "type": "ai_message",
            "content": "",
            "tool_calls": [
                {
                    "name": "create_incident",
                    "args": {"caller_id": 7, "channel": "chat", "priority": "moderate"},
                }
            ],
        },
        {
            "type": "tool_result",
            "tool_name": "create_incident",
            "result": wrapped('{"incident_id": "INC_024", "channel": "chat"}'),
        },
        {
            "type": "ai_message",
            "content": "",
            "tool_calls": [
                {
                    "name": "send_notification",
                    "args": {"incident_id": "INC_024", "email": "b@c.d"},
                }
            ],
        },
        {
            "type": "tool_result",
            "tool_name": "send_notification",
            "result": {"success": False, "result": "email unknown"},
        },
        {"type": "ai_message", "content": "Logged INC_024.", "tool_calls": []},
    ],
    "verification_results": {
        "Incident": {
            "passed": False,
            "expected": 1,
            "actual": 0,
            "comparison_type": "equals",
            "query": "SELECT COUNT(*) FROM incident WHERE incident_id = 'INC_024' "
            "AND channel = 'phone' AND priority = 'critical'",
            "diagnosis": {
                "kind": "cells",
                "table": "incident",
                "rows": [
                    {
                        "row": {"incident_id": "INC_024"},
                        "condition": "incident_id = 'INC_024'",
                        "ok": True,
                    },
                    {
                        "row": {"incident_id": "INC_024"},
                        "condition": "channel = 'phone'",
                        "ok": False,
                        "actual": ["chat"],
                    },
                    {
                        "row": {"incident_id": "INC_024"},
                        "condition": "priority = 'critical'",
                        "ok": False,
                        "actual": ["moderate"],
                    },
                ],
            },
        },
        "Notification": {
            "passed": False,
            "expected": 1,
            "actual": 0,
            "comparison_type": "equals",
            "query": "SELECT COUNT(*) FROM notification WHERE incident_id = 'INC_024'",
            "diagnosis": {"kind": "no_rows", "table": "notification"},
        },
        "Task": {
            "passed": False,
            "expected": 1,
            "actual": 0,
            "comparison_type": "equals",
            "query": "SELECT COUNT(*) FROM incident_task WHERE incident_id = 'INC_024'",
            "diagnosis": {"kind": "no_rows", "table": "incident_task"},
        },
        "Caller": {"passed": True, "expected": 1, "actual": 1},
    },
}
KEYS = {
    "incident": ["incident_id"],
    "notification": ["notification_id"],
    "incident_task": ["incident_task_id"],
}
CHANGES = {
    "incident": {
        "inserted": [{"incident_id": "INC_024"}],
        "updated": [],
        "deleted": [],
    },
    "incident_knowledge": {"inserted": [{"id": 1}], "updated": [], "deleted": []},
}


def traced():
    execution = json.loads(json.dumps(EXECUTION))
    agent_calls = report.calls(execution)
    for check in execution["verification_results"].values():
        if not check["passed"]:
            check["trace"] = report.trace(check, agent_calls, KEYS)
    return execution


def test_a_tool_that_answers_with_an_error_did_not_succeed():
    refused = {
        "success": True,
        "result": {"isError": True, "content": [{"type": "text", "text": "not found"}]},
    }
    assert not report.succeeded(refused)
    assert not report.succeeded({"success": False, "error": "timeout"})
    assert report.succeeded(wrapped("{}"))


def test_calls_pair_each_tool_call_with_its_result():
    agent_calls = report.calls(EXECUTION)
    assert [(c["turn"], c["tool"], c["success"]) for c in agent_calls] == [
        (1, "list_users", True),
        (2, "create_incident", True),
        (3, "send_notification", False),
    ]
    assert '"incident_id": "INC_024"' in agent_calls[1]["result"]


def test_a_wrong_cell_is_traced_to_the_call_that_set_it():
    found = traced()["verification_results"]["Incident"]["trace"]
    assert found["step"] == "wrong"
    # The create call wrote the row; the notification call, though it names
    # the incident, does not write incidents.
    assert [c["turn"] for c in found["related"]] == [2]
    assert found["set_by"]["channel"] == {
        "turn": 2,
        "tool": "create_incident",
        "value": "chat",
    }
    assert found["set_by"]["priority"]["turn"] == 2


def test_a_missing_row_is_skipped_or_failed_by_its_calls():
    results = traced()["verification_results"]
    # send_notification was called for the row and returned an error.
    assert results["Notification"]["trace"]["step"] == "failed"
    # No call wrote incident_task at all.
    assert results["Task"]["trace"]["step"] == "skipped"


def test_the_attempt_page_shows_request_turns_changes_and_traced_checks():
    text = report.attempt_text("task_x", 1, traced(), CHANGES)
    assert text.startswith("# task_x, attempt 1: FAIL (1 of 4 checks passed)")
    assert "## Request\n\nLog a critical phone incident for Benjamin" in text
    # A turn is a heading; each call sits under it with its arguments one
    # per line and what came back beneath.
    assert (
        "### Turn 2\n\n`create_incident` → ok\n\n"
        "```\ncaller_id = 7\nchannel   = chat\npriority  = moderate\n```\n\n"
        "returned\n\n```\nincident_id = INC_024\nchannel     = chat\n```" in text
    )
    assert "`send_notification` → ERROR" in text
    assert "returned\n\n```\nemail unknown\n```" in text
    assert "### Turn 4\n\n> Logged INC_024." in text
    assert (
        "- incident: 1 inserted\n- incident_knowledge: 1 inserted\n"
        "- no check of this task mentions: incident_knowledge" in text
    )
    assert (
        "Row `incident_id INC_024`: not in the seed, inserted at turn 2 by `create_incident`."
        in text
    )
    assert "| incident_id = 'INC_024' | INC_024 | ok |  |" in text
    assert (
        "| channel = 'phone' | chat | WRONG | turn 2, `create_incident(channel='chat')` |"
        in text
    )
    assert (
        "No row inserted or updated in `notification`.\n"
        "- turn 3, `send_notification`: failed" in text
    )
    assert (
        "No row inserted or updated in `incident_task`; no call wrote `incident_task`."
        in text
    )
    # A missing row is listed as the check wants it.
    assert "| incident_id = 'INC_024' | no row |" in text


def test_a_long_argument_does_not_hide_the_ones_after_it():
    execution = traced()
    call = execution["conversation_flow"][4]["tool_calls"][0]
    call["args"] = {"description": "word " * 200, "channel": "chat"}
    text = report.attempt_text("task_x", 1, execution, CHANGES, max_chars=40)
    # Forty characters of the value, then the mark that it was cut.
    assert "description = " + "word " * 8 + " ..." in text
    assert "channel     = chat" in text


def test_a_list_in_an_answer_is_shown_one_item_per_line():
    lines = report._fields(
        {"users": [{"user_id": n} for n in range(12)], "total": 12}, 40
    )
    assert lines[:2] == ["users = 12 items", '  {"user_id": 0}']
    assert lines[10:] == ['  {"user_id": 9}', "  ... 2 more", "total = 12"]


def test_an_updated_row_shows_its_value_before_the_attempt():
    execution = traced()
    changes = {
        "incident": {
            "inserted": [],
            "updated": [
                {
                    "key": {"incident_id": "INC_024"},
                    "changes": {"channel": ["email", "chat"]},
                }
            ],
            "deleted": [],
        }
    }
    text = report.attempt_text("task_x", 1, execution, changes)
    assert (
        "Row `incident_id INC_024`: in the seed, updated at turn 2 by `create_incident`."
        in text
    )
    assert (
        "| Check wants | Before | After the attempt |  | Where the value came from |"
        in text
    )
    assert (
        "| channel = 'phone' | email | chat | WRONG | turn 2, `create_incident(channel='chat')` |"
        in text
    )
    assert "| incident_id = 'INC_024' | unchanged |  | ok |  |" in text


def test_cell_and_change_rows_carry_the_trace():
    execution = traced()
    rows = report.cell_rows("task_x", 1, execution["verification_results"])
    assert [(r["condition"], r["ok"], r["turn"], r["tool"]) for r in rows] == [
        ("incident_id = 'INC_024'", True, "", ""),
        ("channel = 'phone'", False, 2, "create_incident"),
        ("priority = 'critical'", False, 2, "create_incident"),
    ]
    queries = [r.get("query", "") for r in execution["verification_results"].values()]
    changed = report.change_rows("task_x", 1, CHANGES, queries)
    assert [(c["table"], c["inserted"], c["mentioned"]) for c in changed] == [
        ("incident", 1, True),
        ("incident_knowledge", 1, False),
    ]


def test_summary_counts_steps_and_names_the_worst_tasks():
    execution = traced()
    results = execution["verification_results"]
    records = [
        {
            "task_id": "t1",
            "success": False,
            "checks_passed": 1,
            "checks_total": 4,
            "tool_errors": 1,
        },
        {
            "task_id": "t2",
            "success": True,
            "checks_passed": 2,
            "checks_total": 2,
            "tool_errors": 0,
        },
    ]
    check_rows = [
        {"task_id": "t1", "passed": False, "step": "wrong", "table": "incident"},
        {"task_id": "t1", "passed": False, "step": "failed", "table": "notification"},
        {"task_id": "t1", "passed": False, "step": "skipped", "table": "incident_task"},
        {"task_id": "t1", "passed": True, "step": "", "table": ""},
        {"task_id": "t2", "passed": True, "step": "", "table": ""},
        {"task_id": "t2", "passed": True, "step": "", "table": ""},
    ]
    queries = [r.get("query", "") for r in results.values()]
    text = report.summary(
        "eval",
        {"domain": "itsm", "mode": "oracle", "model": "haiku-5-5"},
        records,
        check_rows,
        report.cell_rows("t1", 1, results),
        report.change_rows("t1", 1, CHANGES, queries),
    )
    assert "| 1 of 2 (50.0%) | 1 of 2 | 3 of 6 | 1 | 1 |" in text
    assert "Failed checks by step: skipped 1, failed 1, wrong 1." in text
    assert "Steps skipped (no call wrote the table), by table: incident_task 1." in text
    assert "Cells wrong, by column: channel 1, priority 1." in text
    assert "| t1 | 0 of 1 | " in text
    assert "| t2 |" not in text


def test_a_call_that_names_a_row_without_writing_its_table_is_not_its_writer():
    execution = {
        "conversation_flow": [
            {
                "type": "ai_message",
                "content": "",
                "tool_calls": [
                    {"name": "create_new_hr_case", "args": {"status": "draft"}}
                ],
            },
            {
                "type": "tool_result",
                "tool_name": "create_new_hr_case",
                "result": wrapped('{"hr_case_id": 1}'),
            },
            {
                "type": "ai_message",
                "content": "",
                "tool_calls": [
                    {
                        "name": "create_hr_case_task",
                        "args": {"hr_case_id": 1, "status": "ready"},
                    }
                ],
            },
            {
                "type": "tool_result",
                "tool_name": "create_hr_case_task",
                "result": wrapped('{"hr_case_task_id": 9, "hr_case_id": 1}'),
            },
        ]
    }
    check = {
        "passed": False,
        "diagnosis": {
            "kind": "cells",
            "table": "hr_case",
            "rows": [
                {
                    "row": {"hr_case_id": 1},
                    "condition": "status = 'work_in_progress'",
                    "ok": False,
                    "actual": ["draft"],
                }
            ],
        },
    }
    found = report.trace(check, report.calls(execution), {"hr_case": ["hr_case_id"]})
    assert [c["tool"] for c in found["related"]] == ["create_new_hr_case"]
    assert found["set_by"]["status"]["tool"] == "create_new_hr_case"


def test_a_surplus_rows_diagnosis_is_traced_without_conditions():
    check = {
        "passed": False,
        "diagnosis": {
            "kind": "too_many_rows",
            "table": "incident",
            "rows": [{"incident_id": "INC_024", "status": "new"}],
        },
    }
    found = report.trace(check, report.calls(EXECUTION), KEYS)
    assert found["step"] == "attempted"
    assert [c["tool"] for c in found["related"]] == ["create_incident"]
    assert found["set_by"] == {}
