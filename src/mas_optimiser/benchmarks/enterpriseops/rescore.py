"""Recompute an evaluation's results from its saved records, without running the agent.

Each attempt's record (``attempt-<n>.json``) and its changes against the seed
(``attempt-<n>.changes.json``) are enough to rebuild the final database
locally and run every check again: the task's checks as they now stand, the
same diagnosis and trace, and the same files the runner writes
(``summary.md``, ``tasks.csv``, ``verifiers.csv``, ``cells.csv``,
``changes.csv`` and ``attempt-<n>.md``). Use it after a change to the
diagnosis or to the checks. An attempt with no saved changes (a run that
raised before it finished) is left as it was.

Each check's query runs on the local rebuild as the benchmark's engine runs
it on the server: a single cell comes back as a value, and the verdict is the
executor's own comparison, corrected for a numeric string as the runner
corrects it. The record's ``verification_results`` are replaced by the
recomputed ones; the verdicts as the run first produced them are kept beside
them as ``verification_results_as_run``.

Usage::

    mas-optimiser rescore runs/<experiment>/<evaluation> [...]

``python -m mas_optimiser.benchmarks.enterpriseops.rescore`` takes the same arguments.
"""

import argparse
import asyncio
import json
import sqlite3
from collections import Counter
from pathlib import Path

from mas_optimiser.benchmarks.enterpriseops import (
    diagnosis,
    runner,
    states,
    verifiers,
)

DESCRIPTION = __doc__.split("\n\n")[0]


def local_runner(final: sqlite3.Connection) -> diagnosis.RunSQL:
    """A query function on the rebuilt database, shaped like the executor's runner."""

    async def run_sql(query: str):
        cursor = final.execute(query)
        columns = [c[0] for c in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        if len(rows) == 1 and len(columns) == 1:
            return rows[0][columns[0]]
        return rows[0] if len(rows) == 1 else rows

    return run_sql


def compare(actual: object, expected: object, comparison: str) -> bool:
    """The executor's comparison of a check's value with its expected value."""
    try:
        if comparison == "equals":
            return actual == expected
        if comparison == "greater_than":
            return actual > expected  # type: ignore[operator]
        if comparison == "less_than":
            return actual < expected  # type: ignore[operator]
        if comparison == "contains":
            return str(expected) in str(actual)
    except TypeError:
        return False
    return False


async def check_locally(
    checks: list[dict], final: sqlite3.Connection
) -> dict[str, dict]:
    """Run each check's query on the rebuilt database and give its verdict as the executor would."""
    run_sql = local_runner(final)
    results = {}
    for check in checks:
        config = check["validation_config"]
        query = config.get("query", "")
        try:
            actual = await run_sql(query)
        except sqlite3.Error as error:
            actual = f"Error: {error}"
        expected = config.get("expected_value")
        comparison = config.get("comparison_type", "equals")
        result = {
            "passed": compare(actual, expected, comparison),
            "expected": expected,
            "actual": actual,
            "comparison_type": comparison,
            "query": query,
        }
        verifiers.recheck(result)
        results[check["name"]] = result
    return results


async def rescore_attempt(
    folder: Path, attempt: int, config: dict, domain: str, pricing: dict
) -> tuple | None:
    """Recompute one attempt's verdicts, diagnosis and files from its saved record and changes.

    Returns:
        What ``runner.outputs`` returns, or ``None`` when the attempt has no
        saved changes.
    """
    stem = f"attempt-{attempt}"
    saved = folder / f"{stem}.changes.json"
    if not saved.exists():
        return None
    record = json.loads((folder / f"{stem}.json").read_text())
    run = record["runs"][0]
    changes = json.loads(saved.read_text())["changes"]
    seed, layout = states.seed_copy(
        Path(config["gym_servers_config"][0]["seed_database_file"]), domain
    )
    final = states.rebuild(seed, changes, layout["keys"])
    checks, names = verifiers.unique_names(config["verifiers"])
    results = await check_locally(checks, final)
    run_sql = local_runner(final)
    for result in results.values():
        if not result["passed"] and result.get("query"):
            result["diagnosis"] = await diagnosis.diagnose(
                result, changes, layout["keys"], run_sql
            )
    run.setdefault("verification_results_as_run", run.get("verification_results", {}))
    run["verification_results"] = results
    run["overall_success"] = all(r["passed"] for r in results.values())
    outputs = runner.outputs(
        folder.name, attempt, run, changes, layout["keys"], names, pricing
    )
    runner.save_attempt(folder, attempt, record, changes)
    return outputs


async def rescore(evaluation: Path) -> Counter:
    """Recompute every attempt of one evaluation and rewrite its tables and summary.

    Returns:
        Counts of the attempts passed, failed and left as they were.
    """
    settings = json.loads((evaluation / "run.json").read_text())
    domain = settings["domain"]
    configs = runner.load_tasks(domain, settings["mode"])
    counts: Counter = Counter()
    records, rows, cells, changed = [], [], [], []
    for task_id in settings["tasks"]:
        folder = evaluation / "tasks" / task_id
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
    """Add the rescore options to ``parser``; shared with the ``mas-optimiser`` command."""
    parser.add_argument(
        "evaluation", nargs="+", type=Path, help="evaluation folders under runs/"
    )


def run(args: argparse.Namespace) -> None:
    """Rescore each evaluation and print the counts."""
    for evaluation in args.evaluation:
        counts = asyncio.run(rescore(evaluation))
        print(
            f"{evaluation}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        )


def main() -> None:
    """Command-line entry point; see the module docstring for usage."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    add_arguments(parser)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
