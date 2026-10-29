"""Score an attempt's final database against the task's check file, and explain each failure.

A check file (``checks``) lists what the task's request asks of the final
database as count queries. After an attempt, its final state is rebuilt
locally from the seed and the attempt's changes (``states``), and scoring runs
there, so an attempt can be scored again after a check file changes without
running the agent.

Each requirement's query decides it. A requirement that is not met gets an
audit built from its parts, the rows its anchor or selector names and the
condition each cell must meet. A broken stay lists the rows it names that the
attempt changed, since a stay holds on the seed and only a change can break
it. Otherwise a count that came out too high lists the rows that match
although they should not, and a count that came out too low lists the named
rows with each failing cell's value against what was asked, or says that no
such row exists. Where the parts do not reproduce the query's count on the
final state (a join, a subquery or another form the parts do not capture),
the failure is explained condition by condition instead, as ``diagnosis``
does for the benchmark's own checks.

Changed rows and cells that no requirement asked for are listed apart. They
are not scored: the stays already score the changes a request forbids.
``explain`` turns all of this into a few lines of text, the same for a person
reading the results and for the proposer.
"""

import re
import sqlite3

from mas_optimiser.benchmarks.enterpriseops import diagnosis, states

# A predicate starts with an operator, in capitals if a word, and a space;
# a value such as "<rotation>" is plain text.
PREDICATE = re.compile(r"^(=|<>|!=|<=|>=|<|>|IS|NOT|IN|LIKE|BETWEEN|GLOB) ")
IDENTIFIER = re.compile(r"\w+")
# Rows shown per failed requirement, and unasked changes listed per attempt;
# the totals are always given.
SAMPLE_ROWS = 5
SAMPLE_CHANGES = 10
WANTED = {"equals": "", "greater_than": "more than ", "less_than": "fewer than "}


def literal(value: object) -> str:
    """A cell value as an SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int | float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def conditions(column: str, value: object) -> list[str]:
    """The SQL conditions one cell or anchor entry stands for.

    A plain value means equality, a string that starts with an operator is a
    predicate on the column, and a list is several conditions that all hold.
    """
    if isinstance(value, list):
        return [c for item in value for c in conditions(column, item)]
    if value is None:
        return [f"{column} IS NULL"]
    if isinstance(value, str) and PREDICATE.match(value):
        return [f"{column} {value}"]
    return [f"{column} = {literal(value)}"]


def parts(requirement: dict) -> tuple[str, list[tuple[str, str]]]:
    """A requirement's row condition and its cell conditions.

    Returns:
        The WHERE clause naming the rows (the anchor's key, or the selector
        bracketed so that an OR inside it binds first) and, per cell
        condition, the column it tests with the condition.
    """
    anchor = requirement.get("anchor")
    if anchor:
        rows = " AND ".join(
            c for column, v in anchor.items() for c in conditions(column, v)
        )
    else:
        rows = f"({requirement['selector']})"
    cells = [
        (column, condition)
        for column, value in requirement["cells"].items()
        for condition in conditions(column, value)
    ]
    return rows, cells


def selected(column: str) -> str:
    """A cell's column as a SELECT item: quoted if a plain name, as written if an expression.

    A cell may test a computed value, such as a field of a JSON column
    (``json_extract(values, '$.category')``), rather than a column.
    """
    return f'"{column}"' if IDENTIFIER.fullmatch(column) else column


def holds(count: float, expected: float, comparison: str) -> bool:
    """Whether a count meets what a requirement expects."""
    if comparison == "greater_than":
        return count > expected
    if comparison == "less_than":
        return count < expected
    return count == expected


def changed_rows(changes: dict, table: str, keys: dict) -> dict[tuple, dict]:
    """The rows of one table the attempt inserted, deleted or updated, by key."""
    change = changes.get(table, {"inserted": [], "deleted": [], "updated": []})
    key = keys[table]
    rows = {}
    for row in change["inserted"]:
        rows[tuple(row[k] for k in key)] = {
            "key": {k: row[k] for k in key},
            "change": "inserted",
        }
    for row_key in change["deleted"]:
        rows[tuple(row_key[k] for k in key)] = {"key": row_key, "change": "deleted"}
    for update in change["updated"]:
        rows[tuple(update["key"][k] for k in key)] = {
            "key": update["key"],
            "cells": update["changes"],
        }
    return rows


def audit(
    requirement: dict,
    count: float,
    final: sqlite3.Connection,
    seed: sqlite3.Connection,
    changes: dict,
    keys: dict,
) -> dict:
    """Explain why a requirement's count is not what it expects.

    Returns:
        A dict with ``kind`` and the evidence: ``stay_broken`` (``touched``,
        the number of rows the stay names before or after that the attempt
        changed, with sample ``rows``), ``extra_rows`` (sample ``rows``, each
        a ``key`` and the cell ``values``, that match although the count is
        too high), ``missing`` (no row matches
        ``where``), ``wrong_cells`` (``named`` rows, of which ``wrong`` fail
        a cell, with sample ``rows`` and their failing cells),
        ``too_few_rows`` (the rows are there and hold every cell, but fewer
        than expected), or ``unstructured`` (the parts do not reproduce the
        count).
    """
    table, key = requirement["table"], keys[requirement["table"]]
    rows, cells = parts(requirement)
    every = " AND ".join([rows] + [condition for _, condition in cells])
    parts_count = final.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {every}')
    if parts_count.fetchone()[0] != count:
        return {"kind": "unstructured"}
    if requirement["kind"] == "stay":
        changed = changed_rows(changes, table, keys)
        names = ", ".join(f'"{k}"' for k in key)
        named = {
            tuple(row)
            for state in (seed, final)
            for row in state.execute(f'SELECT {names} FROM "{table}" WHERE {rows}')
        }
        touched = [changed[k] for k in sorted(named & changed.keys(), key=repr)]
        return {
            "kind": "stay_broken",
            "touched": len(touched),
            "rows": touched[:SAMPLE_ROWS],
        }
    columns = key + [c for c in dict.fromkeys(c for c, _ in cells) if c not in key]
    listed = ", ".join(selected(c) for c in columns)
    expected, comparison = requirement["expected"], requirement["comparison"]
    if comparison == "less_than" or (comparison == "equals" and count > expected):
        found = final.execute(
            f'SELECT {listed} FROM "{table}" WHERE {every} LIMIT {SAMPLE_ROWS}'
        )
        sample = [dict(zip(columns, row, strict=True)) for row in found]
        return {
            "kind": "extra_rows",
            "rows": [
                {
                    "key": {k: values[k] for k in key},
                    "values": {c: v for c, v in values.items() if c not in key},
                }
                for values in sample
            ],
        }
    named = final.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {rows}').fetchone()[0]
    if not named:
        return {"kind": "missing", "where": rows}
    if not cells:
        return {"kind": "too_few_rows", "named": named}
    # "(c) IS 1" is 0 rather than NULL when a NULL cell leaves c unknown, so
    # a NULL fails the condition here as it does in the count.
    tests = [f"({condition}) IS 1" for _, condition in cells]
    failing = f"{rows} AND NOT ({' AND '.join(tests)})"
    wrong = final.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {failing}').fetchone()[
        0
    ]
    if not wrong:
        return {"kind": "too_few_rows", "named": named}
    found = final.execute(
        f'SELECT {listed}, {", ".join(tests)} FROM "{table}" WHERE {failing} '
        f"LIMIT {SAMPLE_ROWS}"
    )
    sample = []
    for row in found:
        values = dict(zip(columns, row[: len(columns)], strict=True))
        sample.append(
            {
                "key": {k: values[k] for k in key},
                "cells": [
                    {"column": column, "wanted": condition, "actual": values[column]}
                    for (column, condition), ok in zip(
                        cells, row[len(columns) :], strict=True
                    )
                    if not ok
                ],
            }
        )
    return {"kind": "wrong_cells", "named": named, "wrong": wrong, "rows": sample}


def local_runner(connection: sqlite3.Connection) -> diagnosis.RunSQL:
    """A query function on a local database, shaping results as the executor's SQL runner does."""

    async def run_sql(query: str):
        cursor = connection.execute(query)
        names = [d[0] for d in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        if len(rows) == 1 and len(names) == 1:
            return rows[0][names[0]]
        return rows[0] if len(rows) == 1 else rows

    return run_sql


async def score(
    document: dict,
    final: sqlite3.Connection,
    seed: sqlite3.Connection,
    changes: dict,
    keys: dict,
) -> dict:
    """Score every requirement of a check file on an attempt's final state.

    Args:
        document: The task's check file.
        final: The attempt's final state, as ``states.rebuild`` makes it.
        seed: The local copy of the task's seed.
        changes: The attempt's changes, as ``states.diff`` gives them.
        keys: Each table's primary key columns, as ``states.primary_keys``
            gives them.

    Returns:
        ``passed`` (every requirement met), ``met``, ``total``,
        ``stays_broken`` and ``requirements``: per requirement in file
        order, its number, kind, table, text, source, count, expected count,
        comparison, whether it is met and, if not, its ``audit``.
    """
    results = []
    for number, requirement in enumerate(document["requirements"], 1):
        count = final.execute(requirement["sql"]).fetchone()[0]
        met = holds(count, requirement["expected"], requirement["comparison"])
        entry = {
            "requirement": number,
            "kind": requirement["kind"],
            "table": requirement["table"],
            "text": requirement["text"],
            "source": requirement["source"],
            "count": count,
            "expected": requirement["expected"],
            "comparison": requirement["comparison"],
            "met": met,
        }
        if not met:
            found = audit(requirement, count, final, seed, changes, keys)
            if found["kind"] == "unstructured":
                found["diagnosis"] = await diagnosis.diagnose(
                    {
                        "query": requirement["sql"],
                        "expected": requirement["expected"],
                        "actual": count,
                        "comparison_type": requirement["comparison"],
                    },
                    local_runner(final),
                )
            entry["audit"] = found
        results.append(entry)
    met = sum(r["met"] for r in results)
    return {
        "passed": met == len(results),
        "met": met,
        "total": len(results),
        "stays_broken": sum(not r["met"] and r["kind"] == "stay" for r in results),
        "requirements": results,
    }


def unasked(
    document: dict,
    changes: dict,
    final: sqlite3.Connection,
    seed: sqlite3.Connection,
    keys: dict,
) -> dict:
    """The attempt's changes that no create, change or remove requirement asked for.

    A requirement asks for the rows its anchor or selector names (in the
    final state, or for a removal in the seed) and the columns its cells
    test; a created or removed row is asked for whole. What is left splits in
    two: changes to rows no requirement names at all, the likelier sign of an
    agent doing more than asked, and changes to other cells of named rows,
    often values a tool sets itself alongside the one asked for.

    Returns:
        ``other_rows``, the changed rows no requirement names (as
        ``changed_rows`` gives them, with ``table`` added), and
        ``other_cells``, the number of changed cells of named rows that no
        requirement tests.
    """
    named, asked = set(), set()
    for requirement in document["requirements"]:
        if requirement["kind"] == "stay":
            continue
        table, key = requirement["table"], keys[requirement["table"]]
        rows, cells = parts(requirement)
        state = seed if requirement["kind"] == "remove" else final
        names = ", ".join(f'"{k}"' for k in key)
        tested = [
            name
            for name in states.columns_of(seed, table)
            if any(re.search(rf"\b{re.escape(name)}\b", column) for column, _ in cells)
        ]
        for row in state.execute(f'SELECT {names} FROM "{table}" WHERE {rows}'):
            named.add((table, tuple(row)))
            asked.update((table, tuple(row), column) for column in tested)
    other_rows, other_cells = [], 0
    for table in changes:
        for row_key, row in changed_rows(changes, table, keys).items():
            if (table, row_key) not in named:
                other_rows.append({"table": table} | row)
            elif "cells" in row:
                other_cells += sum(
                    (table, row_key, c) not in asked for c in row["cells"]
                )
    return {"other_rows": other_rows, "other_cells": other_cells}


def shown(value: object) -> str:
    """A value as the explanation prints it."""
    return "NULL" if value is None else literal(value)


def row_name(table: str, key: dict) -> str:
    """A row by its table and key, e.g. ``customer_case 400``."""
    if len(key) == 1:
        return f"{table} {next(iter(key.values()))}"
    return f"{table} " + ", ".join(f"{k} {v}" for k, v in key.items())


def change_text(table: str, row: dict) -> str:
    """One changed row, e.g. ``customer_case 1: assigned_to from 403 to 4``."""
    if "change" in row:
        return f"{row_name(table, row['key'])} {row['change']}"
    cells = ", ".join(
        f"{c} from {shown(before)} to {shown(after)}"
        for c, (before, after) in row["cells"].items()
    )
    return f"{row_name(table, row['key'])}: {cells}"


def first(total: int, sample: list) -> str:
    """A note that only the first rows of a longer list are shown."""
    return f" (first {len(sample)} shown)" if total > len(sample) else ""


def failure(entry: dict) -> str:
    """One line saying what a failed requirement found."""
    found, table = entry["audit"], entry["table"]
    target = f"{WANTED[entry['comparison']]}{entry['expected']}"
    if found["kind"] == "stay_broken" and found["touched"]:
        rows = "; ".join(change_text(table, row) for row in found["rows"])
        return (
            f"the attempt changed {found['touched']} of the rows it should leave"
            f"{first(found['touched'], found['rows'])}: {rows}"
        )
    if found["kind"] == "missing":
        return f"no {table} row where {found['where']}"
    if found["kind"] == "extra_rows":
        rows = "; ".join(
            row_name(table, row["key"])
            + (": " if row["values"] else "")
            + ", ".join(f"{c} {shown(v)}" for c, v in row["values"].items())
            for row in found["rows"]
        )
        return (
            f"{entry['count']} rows match, {target} wanted"
            f"{first(entry['count'], found['rows'])}: {rows}"
        )
    if found["kind"] == "wrong_cells":
        rows = "; ".join(
            row_name(table, row["key"])
            + ": "
            + ", ".join(
                f"{cell['column']} is {shown(cell['actual'])}, wanted {cell['wanted']}"
                for cell in row["cells"]
            )
            for row in found["rows"]
        )
        return (
            f"{found['wrong']} of {found['named']} rows wrong"
            f"{first(found['wrong'], found['rows'])}: {rows}"
        )
    if found["kind"] == "too_few_rows":
        return f"{entry['count']} rows match every condition, {target} wanted"
    return diagnosis.describe(found.get("diagnosis")) or (
        f"count {entry['count']}, {target} wanted"
    )


def explain(result: dict, other: dict, benchmark_passed: bool | None = None) -> str:
    """The attempt's verdict and what went wrong, in a few lines, for a reader or the proposer.

    Args:
        result: As ``score`` returns it.
        other: As ``unasked`` returns it.
        benchmark_passed: The benchmark's own verdict, mentioned where it
            differs from this one.
    """
    if result["passed"]:
        lines = [f"PASS: all {result['total']} requirements met."]
    else:
        broken = result["stays_broken"]
        stays = f", {broken} stay{'s' if broken > 1 else ''} broken" if broken else ""
        lines = [f"FAIL: {result['met']} of {result['total']} requirements met{stays}."]
    for entry in result["requirements"]:
        if not entry["met"]:
            lines.append(
                f"R{entry['requirement']} ({entry['kind']}, {entry['table']}) "
                f'"{entry["text"]}": {failure(entry)}'
            )
    rows = other["other_rows"]
    if rows:
        listed = "; ".join(
            change_text(row["table"], row) for row in rows[:SAMPLE_CHANGES]
        )
        lines.append(
            f"Rows changed that no requirement names ({len(rows)})"
            f"{first(len(rows), rows[:SAMPLE_CHANGES])}: {listed}."
        )
    if other["other_cells"]:
        lines.append(
            f"Other cells changed in rows the request names: {other['other_cells']}."
        )
    if benchmark_passed is not None and benchmark_passed != result["passed"]:
        verdict = "pass" if benchmark_passed else "fail"
        lines.append(f"The benchmark's own checks {verdict} this attempt.")
    return "\n".join(lines)
