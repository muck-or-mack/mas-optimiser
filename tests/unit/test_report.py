"""Unit tests of ``report``: the trace from a failed check to the agent's turn, the page and the summary."""

import json

from mas_optimiser.benchmarks.enterpriseops import report
from tests.unit.test_markdown import cells


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
                        "column": "incident_id",
                        "wanted": "= 'INC_024'",
                        "ok": True,
                        "actual": ["INC_024"],
                    },
                    {
                        "row": {"incident_id": "INC_024"},
                        "condition": "channel = 'phone'",
                        "column": "channel",
                        "wanted": "= 'phone'",
                        "ok": False,
                        "actual": ["chat"],
                    },
                    {
                        "row": {"incident_id": "INC_024"},
                        "condition": "priority = 'critical'",
                        "column": "priority",
                        "wanted": "= 'critical'",
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
# The arguments each tool takes, as the evaluation's tool list gives them.
ARGUMENTS = {"create_incident": ["caller_id", "channel", "priority", "impact"]}
# The tools the task was given.
GIVEN = ["list_users", "create_incident", "send_notification", "create_incident_task"]


def traced():
    execution = json.loads(json.dumps(EXECUTION))
    agent_calls = report.calls(execution)
    for check in execution["verification_results"].values():
        if not check["passed"]:
            check["trace"] = report.trace(check, agent_calls, KEYS, ARGUMENTS)
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


def test_a_lookup_that_finds_no_record_is_told_from_an_error():
    def flow(tool, text):
        return {
            "conversation_flow": [
                {"type": "ai_message", "tool_calls": [{"name": tool, "args": {}}]},
                {"type": "tool_result", "result": {"success": False, "result": text}},
            ]
        }

    def found_nothing(tool, text):
        return report.calls(flow(tool, text))[0]["nothing_found"]

    # The three servers' wordings: an error code, a message, a validation type.
    assert found_nothing("find_item_by_serial_number", '{"error_code": "NOT_FOUND"}')
    assert found_nothing("get_user", '{"detail": "User 9 not found"}')
    assert found_nothing("list_cases", '{"type": "value_error.not_found"}')
    # A bad argument is an error, and so is a write that names a missing record.
    assert not found_nothing("get_user", '{"detail": "user_id must be a string"}')
    assert not found_nothing("create_incident", '{"detail": "User 9 not found"}')
    assert not report.calls(EXECUTION)[0]["nothing_found"]


def test_a_wrong_cell_is_traced_to_the_call_that_set_it():
    found = traced()["verification_results"]["Incident"]["trace"]
    assert found["failure"] == "wrong values"
    # The create call wrote the row; the notification call, though it names
    # the incident, does not write incidents.
    assert [c["turn"] for c in found["related"]] == [2]
    assert found["set_by"]["channel"] == {
        "turn": 2,
        "tool": "create_incident",
        "how": "passed",
        "value": "chat",
    }
    assert found["set_by"]["priority"]["how"] == "passed"
    # The key was not passed and the tool has no argument for it: the
    # server set it when the call wrote the row.
    assert found["set_by"]["incident_id"] == {
        "turn": 2,
        "tool": "create_incident",
        "how": "no argument",
    }


def test_a_cell_no_call_passed_is_left_out_when_the_tool_takes_it():
    check = json.loads(json.dumps(EXECUTION["verification_results"]["Incident"]))
    check["diagnosis"]["rows"].append(
        {
            "row": {"incident_id": "INC_024"},
            "condition": "impact = 'high'",
            "column": "impact",
            "wanted": "= 'high'",
            "ok": False,
            "actual": ["low"],
        }
    )
    agent_calls = report.calls(EXECUTION)
    known = report.trace(check, agent_calls, KEYS, ARGUMENTS)["set_by"]["impact"]
    assert known == {"turn": 2, "tool": "create_incident", "how": "left out"}
    # Without the evaluation's tool list the two cannot be told apart.
    unknown = report.trace(check, agent_calls, KEYS, {})["set_by"]
    assert unknown["impact"]["how"] == unknown["incident_id"]["how"] == "not passed"


def test_a_missing_row_is_told_by_its_calls():
    results = traced()["verification_results"]
    # send_notification was called for the row and returned an error.
    assert results["Notification"]["trace"]["failure"] == "call failed"
    # No call wrote incident_task at all.
    assert results["Task"]["trace"]["failure"] == "table not written"


def page_rows(text):
    return [cells(line) for line in text.split("\n") if line.startswith("|")]


def test_the_attempt_page_goes_from_the_tables_to_the_checks_to_the_turns():
    text = report.attempt_text("task_x", 1, traced(), CHANGES, GIVEN, ARGUMENTS)
    assert text.startswith("# task_x, attempt 1: FAIL (1 of 4 checks passed)")
    assert "## Request\n\nLog a critical phone incident for Benjamin" in text
    sections = [
        "## Tables the checks look at",
        "## Tools the task was given",
        "## Checks",
        "## What the agent did",
        "## Rows the attempt changed",
    ]
    assert sorted(sections, key=text.index) == sections
    rows = page_rows(text)
    # One row per table: the checks on it, what the attempt changed, the result.
    assert "3 tables with a check on them: 0 correct, 1 wrong, 2 missed." in text
    assert ["`incident`", "Incident", "1 inserted", "wrong"] in rows
    assert ["`notification`", "Notification", "none", "missed"] in rows
    assert ["`incident_knowledge`", "none", "1 inserted", "not asked for"] in rows
    assert "Checks whose table could not be read from their query: Caller." in text
    # The tools given, when each was called and what each takes.
    assert ["`create_incident`", "2", "4"] in rows
    assert ["`create_incident_task`", "never", "not saved"] in rows
    assert "create_incident(caller_id, channel, priority, impact)" in text
    # A check with wrong values: each cell wanted against found, and where
    # the value came from.
    assert "Row `incident_id INC_024`: inserted at turn 2 by `create_incident`." in text
    assert ["column", "wanted", "found", "ok", "where the value came from"] in rows
    assert [
        "`channel`",
        "= 'phone'",
        "chat",
        "WRONG",
        "passed at turn 2: `create_incident(channel='chat')`",
    ] in rows
    assert [
        "`incident_id`",
        "= 'INC_024'",
        "INC_024",
        "ok",
        "set by the server: `create_incident` takes no `incident_id`",
    ] in rows
    # A check with no row: the calls that tried, or the tool that could have.
    assert (
        "No row of `notification` was inserted or updated. "
        "Calls that write it: turn 3 `send_notification` failed." in text
    )
    assert (
        "No call wrote `incident_task`. "
        "The task was given `create_incident_task` to write it." in text
    )
    assert ["check wants", "found", "tool given that takes it"] in rows
    assert ["incident_id = 'INC_024'", "no row", ""] in rows
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
    # The rows the attempt touched, last.
    assert (
        "### incident\n\nRow `incident_id INC_024`, inserted:\n\n```\nincident_id = INC_024\n```"
        in text
    )


def test_the_page_says_which_tool_takes_a_column_no_call_wrote():
    arguments = ARGUMENTS | {"create_incident_task": ["incident_id", "status"]}
    execution = traced()
    execution["verification_results"]["Task"]["query"] += " AND state = 'open'"
    text = report.attempt_text("task_x", 1, execution, CHANGES, GIVEN, arguments)
    rows = page_rows(text)
    assert ["incident_id = 'INC_024'", "no row", "`create_incident_task`"] in rows
    assert ["state = 'open'", "no row", "none"] in rows


def test_an_excluded_check_is_listed_and_not_counted():
    execution = traced()
    execution["verification_results"]["Caller"] = {
        "passed": None,
        "benchmark_passed": False,
        "patch": "excluded",
    }
    text = report.attempt_text("task_x", 1, execution, CHANGES, GIVEN, ARGUMENTS)
    assert text.startswith("# task_x, attempt 1: FAIL (0 of 3 checks passed)")
    assert "### EXCLUDED  Caller" in text


def test_a_whole_toolbox_is_cut_to_the_tools_called_and_an_empty_cell_says_so():
    given = GIVEN + [f"get_thing_{n}" for n in range(30)]
    changes = {
        "incident": {
            "inserted": [{"incident_id": "INC_024", "problem": None}],
            "updated": [],
            "deleted": [],
        }
    }
    text = report.attempt_text("task_x", 1, traced(), changes, given, ARGUMENTS)
    assert "34 tools; the 3 the agent called are shown, the rest are in" in text
    listed = [row[0] for row in page_rows(text) if row[1:] == ["never", "not saved"]]
    assert "get_thing_0" not in text and listed == []
    assert "incident_id = INC_024\nproblem     = (empty)" in text


def test_what_the_agent_wrote_is_never_cut_and_keeps_its_lines():
    execution = traced()
    call = execution["conversation_flow"][4]["tool_calls"][0]
    call["args"] = {"message": "Dear Ben,\n\nLogged.", "channel": "chat"}
    text = report.attempt_text(
        "task_x", 1, execution, CHANGES, GIVEN, ARGUMENTS, max_chars=40
    )
    assert "message = Dear Ben,\n          \n          Logged.\nchannel = chat" in text


def test_a_long_field_of_an_answer_does_not_hide_the_ones_after_it():
    lines = report._fields({"description": "word " * 200, "channel": "chat"}, 40)
    # Forty characters of the value, then the mark that it was cut.
    assert lines == ["description = " + "word " * 8 + " ...", "channel     = chat"]


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
    text = report.attempt_text("task_x", 1, execution, changes, GIVEN, ARGUMENTS)
    assert (
        "Row `incident_id INC_024`: in the seed, updated at turn 2 by `create_incident`."
        in text
    )
    rows = page_rows(text)
    header = ["column", "wanted", "before", "found", "ok", "where the value came from"]
    assert header in rows
    # An updated row shows each changed cell before and after.
    assert (
        "Row `incident_id INC_024`, updated:\n\n```\nchannel = email -> chat\n```"
        in text
    )
    assert [
        "`channel`",
        "= 'phone'",
        "email",
        "chat",
        "WRONG",
        "passed at turn 2: `create_incident(channel='chat')`",
    ] in rows
    assert [row[2] for row in rows if row[0] == "`incident_id`"] == ["unchanged"]


def test_cell_rows_give_wanted_against_found_and_where_the_value_came_from():
    rows = report.cell_rows("task_x", 1, traced()["verification_results"])
    assert [tuple(r[k] for k in report.CELL_COLUMNS[5:]) for r in rows] == [
        ("incident_id", "incident_id = 'INC_024'", "= 'INC_024'", "INC_024", True)
        + ("no argument", 2, "create_incident"),
        ("channel", "channel = 'phone'", "= 'phone'", "chat", False)
        + ("passed", 2, "create_incident"),
        ("priority", "priority = 'critical'", "= 'critical'", "moderate", False)
        + ("passed", 2, "create_incident"),
    ]


def test_table_rows_say_how_the_attempt_did_on_each_table():
    results = traced()["verification_results"]
    tables = report.table_rows("task_x", 1, results, CHANGES)
    assert [tuple(t[k] for k in report.TABLE_COLUMNS[2:]) for t in tables] == [
        ("incident", 1, 0, 1, 0, 0, "wrong"),
        ("notification", 1, 0, 0, 0, 0, "missed"),
        ("incident_task", 1, 0, 0, 0, 0, "missed"),
        ("incident_knowledge", 0, 0, 1, 0, 0, "not asked for"),
    ]
    # Without the changes nothing can be said of a table.
    assert report.table_rows("task_x", 1, results, None) == []


def test_a_table_belongs_to_the_check_that_counts_its_rows():
    results = {
        "user": {"passed": True, "query": "SELECT COUNT(*) FROM users WHERE id = 7"},
        "sla": {
            "passed": True,
            "query": "SELECT COUNT(*) FROM incident_sla s JOIN sla_definition d "
            "ON d.id = s.sla_id WHERE d.name = 'gold'",
        },
        "dropped": {
            "passed": None,
            "patch": "excluded",
            "query": "SELECT COUNT(*) FROM incident WHERE status = 'new'",
        },
    }
    one = {"inserted": [{"id": 1}], "updated": [], "deleted": []}
    changes = {"incident_sla": one, "sla_definition": one, "incident": one}
    tables = report.table_rows("task_x", 1, results, changes)
    assert [(t["table"], t["checks"], t["result"]) for t in tables] == [
        # A check that asks for nothing new is met by leaving its table alone.
        ("users", 1, "correct"),
        ("incident_sla", 1, "correct"),
        # Read through the join, or only by a check that is not counted.
        ("sla_definition", 0, "read by a check"),
        ("incident", 0, "read by a check"),
    ]


def test_summary_counts_failures_and_names_the_worst_tasks():
    execution = traced()
    results = execution["verification_results"]
    outcomes = [
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
    failed = (
        ("wrong values", "incident"),
        ("call failed", "notification"),
        ("table not written", "incident_task"),
    )
    check_rows = [
        {"task_id": "t1", "passed": False, "failure": failure, "table": table}
        for failure, table in failed
    ] + [
        {"task_id": task, "passed": True, "failure": "", "table": ""}
        for task in ("t1", "t2", "t2")
    ]
    failures = [
        {"table": "incident", "column": "channel", "failure": "wrong value"}
        | {"how": "passed", "attempts": 1, "tasks": 1},
        {"table": "incident_task", "column": "", "failure": "table not written"}
        | {"how": "", "attempts": 1, "tasks": 1},
    ]
    text = report.summary(
        "eval",
        {"domain": "itsm", "mode": "oracle", "model": "haiku-5-5"},
        outcomes,
        check_rows,
        report.cell_rows("t1", 1, results),
        report.table_rows("t1", 1, results, CHANGES),
        failures,
    )
    rows = page_rows(text)
    # The headline on its side: a measure, its value, what it counts.
    values = {row[0]: row[1] for row in rows if len(row) == 3}
    assert values["attempts fully passed"] == "1 of 2 (50.0%)"
    assert values["tasks passed on every attempt"] == "1 of 2 (50.0%)"
    assert values["checks passed"] == "3 of 6 (50.0%)"
    assert values["tables correct"] == "0 of 3 (0.0%)"
    assert values["tables wrong"] == "1 of 3 (33.3%)"
    assert values["tables missed"] == "2 of 3 (66.7%)"
    assert values["attempts with a tool error"] == "1 of 2 (50.0%)"
    assert values["lookups that found nothing"] == "0"
    assert values["attempts that changed a table not asked for"] == "1 of 2 (50.0%)"
    # The tables with a failure, then the head of the failures.
    assert "### Tables wrong or missed (3 of 3 shown;" in text
    assert ["`incident`", "1", "0", "1", "0"] in rows
    assert ["`incident_task`", "1", "0", "0", "1"] in rows
    assert "### Most frequent failures (2 of 2 shown;" in text
    assert ["`incident`", "`channel`", "wrong value", "passed", "1", "1"] in rows
    assert ["`incident_task`", "", "table not written", "", "1", "1"] in rows
    # The words are explained where they are used, and the files listed.
    assert ["table not written", "no call wrote the table"] in rows
    assert values["`tables.csv`"] == "attempt and table"
    # Only a task that failed is among those that passed least.
    assert ["t1", "0 of 1", "call failed: `notification`"] in rows
    assert not any(row[0] == "t2" for row in rows)


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
                    "column": "status",
                    "wanted": "= 'work_in_progress'",
                    "ok": False,
                    "actual": ["draft"],
                }
            ],
        },
    }
    found = report.trace(
        check, report.calls(execution), {"hr_case": ["hr_case_id"]}, {}
    )
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
    found = report.trace(check, report.calls(EXECUTION), KEYS, {})
    assert found["failure"] == "too many rows"
    assert [c["tool"] for c in found["related"]] == ["create_incident"]
    assert found["set_by"] == {}
