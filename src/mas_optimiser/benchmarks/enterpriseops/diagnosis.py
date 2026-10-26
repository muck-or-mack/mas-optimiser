"""Explain a failed count verifier condition by condition.

A verifier in EnterpriseOps-Gym is one SQL query over the final database
state, almost always ``SELECT COUNT(*) FROM <table> WHERE a AND b AND c``
compared with an expected count, so a failure says only that the whole
conjunction did not hold. Which condition failed (was the incident never
created, or created on the wrong channel, or with no priority?) is the first
thing a reader of the run, or an optimiser proposing a fix, needs to know.

The diagnosis keeps the query's SELECT and FROM as written and varies only the
WHERE clause. For a verifier that found too few rows it runs the query with the
first k conditions for k = 1, 2, ... until the count falls short: the
conditions before that point hold and the one at it is the first to fail. It
then tests each remaining condition on top of the holding prefix, so every
failing condition is named, and for a failing ``column = value`` fetches the
column's actual value the same way. A verifier that found too many rows (an
integrity check expecting none) gets the surplus rows instead. A query that is
not a count over a WHERE clause, or whose WHERE has a top-level OR, is left
with expected and actual only, as the executor records them.

Every query runs against the run's own database through the executor's SQL
runner, before the executor deletes that database. A verifier with n
conditions costs at most 2n + 1 cheap local queries, and no model is involved.
"""

import re
from collections.abc import Awaitable, Callable

COUNT_QUERY = re.compile(
    r"^\s*SELECT\s+(?P<count>COUNT\s*\([^()]*\))(?:\s+AS\s+\w+)?"
    r"\s+FROM\s+(?P<source>.+?)\s+WHERE\s+(?P<where>.+?)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
# Clauses after WHERE that the prefix walk cannot keep intact.
TAIL = re.compile(r"\b(GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT)\b", re.IGNORECASE)
# Quoted strings are single tokens so that AND or OR inside them is skipped.
TOKENS = re.compile(r"'[^']*'|\"[^\"]*\"|\(|\)|\bAND\b|\bOR\b", re.IGNORECASE)
# The column (or function of one) a simple condition tests.
COLUMN = re.compile(
    r"^\s*(\w+\([^()]*\)|[\w.]+)\s*(=|!=|<>|>=|<=|>|<|\bIS\b|\bLIKE\b|\bIN\b)",
    re.IGNORECASE,
)
SAMPLE_ROWS = 3
NUMBER_TYPES = (int, float)

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
        cannot walk: not a count, no WHERE, a GROUP BY, HAVING, ORDER BY or
        LIMIT after it, or a top-level OR.
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


async def diagnose(result: dict, run_sql: RunSQL) -> dict:
    """Explain one failed verifier verdict.

    Args:
        result: The executor's record of the verdict: ``query``, ``expected``,
            ``actual`` and ``comparison_type``.
        run_sql: Runs a query against the run's database and returns the value
            the executor's runner would.

    Returns:
        A dict with ``kind`` and the evidence for it:
        ``conditions`` (``held``, the number of leading conditions that hold,
        and ``failed``, each failing condition with the ``actual`` values
        found), ``no_match`` (the first ``condition`` already matches
        nothing), ``too_many_rows`` (sample ``rows``), or ``unsupported``.
    """
    parsed = parse_count_query(result.get("query", ""))
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
            "rows": values(rows) if not isinstance(rows, list) else rows,
        }
    if comparison == "equals":
        enough = lambda n: n >= expected  # noqa: E731
    elif comparison == "greater_than":
        enough = lambda n: n > expected  # noqa: E731
    else:
        return {"kind": "unsupported"}

    async def count_with(where: list[str]) -> bool:
        n = as_number(
            await run_sql(f"SELECT {count} FROM {source} WHERE {' AND '.join(where)}")
        )
        return n is not None and enough(n)

    # Walk the conditions in order; the prefix that still returns enough rows
    # is the ground every later test stands on.
    held = 0
    for k in range(1, len(conditions) + 1):
        if not await count_with(conditions[:k]):
            break
        held = k
    if held == 0:
        return {"kind": "no_match", "condition": conditions[0]}
    prefix = conditions[:held]
    failed = []
    for condition in conditions[held:]:
        if await count_with(prefix + [condition]):
            continue
        entry = {"condition": condition}
        column = COLUMN.match(condition)
        if column:
            found = await run_sql(
                f"SELECT DISTINCT {column.group(1)} FROM {source} "
                f"WHERE {' AND '.join(prefix)} LIMIT {SAMPLE_ROWS}"
            )
            entry["actual"] = values(found)
        failed.append(entry)
    return {"kind": "conditions", "held": held, "failed": failed}


def describe(diagnosis: dict | None) -> str:
    """One line for a CSV cell or a transcript: the failing conditions and what was found."""
    if not diagnosis or diagnosis["kind"] == "unsupported":
        return ""
    if diagnosis["kind"] == "no_match":
        return f"nothing matches {diagnosis['condition']}"
    if diagnosis["kind"] == "too_many_rows":
        rows = diagnosis["rows"]
        return f"too many rows match; {len(rows)} shown: {str(rows)[:120]}"
    parts = []
    for entry in diagnosis["failed"]:
        if "actual" in entry:
            found = ", ".join(str(v) for v in entry["actual"]) or "no value"
            parts.append(f"{entry['condition']} (actual: {found})")
        else:
            parts.append(entry["condition"])
    return "; ".join(parts)
