"""Explain a failed check cell by cell: each condition of its query against the rows the attempt touched.

A check in EnterpriseOps-Gym is one SQL query over the final database state,
almost always ``SELECT COUNT(*) FROM <table> WHERE a AND b AND c`` compared
with an expected count, so a failure says only that the whole conjunction did
not hold. Which condition failed, and what value the attempt left instead, is
the first thing a reader of the attempt, or an optimiser proposing a fix, needs
to know.

The diagnosis takes the query apart at its top-level ANDs (``sql.parse``
does the reading) and tests each condition on its own against the rows the
attempt inserted or updated in the query's table (``states.diff`` says
which), so a condition that an untouched row happens to satisfy is never
mistaken for one the attempt met. For a
failing condition on a column, the column's value in that row is fetched. A
check that counts too many rows (an integrity check expecting none) gets the
surplus rows instead; a check that reads one value (``SELECT col FROM t WHERE
key``) is already wanted against actual; a query with a top-level OR or
another shape is left with expected and actual only, as the executor records
them.

Every query runs against the attempt's own database, on the server before
the executor deletes it or on a local rebuild afterwards. A check with n
conditions on r touched rows (at most ``SAMPLE_ROWS``) costs at most r(2n)
cheap queries, and no model is involved.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``diagnosis``.
"""

from collections.abc import Awaitable, Callable

from mas_optimiser.benchmarks.enterpriseops import sql

SAMPLE_ROWS = 3
NUMBER_TYPES = (int, float)
# The kinds of diagnosis the renderers know; a record from an older evaluation may
# carry another and is shown without one.
KINDS = ("cells", "value", "no_rows", "too_many_rows")

RunSQL = Callable[[str], Awaitable[object]]


def _as_number(value: object) -> float | None:
    """The value as a number.

    Args:
        value: Anything a query or a task can give.

    Returns:
        The number, or ``None`` when the value is not one (``True`` is not).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, NUMBER_TYPES):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _values(result: object) -> list:
    """The cells of a query result as a flat list, however the SQL runner shaped it.

    Args:
        result: What the runner returned: a bare value for a single cell, a
            dict for a single row, a list of dicts for several rows.

    Returns:
        The cells, one per row for a list of rows.
    """
    if isinstance(result, dict):
        return list(result.values())
    if isinstance(result, list):
        return [
            list(row.values())[0] if isinstance(row, dict) else row for row in result
        ]
    return [result]


def _literal(value: object) -> str:
    """A key value as an SQL literal.

    Args:
        value: The value, a number or a string.

    Returns:
        The number as written, or the string quoted with its quotes doubled.
    """
    if isinstance(value, NUMBER_TYPES) and not isinstance(value, bool):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _touched(changes: dict, table: str, key: list[str]) -> list[dict]:
    """The keys of the rows the attempt inserted or updated in ``table``, inserted first.

    Args:
        changes: The attempt's changes, as ``states.diff`` gives them.
        table: The table to look in.
        key: The table's primary key columns.

    Returns:
        One dict of key column to value per row; empty when the table is unchanged.
    """
    change = changes.get(table)
    if not change:
        return []
    inserted = [{k: row[k] for k in key} for row in change["inserted"]]
    return inserted + [update["key"] for update in change["updated"]]


async def diagnose(
    result: dict, changes: dict, keys: dict, columns: dict, run_sql: RunSQL
) -> dict:
    """Explain one failed check.

    Args:
        result: The executor's record of the verdict: ``query``, ``expected``,
            ``actual`` and ``comparison_type``.
        changes: The attempt's changes against the seed, as ``states.diff``
            gives them.
        keys: Each table's primary key columns.
        columns: Each table's columns, from the schema.
        run_sql: Runs a query against the attempt's database and returns the
            value the executor's runner would.

    Returns:
        A dict with ``kind`` and the evidence for it: ``cells`` (``rows``: for
        each of the first ``SAMPLE_ROWS`` touched rows and each condition,
        whether it holds and, when it does not, the ``actual`` values found),
        ``no_rows`` (the attempt touched no
        row of the ``table``), ``too_many_rows`` (sample ``rows``), ``value``
        (one row of wanted against actual), or ``unsupported``.
    """
    parsed = sql.parse(result.get("query", ""), columns)
    if parsed is None:
        return {"kind": "unsupported"}
    table = parsed["table"]
    if parsed["kind"] == "value":
        row = {
            "condition": f"{parsed['column']} = {result.get('expected')!r}",
            "ok": False,
            "actual": [result.get("actual")],
        }
        return {"kind": "value", "table": table, "rows": [row]}
    expected = _as_number(result.get("expected"))
    actual = _as_number(result.get("actual"))
    comparison = result.get("comparison_type", "equals")
    if expected is None or actual is None:
        return {"kind": "unsupported"}
    conditions = parsed["conditions"]
    if comparison == "less_than" or (comparison == "equals" and actual > expected):
        every = " AND ".join(condition["text"] for condition in conditions)
        rows = await run_sql(
            f"SELECT * FROM {parsed['source']} WHERE {every} LIMIT {SAMPLE_ROWS}"
        )
        return {
            "kind": "too_many_rows",
            "table": table,
            "rows": rows if isinstance(rows, list) else [rows],
        }
    if table not in keys:
        return {"kind": "unsupported"}
    rows = _touched(changes, table, keys[table])
    if not rows:
        return {"kind": "no_rows", "table": table}
    cells = []
    for key in rows[:SAMPLE_ROWS]:
        where = " AND ".join(
            f"{parsed['alias']}.{k} = {_literal(v)}" for k, v in key.items()
        )
        for condition in conditions:
            # A condition that reads another table of the join is tested
            # through the join; one on the main table alone, on that table
            # alone, so a join that finds nothing does not fail every
            # condition at once.
            scope = parsed["source"] if condition["joined"] else parsed["main"]
            held = _as_number(
                await run_sql(
                    f"SELECT COUNT(*) FROM {scope} WHERE {where} AND {condition['text']}"
                )
            )
            cell = {"row": key, "condition": condition["text"], "ok": bool(held)}
            if condition["left"] and not cell["ok"]:
                found = await run_sql(
                    f"SELECT DISTINCT {condition['left']} FROM {scope} WHERE {where}"
                )
                cell["actual"] = _values(found)
            cells.append(cell)
    return {"kind": "cells", "table": table, "rows": cells}


def describe(diagnosis: dict | None) -> str:
    """One line for a CSV cell: the failing conditions and what was found.

    Args:
        diagnosis: What ``diagnose`` returned, or ``None`` for a check without one.

    Returns:
        The line; empty for no diagnosis or a kind the renderers do not know.
    """
    if not diagnosis or diagnosis["kind"] not in KINDS:
        return ""
    if diagnosis["kind"] == "no_rows":
        return f"no row inserted or updated in {diagnosis['table']}"
    if diagnosis["kind"] == "too_many_rows":
        rows = diagnosis["rows"]
        return f"too many rows match; {len(rows)} shown: {str(rows)[:120]}"
    parts = []
    for cell in diagnosis["rows"]:
        if cell["ok"]:
            continue
        found = ", ".join(str(v) for v in cell.get("actual", [])) or "no value"
        parts.append(f"{cell['condition']} (actual: {found})")
    return "; ".join(dict.fromkeys(parts))
