import asyncio
import sqlite3

import pytest

from mas_optimiser.benchmarks.enterpriseops import scoring, states

SCHEMA = """
CREATE TABLE incident (
    incident_id VARCHAR(16) NOT NULL, number VARCHAR(16), caller_id VARCHAR(16),
    priority VARCHAR(16), assigned_to INTEGER, state VARCHAR(16), notes TEXT,
    PRIMARY KEY (incident_id)
);
CREATE UNIQUE INDEX ix_number ON incident (number);
CREATE TABLE user_role (
    user_id INTEGER NOT NULL, role_id INTEGER NOT NULL, PRIMARY KEY (user_id, role_id)
);
"""
ROWS = """
INSERT INTO incident VALUES
('INC_001', 'N1', 'USER_1', 'low', NULL, 'new', '{"tag": "vpn"}'),
('INC_002', 'N2', 'USER_2', 'high', 7, 'new', NULL),
('INC_003', 'N3', 'USER_2', 'low', 4, 'closed', NULL);
INSERT INTO user_role VALUES (1, 1), (1, 2), (2, 1);
"""


@pytest.fixture
def seed():
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA + ROWS)
    return connection


def requirement(**fields):
    base = {
        "text": "request words",
        "kind": "change",
        "table": "incident",
        "cells": {},
        "expected": 1,
        "comparison": "equals",
        "source": "new",
    }
    base |= fields
    rows, cells = scoring.parts(base)
    base["sql"] = 'SELECT COUNT(*) FROM "incident" WHERE ' + " AND ".join(
        [rows] + [c for _, c in cells]
    )
    if base["table"] != "incident":
        base["sql"] = base["sql"].replace('"incident"', f'"{base["table"]}"')
    return base


def state_of(connection):
    """Every row of a local database, as ``states.read_state`` gives a server's."""
    keys = states.primary_keys(connection)
    return {
        table: [
            dict(zip(states.columns_of(connection, table), row, strict=True))
            for row in connection.execute(f'SELECT * FROM "{table}"')
        ]
        for table in keys
    }


def attempt(seed, statements, *requirements):
    """Run SQL as an agent would, then diff, rebuild and score, as the runner does."""
    after = sqlite3.connect(":memory:")
    seed.backup(after)
    for statement in statements:
        after.execute(statement)
    keys = states.primary_keys(seed)
    changes = states.diff(seed, state_of(after), keys)
    final = states.rebuild(seed, changes, keys)
    document = {"requirements": list(requirements)}
    result = asyncio.run(scoring.score(document, final, seed, changes, keys))
    return result, scoring.unasked(document, changes, final, seed, keys), changes


def test_conditions_tell_predicates_from_plain_values():
    assert scoring.conditions("a", "LIKE '%x%'") == ["a LIKE '%x%'"]
    assert scoring.conditions("a", "<rotation>on</rotation>") == [
        "a = '<rotation>on</rotation>'"
    ]
    assert scoring.conditions("a", ["IS NOT NULL", "<> 'x'"]) == [
        "a IS NOT NULL",
        "a <> 'x'",
    ]
    assert scoring.conditions("a", None) == ["a IS NULL"]
    assert scoring.conditions("a", "O'Brien") == ["a = 'O''Brien'"]
    assert scoring.conditions("a", 4) == ["a = 4"]


def test_a_met_requirement_has_no_audit(seed):
    result, _, _ = attempt(
        seed,
        ["UPDATE incident SET priority = 'high' WHERE incident_id = 'INC_001'"],
        requirement(anchor={"incident_id": "INC_001"}, cells={"priority": "high"}),
    )
    assert result["passed"] and result["met"] == result["total"] == 1
    assert "audit" not in result["requirements"][0]


def test_a_wrong_cell_names_the_row_its_value_and_what_was_wanted(seed):
    result, _, _ = attempt(
        seed,
        [],
        requirement(anchor={"incident_id": "INC_001"}, cells={"priority": "high"}),
    )
    found = result["requirements"][0]["audit"]
    assert found == {
        "kind": "wrong_cells",
        "named": 1,
        "wrong": 1,
        "rows": [
            {
                "key": {"incident_id": "INC_001"},
                "cells": [
                    {
                        "column": "priority",
                        "wanted": "priority = 'high'",
                        "actual": "low",
                    }
                ],
            }
        ],
    }


def test_a_null_cell_fails_its_condition(seed):
    result, _, _ = attempt(
        seed,
        [],
        requirement(anchor={"incident_id": "INC_001"}, cells={"assigned_to": "<> 9"}),
    )
    cells = result["requirements"][0]["audit"]["rows"][0]["cells"]
    assert cells == [
        {"column": "assigned_to", "wanted": "assigned_to <> 9", "actual": None}
    ]


def test_a_missing_row_is_reported_as_missing(seed):
    result, _, _ = attempt(
        seed,
        [],
        requirement(
            kind="create", anchor={"incident_id": "INC_004"}, cells={"priority": "low"}
        ),
    )
    assert result["requirements"][0]["audit"] == {
        "kind": "missing",
        "where": "incident_id = 'INC_004'",
    }


def test_too_many_rows_are_listed_by_key(seed):
    result, _, _ = attempt(
        seed,
        [],
        requirement(
            selector="state = 'new'", cells={"assigned_to": "IS NOT 4"}, expected=0
        ),
    )
    found = result["requirements"][0]["audit"]
    assert found["kind"] == "extra_rows"
    assert [row["key"] for row in found["rows"]] == [
        {"incident_id": "INC_001"},
        {"incident_id": "INC_002"},
    ]


def test_a_broken_stay_names_only_the_rows_the_attempt_changed(seed):
    # INC_003 is closed and already assigned to 4: the stay counts it, but the
    # attempt did not touch it, so only INC_002 is named.
    result, other, _ = attempt(
        seed,
        ["UPDATE incident SET assigned_to = 4 WHERE incident_id = 'INC_002'"],
        requirement(
            kind="stay", selector="caller_id = 'USER_2'", cells={"assigned_to": 4}
        ),
    )
    found = result["requirements"][0]["audit"]
    assert found == {
        "kind": "stay_broken",
        "touched": 1,
        "rows": [{"key": {"incident_id": "INC_002"}, "cells": {"assigned_to": [7, 4]}}],
    }
    assert result["stays_broken"] == 1
    assert other["other_rows"] == [
        {
            "table": "incident",
            "key": {"incident_id": "INC_002"},
            "cells": {"assigned_to": [7, 4]},
        }
    ]


def test_a_cell_on_a_json_field_is_audited(seed):
    result, _, _ = attempt(
        seed,
        [],
        requirement(
            anchor={"incident_id": "INC_001"},
            cells={"json_extract(notes, '$.tag')": "wifi"},
        ),
    )
    cell = result["requirements"][0]["audit"]["rows"][0]["cells"][0]
    assert cell["actual"] == "vpn" and cell["column"] == "json_extract(notes, '$.tag')"


def test_a_query_its_parts_do_not_capture_is_explained_condition_by_condition(seed):
    odd = requirement(anchor={"incident_id": "INC_001"}, cells={"priority": "high"})
    odd["sql"] = (
        "SELECT COUNT(*) FROM incident WHERE incident_id = 'INC_001' "
        "AND priority = 'high' AND caller_id IN (SELECT caller_id FROM incident)"
    )
    odd["cells"] = {}
    result, _, _ = attempt(seed, [], odd)
    found = result["requirements"][0]["audit"]
    assert found["kind"] == "unstructured"
    assert found["diagnosis"]["kind"] == "conditions"


def test_unasked_changes_split_other_rows_from_other_cells_of_asked_rows(seed):
    result, other, _ = attempt(
        seed,
        [
            "UPDATE incident SET priority = 'high', state = 'open' WHERE incident_id = 'INC_001'",
            "DELETE FROM user_role WHERE user_id = 2",
            "INSERT INTO incident VALUES ('INC_009', 'N9', 'USER_9', 'low', NULL, 'new', NULL)",
        ],
        requirement(anchor={"incident_id": "INC_001"}, cells={"priority": "high"}),
    )
    assert result["passed"]
    assert other["other_cells"] == 1
    assert sorted((row["table"], row.get("change")) for row in other["other_rows"]) == [
        ("incident", "inserted"),
        ("user_role", "deleted"),
    ]


def test_explain_gives_the_verdict_each_failure_and_the_benchmark_where_it_differs(
    seed,
):
    result, other, _ = attempt(
        seed,
        ["UPDATE incident SET assigned_to = 4 WHERE incident_id = 'INC_002'"],
        requirement(anchor={"incident_id": "INC_001"}, cells={"priority": "high"}),
        requirement(
            kind="stay", selector="caller_id = 'USER_2'", cells={"assigned_to": 4}
        ),
    )
    text = scoring.explain(result, other, benchmark_passed=True)
    lines = text.splitlines()
    assert lines[0] == "FAIL: 0 of 2 requirements met, 1 stay broken."
    assert lines[1].startswith(
        'R1 (change, incident) "request words": 1 of 1 rows wrong'
    )
    assert "incident INC_001: priority is 'low', wanted priority = 'high'" in lines[1]
    assert "incident INC_002: assigned_to from 7 to 4" in lines[2]
    assert lines[-1] == "The benchmark's own checks pass this attempt."
    assert (
        scoring.explain(result, other, benchmark_passed=False).count("benchmark") == 0
    )


def test_rows_that_hold_every_cell_but_are_too_few_are_reported_as_too_few(seed):
    result, _, _ = attempt(
        seed,
        [],
        requirement(
            selector="caller_id = 'USER_2'",
            cells={"state": "IS NOT NULL"},
            expected=2,
            comparison="greater_than",
        ),
    )
    entry = result["requirements"][0]
    assert entry["audit"] == {"kind": "too_few_rows", "named": 2}
    assert scoring.failure(entry) == "2 rows match every condition, more than 2 wanted"


def test_a_removed_row_is_asked_for_and_not_listed_as_unasked(seed):
    remove = requirement(kind="remove", anchor={"incident_id": "INC_003"}, expected=0)
    result, other, _ = attempt(
        seed, ["DELETE FROM incident WHERE incident_id = 'INC_003'"], remove
    )
    assert result["passed"]
    assert other == {"other_rows": [], "other_cells": 0}
