import asyncio
import sqlite3

from mas_optimiser.benchmarks.enterpriseops import checks, rescore


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
    run_sql = rescore.local_runner(database())
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
    results = asyncio.run(rescore.check_locally(checks, database()))
    # A numeric string expected value fails the executor's == and passes the recheck.
    assert results["count"]["upstream_passed"] is False
    assert results["count"]["passed"] is True
    assert results["value"]["passed"] is False and results["value"]["actual"] == "high"
    assert results["broken"]["passed"] is False
    assert results["broken"]["actual"].startswith("Error:")
