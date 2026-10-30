"""Explain a failed check cell by cell: each condition of its query against the rows the attempt touched.

A check in EnterpriseOps-Gym is one SQL query over the final database state,
almost always ``SELECT COUNT(*) FROM <table> WHERE a AND b AND c`` compared
with an expected count, so a failure says only that the whole conjunction did
not hold. Which condition failed, and what value the attempt left instead, is
the first thing a reader of the run, or an optimiser proposing a fix, needs
to know.

The diagnosis takes the query apart at its top-level ANDs and tests each
condition on its own against the rows the attempt inserted or updated in the
query's table (``states.diff`` says which), so a condition that an untouched
row happens to satisfy is never mistaken for one the attempt met. For a
failing condition on a column, the column's value in that row is fetched. A
check that counts too many rows (an integrity check expecting none) gets the
surplus rows instead; a check that reads one value (``SELECT col FROM t WHERE
key``) is already wanted against actual; a query with a top-level OR or
another shape is left with expected and actual only, as the executor records
them.

Every query runs against the attempt's own database, on the server before
the executor deletes it or on a local rebuild afterwards. A check with n
conditions on r touched rows costs at most r(2n) cheap queries, and no model
is involved.
"""

import re
from collections.abc import Awaitable, Callable

COUNT_QUERY = re.compile(
    r"^\s*SELECT\s+(?P<count>COUNT\s*\([^()]*\))(?:\s+AS\s+\w+)?"
    r"\s+FROM\s+(?P<source>.+?)\s+WHERE\s+(?P<where>.+?)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
VALUE_QUERY = re.compile(
    r"^\s*SELECT\s+(?P<column>[\w.]+)\s+FROM\s+(?P<table>\w+)\s+WHERE\b", re.IGNORECASE
)
# Clauses after WHERE that the condition tests cannot keep intact.
TAIL = re.compile(r"\b(GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT)\b", re.IGNORECASE)
# Quoted strings are single tokens so that AND or OR inside them is skipped.
TOKENS = re.compile(r"'[^']*'|\"[^\"]*\"|\(|\)|\bAND\b|\bOR\b", re.IGNORECASE)
# The column (or function of one) a simple condition tests.
COLUMN = re.compile(
    r"^\s*(\w+\([^()]*\)|[\w.]+)\s*(=|!=|<>|>=|<=|>|<|\bIS\b|\bLIKE\b|\bIN\b)",
    re.IGNORECASE,
)
# The first table of a FROM clause, with its alias if it has one.
MAIN_TABLE = re.compile(
    r"^\s*(?P<table>\w+)(?:\s+(?:AS\s+)?(?P<alias>\w+))?", re.IGNORECASE
)
SAMPLE_ROWS = 3
NUMBER_TYPES = (int, float)
# The kinds of diagnosis the renderers know; a record from an older run may
# carry another and is shown without one.
KINDS = ("cells", "value", "no_rows", "too_many_rows")

RunSQL = Callable[[str], Awaitable[object]]


def split_conditions(where: str) -> list[str] | None:
    """Split a WHERE clause at its top-level ANDs; ``None`` if it has a top-level OR."""
    cuts = []
    depth = 0
    for match in TOKENS.finditer(where):
        token = match.group().upper()
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0 and token == "OR":
            return None
        elif depth == 0 and token == "AND":
            cuts.append((match.start(), match.end()))
    pieces = []
    start = 0
    for begin, end in cuts:
        pieces.append(where[start:begin])
        start = end
    pieces.append(where[start:])
    return [piece.strip() for piece in pieces]


def parse_count_query(query: str) -> tuple[str, str, list[str]] | None:
    """Take a count query apart into its COUNT expression, FROM clause and conditions.

    Returns:
        ``(count, source, conditions)``, or ``None`` for a query the diagnosis
        cannot take apart: not a count, no WHERE, a GROUP BY, HAVING, ORDER BY
        or LIMIT after it, or a top-level OR.
    """
    match = COUNT_QUERY.match(query)
    if not match or TAIL.search(match["where"]):
        return None
    conditions = split_conditions(match["where"])
    if conditions is None:
        return None
    return match["count"], match["source"], conditions


def as_number(value: object) -> float | None:
    """The value as a number, or ``None`` if it is not one (``True`` is not)."""
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


def values(result: object) -> list:
    """The cells of a query result as a flat list, however the SQL runner shaped it.

    The executor's runner returns a bare value for a single cell, a dict for a
    single row and a list of dicts for several rows.
    """
    if isinstance(result, dict):
        return list(result.values())
    if isinstance(result, list):
        return [
            list(row.values())[0] if isinstance(row, dict) else row for row in result
        ]
    return [result]


def literal(value: object) -> str:
    """A key value as an SQL literal."""
    if isinstance(value, NUMBER_TYPES) and not isinstance(value, bool):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def touched(changes: dict, table: str, key: list[str]) -> list[dict]:
    """The keys of the rows the attempt inserted or updated in ``table``, inserted first."""
    change = changes.get(table)
    if not change:
        return []
    inserted = [{k: row[k] for k in key} for row in change["inserted"]]
    return inserted + [update["key"] for update in change["updated"]]


async def diagnose(result: dict, changes: dict, keys: dict, run_sql: RunSQL) -> dict:
    """Explain one failed check.

    Args:
        result: The executor's record of the verdict: ``query``, ``expected``,
            ``actual`` and ``comparison_type``.
        changes: The attempt's changes against the seed, as ``states.diff``
            gives them.
        keys: Each table's primary key columns.
        run_sql: Runs a query against the attempt's database and returns the
            value the executor's runner would.

    Returns:
        A dict with ``kind`` and the evidence for it: ``cells`` (``rows``: for
        each touched row and condition, whether it holds and, when it does
        not, the ``actual`` values found), ``no_rows`` (the attempt touched no
        row of the ``table``), ``too_many_rows`` (sample ``rows``), ``value``
        (one row of wanted against actual), or ``unsupported``.
    """
    query = result.get("query", "")
    value = VALUE_QUERY.match(query)
    if value:
        row = {
            "condition": f"{value['column']} = {result.get('expected')!r}",
            "ok": False,
            "actual": [result.get("actual")],
        }
        return {"kind": "value", "table": value["table"], "rows": [row]}
    parsed = parse_count_query(query)
    expected = as_number(result.get("expected"))
    actual = as_number(result.get("actual"))
    comparison = result.get("comparison_type", "equals")
    if parsed is None or expected is None or actual is None:
        return {"kind": "unsupported"}
    count, source, conditions = parsed
    if comparison == "less_than" or (comparison == "equals" and actual > expected):
        rows = await run_sql(
            f"SELECT * FROM {source} WHERE {' AND '.join(conditions)} LIMIT {SAMPLE_ROWS}"
        )
        return {
            "kind": "too_many_rows",
            "table": MAIN_TABLE.match(source)["table"],
            "rows": rows if isinstance(rows, list) else [rows],
        }
    main = MAIN_TABLE.match(source)
    if main is None or main["table"] not in keys:
        return {"kind": "unsupported"}
    table, alias = main["table"], main["alias"] or main["table"]
    rows = touched(changes, table, keys[table])
    if not rows:
        return {"kind": "no_rows", "table": table}
    cells = []
    for key in rows[:SAMPLE_ROWS]:
        where = " AND ".join(f"{alias}.{k} = {literal(v)}" for k, v in key.items())
        for condition in conditions:
            # A condition on another table of a join is tested through the
            # join; one on the main table, on that table alone, so a join
            # that finds nothing does not fail every condition at once.
            column = COLUMN.match(condition)
            other = (
                column and "." in column[1] and not column[1].startswith(f"{alias}.")
            )
            scope = source if other else f"{table} {alias}"
            held = as_number(
                await run_sql(
                    f"SELECT COUNT(*) FROM {scope} WHERE {where} AND {condition}"
                )
            )
            cell = {"row": key, "condition": condition, "ok": bool(held)}
            if column and not cell["ok"]:
                found = await run_sql(
                    f"SELECT DISTINCT {column[1]} FROM {scope} WHERE {where}"
                )
                cell["actual"] = values(found)
            cells.append(cell)
    return {"kind": "cells", "table": table, "rows": cells}


def describe(diagnosis: dict | None) -> str:
    """One line for a CSV cell: the failing conditions and what was found."""
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
