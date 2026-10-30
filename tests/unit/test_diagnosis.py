import asyncio
import sqlite3

import pytest

from mas_optimiser.benchmarks.enterpriseops import diagnosis

INCIDENT_QUERY = (
    "SELECT COUNT(*) FROM incident WHERE incident_id = 'INC_024' AND "
    "channel = 'phone' AND status = 'new' AND priority = 'moderate';"
)
KEYS = {"incident": ["incident_id"], "users": ["user_id"]}
# The attempt inserted INC_024; INC_025 was in the seed.
CHANGES = {
    "incident": {
        "inserted": [
            {
                "incident_id": "INC_024",
                "channel": "chat",
                "priority": None,
                "status": "new",
                "caller": "U2",
            }
        ],
        "deleted": [],
        "updated": [],
    }
}


@pytest.fixture
def run_sql():
    """A query function over an in-memory database, shaped like the executor's."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE incident (incident_id, channel, priority, status, caller)"
    )
    conn.execute("INSERT INTO incident VALUES ('INC_024', 'chat', NULL, 'new', 'U2')")
    conn.execute(
        "INSERT INTO incident VALUES ('INC_025', 'phone', 'high', 'new', 'U1')"
    )
    conn.execute("CREATE TABLE users (user_id, email)")
    conn.execute("INSERT INTO users VALUES ('U1', 'a@b.c')")

    async def run(query):
        cursor = conn.execute(query)
        columns = [d[0] for d in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        if len(rows) == 1 and len(columns) == 1:
            return rows[0][columns[0]]
        if len(rows) == 1:
            return rows[0]
        return rows

    return run


def verdict(query, expected=1, actual=0, comparison="equals"):
    return {
        "query": query,
        "expected": expected,
        "actual": actual,
        "comparison_type": comparison,
    }


def test_conditions_split_at_top_level_and_only():
    where = "a = 1 AND (b = 2 OR c = 3) AND d = 'x AND y' AND e IN (1, 2)"
    assert diagnosis.split_conditions(where) == [
        "a = 1",
        "(b = 2 OR c = 3)",
        "d = 'x AND y'",
        "e IN (1, 2)",
    ]
    assert diagnosis.split_conditions("a = 1 OR b = 2") is None


def test_count_queries_parse_with_aliases_and_joins():
    count, source, conditions = diagnosis.parse_count_query(
        "SELECT COUNT(*) AS n FROM incident i JOIN users u ON i.caller = u.id "
        "WHERE i.incident_id = 'INC_024' AND u.email = 'a@b.c'"
    )
    assert count == "COUNT(*)"
    assert source == "incident i JOIN users u ON i.caller = u.id"
    assert conditions == ["i.incident_id = 'INC_024'", "u.email = 'a@b.c'"]
    assert (
        diagnosis.parse_count_query("SELECT status FROM incident WHERE id = 1") is None
    )
    assert (
        diagnosis.parse_count_query(
            "SELECT COUNT(*) FROM t WHERE a = 1 GROUP BY b HAVING COUNT(*) > 1"
        )
        is None
    )
    assert (
        diagnosis.parse_count_query("WITH x AS (SELECT 1) SELECT COUNT(*) FROM x")
        is None
    )


def test_each_condition_is_tested_on_the_rows_the_attempt_touched(run_sql):
    found = asyncio.run(
        diagnosis.diagnose(verdict(INCIDENT_QUERY), CHANGES, KEYS, run_sql)
    )
    key = {"incident_id": "INC_024"}
    assert found["kind"] == "cells"
    assert found["rows"] == [
        {"row": key, "condition": "incident_id = 'INC_024'", "ok": True},
        {"row": key, "condition": "channel = 'phone'", "ok": False, "actual": ["chat"]},
        {"row": key, "condition": "status = 'new'", "ok": True},
        {
            "row": key,
            "condition": "priority = 'moderate'",
            "ok": False,
            "actual": [None],
        },
    ]
    assert (
        diagnosis.describe(found)
        == "channel = 'phone' (actual: chat); priority = 'moderate' (actual: None)"
    )


def test_an_untouched_row_that_matches_is_not_credited(run_sql):
    # INC_025 has channel = 'phone', but the attempt never touched it.
    query = "SELECT COUNT(*) FROM incident WHERE channel = 'phone' AND priority = 'moderate'"
    found = asyncio.run(diagnosis.diagnose(verdict(query), CHANGES, KEYS, run_sql))
    assert [cell["ok"] for cell in found["rows"]] == [False, False]
    assert found["rows"][0]["actual"] == ["chat"]


def test_a_table_the_attempt_never_touched_says_so(run_sql):
    found = asyncio.run(diagnosis.diagnose(verdict(INCIDENT_QUERY), {}, KEYS, run_sql))
    assert found == {"kind": "no_rows", "table": "incident"}
    assert diagnosis.describe(found) == "no row inserted or updated in incident"


def test_a_condition_on_a_joined_table_is_tested_through_the_join(run_sql):
    query = (
        "SELECT COUNT(*) FROM incident i JOIN users u ON i.caller = u.user_id "
        "WHERE i.incident_id = 'INC_024' AND u.email = 'a@b.c'"
    )
    found = asyncio.run(diagnosis.diagnose(verdict(query), CHANGES, KEYS, run_sql))
    # The inserted incident's caller has no users row, so the join finds
    # nothing; the condition on the incident itself still holds.
    assert [(cell["condition"], cell["ok"]) for cell in found["rows"]] == [
        ("i.incident_id = 'INC_024'", True),
        ("u.email = 'a@b.c'", False),
    ]
    assert found["rows"][1]["actual"] == []


def test_surplus_rows_are_shown_for_a_check_expecting_none(run_sql):
    query = "SELECT COUNT(*) FROM incident WHERE status = 'new'"
    found = asyncio.run(
        diagnosis.diagnose(verdict(query, 0, 2), CHANGES, KEYS, run_sql)
    )
    assert found["kind"] == "too_many_rows"
    assert [row["incident_id"] for row in found["rows"]] == ["INC_024", "INC_025"]
    assert diagnosis.describe(found).startswith("too many rows match; 2 shown")


def test_a_value_check_is_wanted_against_actual(run_sql):
    found = asyncio.run(
        diagnosis.diagnose(
            verdict(
                "SELECT status FROM incident WHERE incident_id = 'INC_024'",
                "resolved",
                "new",
            ),
            CHANGES,
            KEYS,
            run_sql,
        )
    )
    assert found == {
        "kind": "value",
        "table": "incident",
        "rows": [{"condition": "status = 'resolved'", "ok": False, "actual": ["new"]}],
    }


def test_other_shapes_and_older_records_are_left_alone(run_sql):
    query = "SELECT COUNT(*) FROM incident WHERE channel = 'phone' OR status = 'new'"
    assert asyncio.run(diagnosis.diagnose(verdict(query), CHANGES, KEYS, run_sql)) == {
        "kind": "unsupported"
    }
    assert diagnosis.describe({"kind": "unsupported"}) == ""
    assert diagnosis.describe({"kind": "no_match", "condition": "x = 1"}) == ""


def test_a_source_that_is_not_a_table_is_left_alone(run_sql):
    query = (
        "SELECT COUNT(*) FROM (SELECT incident_id FROM incident WHERE status = 'new') x "
        "WHERE x.incident_id = 'INC_024'"
    )
    found = asyncio.run(diagnosis.diagnose(verdict(query), CHANGES, KEYS, run_sql))
    assert found == {"kind": "unsupported"}
