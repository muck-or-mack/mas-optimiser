"""Recompute an evaluation's results from its saved records, without running the agent.

Each attempt's record (``attempt-<n>.json``) and its changes against the seed
(``attempt-<n>.changes.json``) are enough to rebuild the final database
locally and run every check again: the task's checks as they now stand, the
same diagnosis and trace, and the same files the runner writes
(``summary.md``, ``tasks.csv``, ``checks.csv``, ``cells.csv``,
``changes.csv`` and ``attempt-<n>.md``). Use it after a change to the
diagnosis or to the checks. An attempt with no saved changes (one that
raised before it finished) is left as it was.

A check that has not changed since the attempt ran keeps the server's verdict; one
whose query or expected value has changed (a patch applied since) is run on
the local rebuild as the benchmark's engine runs it on the server, with the
executor's own comparison corrected for a numeric string as the runner
corrects it. The record's ``verification_results`` are replaced by the
recomputed ones; the verdicts as the attempt first produced them are kept beside
them as ``verification_results_as_run``. A condition on the clock
(``created_at >= datetime('now', '-1 day')``) is judged at the time the
attempt's checks ran, so its answer does not change with the day of the rescore.

Usage::

    mas-optimiser rescore evaluations/<experiment>/<evaluation> [...]
"""

import argparse
import asyncio
import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mas_optimiser.benchmarks.enterpriseops import (
    checks,
    diagnosis,
    runner,
    states,
)

DESCRIPTION = __doc__.split("\n\n")[0]
# The quoted ``now`` that SQLite's date functions take for the present moment.
NOW = re.compile(r"""(['"])now\1""", re.IGNORECASE)


def local_runner(final: sqlite3.Connection, now: str) -> diagnosis.RunSQL:
    """A query function on the rebuilt database, shaped like the executor's runner.

    Args:
        final: The rebuilt database.
        now: The time the attempt's checks ran, as SQLite writes one
            (``2026-11-03 08:04:22``); it stands in for ``'now'`` in every query.

    Returns:
        An async function of a query: a query the database rejects gives
        ``None``, a single cell its value, a single row its dict, several
        rows a list of dicts.
    """

    async def run_sql(query: str):
        try:
            cursor = final.execute(NOW.sub(f"'{now}'", query))
        except sqlite3.Error:
            return None
        columns = [c[0] for c in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        if len(rows) == 1 and len(columns) == 1:
            return rows[0][columns[0]]
        return rows[0] if len(rows) == 1 else rows

    return run_sql


async def check_locally(kept: list[dict], run_sql: diagnosis.RunSQL) -> dict[str, dict]:
    """Run each check's query on the rebuilt database and give its verdict as the executor would.

    Args:
        kept: The checks to run, from ``checks.prepare``.
        run_sql: The query function on the rebuilt database, from ``local_runner``.

    Returns:
        Each check's verdict keyed by unique name, shaped as the executor
        records it, with ``upstream_passed`` from ``checks.recheck``.
    """
    results = {}
    for check in kept:
        config = check["validation_config"]
        query = config.get("query", "")
        actual = await run_sql(query)
        expected = config.get("expected_value")
        comparison = config.get("comparison_type", "equals")
        result = {
            "passed": checks.compare(actual, expected, comparison),
            "expected": expected,
            "actual": actual,
            "comparison_type": comparison,
            "query": query,
        }
        checks.recheck(result)
        results[check["name"]] = result
    return results


def keep_live_verdicts(results: dict[str, dict], as_run: dict[str, dict]) -> dict:
    """The server's verdict stands for every check that has not changed since the attempt ran.

    The local rebuild re-checks only a check whose query or expected value
    differs from the attempt's (a patch applied since), and diagnoses the rest:
    the rebuild leaves out what the server stamps on rows it never changed,
    so its verdict is an approximation where the server's is the measurement.

    Args:
        results: The local verdicts from ``check_locally``, edited in place.
        as_run: The verdicts the attempt first produced.

    Returns:
        ``results``.
    """
    for name, result in results.items():
        live = as_run.get(name)
        if (
            live
            and live.get("query") == result["query"]
            and live.get("expected") == result["expected"]
            and "passed" in live
        ):
            result["passed"] = live["passed"]
            result["actual"] = live.get("actual")
            result["upstream_passed"] = live.get("upstream_passed", live["passed"])
    return results


async def rescore_attempt(
    folder: Path, attempt: int, config: dict, domain: str, pricing: dict
) -> tuple | None:
    """Recompute one attempt's verdicts, diagnosis and files from its saved record and changes.

    Args:
        folder: The task's folder under the evaluation's ``tasks/``.
        attempt: The attempt number, from 1.
        config: The task's config, from ``runner.load_tasks``.
        domain: The evaluation's domain.
        pricing: The price table, from ``evaluation.json``.

    Returns:
        What ``runner.outputs`` returns, or ``None`` when the attempt has no
        saved changes.
    """
    stem = f"attempt-{attempt}"
    saved = folder / f"{stem}.changes.json"
    if not saved.exists():
        return None
    record = json.loads((folder / f"{stem}.json").read_text())
    execution = record["runs"][0]
    changes = json.loads(saved.read_text())["changes"]
    seed, layout = states.seed_copy(
        Path(config["gym_servers_config"][0]["seed_database_file"]), domain
    )
    final = states.rebuild(seed, changes, layout["keys"])
    kept, names, _ = checks.prepare(folder.name, config["verifiers"])
    as_run = execution.setdefault(
        "verification_results_as_run", execution.get("verification_results", {})
    )
    # The server ran the checks as the attempt ended.
    ended = datetime.fromisoformat(execution["started_at"]) + timedelta(
        milliseconds=execution["execution_time_ms"]
    )
    run_sql = local_runner(final, f"{ended.astimezone(UTC):%Y-%m-%d %H:%M:%S}")
    results = keep_live_verdicts(await check_locally(kept, run_sql), as_run)
    await checks.original_verdicts(results, names, lambda name: run_sql)
    for result in results.values():
        if result["passed"] is False and result.get("query"):
            result["diagnosis"] = await diagnosis.diagnose(
                result, changes, layout["keys"], run_sql
            )
    execution["verification_results"] = results
    execution["overall_success"] = checks.passed(results)
    outputs = runner.outputs(
        folder.name, attempt, execution, changes, layout["keys"], names, pricing
    )
    runner.save_attempt(folder, attempt, record, changes)
    return outputs


async def rescore(evaluation: Path) -> Counter:
    """Recompute every attempt of one evaluation and rewrite its tables and summary.

    Args:
        evaluation: The evaluation folder.

    Returns:
        Counts of the attempts passed, failed and left as they were, and of
        the tasks with no check left to judge by.
    """
    settings = json.loads((evaluation / "evaluation.json").read_text())
    domain = settings["domain"]
    configs = runner.load_tasks(domain, settings["mode"])
    counts: Counter = Counter()
    records, rows, cells, changed = [], [], [], []
    for task_id in settings["tasks"]:
        folder = evaluation / "tasks" / task_id
        if not checks.prepare(task_id, configs[task_id]["verifiers"])[0]:
            # Every check excluded since the evaluation: the task cannot be judged.
            counts["no check to judge by"] += 1
            continue
        for attempt in range(1, settings["attempts"] + 1):
            if not (folder / f"attempt-{attempt}.json").exists():
                continue
            out = await rescore_attempt(
                folder, attempt, configs[task_id], domain, settings["pricing"]
            )
            if out is None:
                counts["no saved changes"] += 1
                continue
            record, check_rows, cell_rows, change_rows = out
            counts["pass" if record["success"] else "fail"] += 1
            records.append(record)
            rows += check_rows
            cells += cell_rows
            changed += change_rows
    runner.write_tables(evaluation, settings, records, rows, cells, changed)
    return counts


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the rescore options to ``parser``; shared with the ``mas-optimiser`` command.

    Args:
        parser: The ``rescore`` subparser.
    """
    parser.add_argument(
        "evaluation", nargs="+", type=Path, help="evaluation folders under evaluations/"
    )


def run(args: argparse.Namespace) -> None:
    """Rescore each evaluation and print the counts.

    Args:
        args: The parsed arguments.
    """
    for evaluation in args.evaluation:
        counts = asyncio.run(rescore(evaluation))
        print(
            f"{evaluation}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        )
