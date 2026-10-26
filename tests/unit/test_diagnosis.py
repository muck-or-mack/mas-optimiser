import asyncio
import sqlite3

import pytest

from mas_optimiser.benchmarks.enterpriseops import diagnosis

INCIDENT_QUERY = (
    "SELECT COUNT(*) FROM incident WHERE incident_id = 'INC_024' AND "
    "channel = 'phone' AND status = 'new' AND priority = 'moderate';"
)


@pytest.fixture
def run_sql():
    """A query function over an in-memory database, shaped like the executor's."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE incident (incident_id, channel, priority, status)")
    conn.execute("INSERT INTO incident VALUES ('INC_024', 'chat', NULL, 'new')")
    conn.execute("INSERT INTO incident VALUES ('INC_025', 'phone', 'high', 'new')")

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


def test_failing_conditions_are_named_with_the_values_found(run_sql):
    verdict = {
        "query": INCIDENT_QUERY,
        "expected": 1,
        "actual": 0,
        "comparison_type": "equals",
    }
    found = asyncio.run(diagnosis.diagnose(verdict, run_sql))
    assert found["kind"] == "conditions" and found["held"] == 1
    assert found["failed"] == [
        {"condition": "channel = 'phone'", "actual": ["chat"]},
        {"condition": "priority = 'moderate'", "actual": [None]},
    ]
    assert (
        diagnosis.describe(found)
        == "channel = 'phone' (actual: chat); priority = 'moderate' (actual: None)"
    )


def test_a_missing_row_is_reported_as_no_match(run_sql):
    verdict = {
        "query": INCIDENT_QUERY.replace("INC_024", "INC_099"),
        "expected": "1",
        "actual": 0,
        "comparison_type": "equals",
    }
    found = asyncio.run(diagnosis.diagnose(verdict, run_sql))
    assert found == {"kind": "no_match", "condition": "incident_id = 'INC_099'"}
    assert diagnosis.describe(found) == "nothing matches incident_id = 'INC_099'"


def test_surplus_rows_are_shown_for_a_check_expecting_none(run_sql):
    verdict = {
        "query": "SELECT COUNT(*) FROM incident WHERE status = 'new'",
        "expected": 0,
        "actual": 2,
        "comparison_type": "equals",
    }
    found = asyncio.run(diagnosis.diagnose(verdict, run_sql))
    assert found["kind"] == "too_many_rows"
    assert [row["incident_id"] for row in found["rows"]] == ["INC_024", "INC_025"]
    assert diagnosis.describe(found).startswith("too many rows match; 2 shown")


def test_greater_than_walks_with_its_own_threshold(run_sql):
    verdict = {
        "query": "SELECT COUNT(*) FROM incident WHERE status = 'new' AND channel = 'phone'",
        "expected": 1,
        "actual": 1,
        "comparison_type": "greater_than",
    }
    found = asyncio.run(diagnosis.diagnose(verdict, run_sql))
    assert found["kind"] == "conditions" and found["held"] == 1
    assert found["failed"][0]["condition"] == "channel = 'phone'"
    assert sorted(found["failed"][0]["actual"]) == ["chat", "phone"]


def test_other_verdicts_are_left_alone(run_sql):
    verdict = {
        "query": "SELECT status FROM incident WHERE incident_id = 'INC_024'",
        "expected": "resolved",
        "actual": "new",
        "comparison_type": "equals",
    }
    assert asyncio.run(diagnosis.diagnose(verdict, run_sql)) == {"kind": "unsupported"}
    assert diagnosis.describe({"kind": "unsupported"}) == ""
    assert diagnosis.describe(None) == ""
