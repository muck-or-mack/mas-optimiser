"""Unit tests of ``rescore`` on saved records and a local rebuild."""

import asyncio
import sqlite3

from mas_optimiser.benchmarks.enterpriseops import checks, rescore

# The moment the attempt's checks ran, as ``local_runner`` takes it.
ENDED = "2026-11-03 08:05:00"


def database():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE incident (incident_id, status, priority)")
    conn.execute("INSERT INTO incident VALUES ('INC_1', 'new', 'low')")
    conn.execute("INSERT INTO incident VALUES ('INC_2', 'closed', 'high')")
    return conn


def check(name, query, expected=1, comparison="equals"):
    return {
        "name": name,
        "validation_config": {
            "query": query,
            "expected_value": expected,
            "comparison_type": comparison,
        },
    }


def test_the_local_runner_shapes_results_as_the_executor_does():
    run_sql = rescore.local_runner(database(), ENDED)
    assert asyncio.run(run_sql("SELECT COUNT(*) FROM incident")) == 2
    assert (
        asyncio.run(run_sql("SELECT status FROM incident WHERE incident_id = 'INC_1'"))
        == "new"
    )
    row = asyncio.run(
        run_sql("SELECT status, priority FROM incident WHERE incident_id = 'INC_2'")
    )
    assert row == {"status": "closed", "priority": "high"}
    assert len(asyncio.run(run_sql("SELECT status FROM incident"))) == 2
    # A query the database rejects answers None, as the executor's runner does.
    assert asyncio.run(run_sql("SELECT nope FROM incident")) is None


def test_a_condition_on_the_clock_is_judged_at_the_time_the_checks_ran():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE hr_case (hr_case_id, created_at)")
    conn.execute("INSERT INTO hr_case VALUES (1, '2026-11-03 08:04:00')")
    query = (
        "SELECT COUNT(*) FROM hr_case WHERE created_at >= datetime('now', '-5 minutes')"
    )
    assert asyncio.run(rescore.local_runner(conn, ENDED)(query)) == 1
    # Judged two days on, the same row would be outside the window.
    assert asyncio.run(rescore.local_runner(conn, "2026-11-05 08:05:00")(query)) == 0


def test_compare_follows_the_executor_including_its_type_strictness():
    assert checks.compare(1, 1, "equals")
    assert not checks.compare(1, "1", "equals")
    assert checks.compare(2, 1, "greater_than")
    assert not checks.compare("a", 1, "greater_than")
    assert checks.compare("abc", "b", "contains")
    assert not checks.compare(1, 1, "unknown")


def test_checks_run_locally_keep_the_executor_verdict_beside_the_corrected_one():
    checks = [
        check("count", "SELECT COUNT(*) FROM incident WHERE status = 'new'", "1"),
        check(
            "value", "SELECT priority FROM incident WHERE incident_id = 'INC_2'", "low"
        ),
        check("broken", "SELECT COUNT(*) FROM nowhere"),
    ]
    run_sql = rescore.local_runner(database(), ENDED)
    results = asyncio.run(rescore.check_locally(checks, run_sql))
    # A numeric string expected value fails the executor's == and passes the recheck.
    assert results["count"]["upstream_passed"] is False
    assert results["count"]["passed"] is True
    assert results["value"]["passed"] is False and results["value"]["actual"] == "high"
    # A query the database rejects has no value, as under the executor.
    assert results["broken"]["passed"] is False
    assert results["broken"]["actual"] is None


def test_the_server_verdict_stands_for_a_check_that_has_not_changed():
    local = {
        "same": {"query": "q1", "expected": 1, "actual": 0, "passed": False},
        "patched": {"query": "q2 repaired", "expected": 1, "actual": 1, "passed": True},
    }
    as_run = {
        "same": {"query": "q1", "expected": 1, "actual": 1, "passed": True},
        "patched": {"query": "q2", "expected": 1, "actual": 0, "passed": False},
    }
    merged = rescore.keep_live_verdicts(local, as_run)
    # The unchanged check keeps the verdict the server gave; the patched one
    # is what the local rebuild says.
    assert merged["same"]["passed"] is True and merged["same"]["actual"] == 1
    assert merged["patched"]["passed"] is True
