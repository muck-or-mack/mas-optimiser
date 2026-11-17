"""Write what an evaluation produced in the form its readers need: a page per attempt, tables per evaluation, a summary.

An attempt's record is JSON for code. The files here are for a person or a
proposing model reading the results. Beside each record, ``attempt-<n>.md``:
the request, what the agent did turn by turn, what changed against the seed,
and every check with, under a failure, each condition marked ok or WRONG
against the row the agent wrote and the turn that wrote it. Beside
``tasks.csv`` and ``checks.csv``, ``cells.csv`` (one row per failed
check, touched row and condition: what was wanted, what was found and the
call the value came from) and ``tables.csv`` (one row per attempt and table
that a check counts rows of or that the attempt changed, with whether the
attempt got the table right). And ``summary.md``, a page that says how the
attempt went and what went wrong, by table and column.

The trace from a failed check back to the agent's turns is mechanical. The
calls that write the check's table are those with a writing verb in the
tool's name and the table's words in the rest of it (``create_incident``
writes ``incident``) or, when none is named so, the writing calls whose
result echoes the table's key column. Among them, the calls whose arguments
or result name the touched row's key (``hr_case_id: 61``) are the ones
linked to the check; when none does, all of them are. A cell's value was
``passed`` by the last linked call whose arguments name its column; a
column no call names came from the call that wrote the row, which ``left
out`` an argument its tool takes or has ``no argument`` of that name. The
failure of a check is ``wrong values`` when the row is there with wrong
cells and ``too many rows`` when more rows match than it allows; else
``table not written`` when no call wrote the table, ``call failed`` when
one of those calls returned an error, and ``no row written`` when they all
succeeded and no row of the table changed.

A table's result puts the checks on it together with what the attempt
changed in it: ``correct`` when every check on it passed, ``wrong`` when
one failed and the attempt changed the table, ``missed`` when one failed
and the attempt changed nothing in it. A changed table that no check
counts rows of is ``not asked for``, or ``read by a check`` when a check
on another table reads it.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``report``.
"""

import json
import re
import textwrap
from collections import Counter

from mas_optimiser import markdown
from mas_optimiser.benchmarks.enterpriseops import diagnosis, sql

CELL_COLUMNS = (
    "task_id",
    "attempt",
    "check",
    "table",
    "row",
    "column",
    "condition",
    "wanted",
    "found",
    "ok",
    "how",
    "turn",
    "tool",
)
TABLE_COLUMNS = (
    "task_id",
    "attempt",
    "table",
    "checks",
    "checks_passed",
    "inserted",
    "updated",
    "deleted",
    "result",
)
# Tool name verbs that write to the database.
WRITING = {
    "create",
    "update",
    "add",
    "remove",
    "delete",
    "link",
    "send",
    "register",
    "assign",
    "set",
    "onboard",
    "offboard",
    "enlist",
    "map",
}
WORST_TASKS = 10
# Items of a list shown on an attempt's page before the rest are counted.
LISTED = 10
# The longest cell of a check's table: every row of a table is padded to its
# widest cell, and the whole value is in the turn that passed it.
TABLE_CHARS = 80
FAILURES = (
    "table not written",
    "call failed",
    "no row written",
    "wrong values",
    "too many rows",
)
# How a lookup says it found no record, in the three servers' wordings: an
# error code, a message, a validation type.
NOT_FOUND = re.compile(r"not[ _]found", re.IGNORECASE)


def _result_text(result: dict) -> str:
    """A tool result's payload as text; MCP servers wrap it as content blocks.

    Args:
        result: The tool result as the record keeps it (``success`` and ``result``).

    Returns:
        The first content block's text, a bare string as it is, or any other
        payload as JSON.
    """
    content = result.get("result", {})
    if isinstance(content, dict) and content.get("content"):
        return str(content["content"][0].get("text", ""))
    return content if isinstance(content, str) else json.dumps(content)


def succeeded(result: dict) -> bool:
    """Whether a tool call did its work: the request got through and the tool did not answer with an error.

    The client's ``success`` says only that the server answered; a tool that
    refused (an unknown user, a missing field) answers with ``isError``.

    Args:
        result: The tool result as the record keeps it.

    Returns:
        ``True`` when both held.
    """
    inner = result.get("result")
    return bool(result.get("success")) and not (
        isinstance(inner, dict) and inner.get("isError")
    )


def calls(execution: dict) -> list[dict]:
    """The agent's tool calls in order, each with its turn, arguments and result.

    Args:
        execution: The executor's account of the attempt (its ``conversation_flow``).

    Returns:
        One dict per call: ``turn``, ``tool``, ``args`` and, once its result
        arrived, ``success``, ``result`` (the result's text) and
        ``nothing_found``: a lookup that answered that no such record exists,
        which the server reports as an error and which is the lookup doing
        its work.
    """
    out, pending, turn = [], [], 0
    for step in execution.get("conversation_flow", []):
        if step["type"] == "ai_message":
            turn += 1
            pending = [
                {"turn": turn, "tool": c["name"], "args": c.get("args") or {}}
                for c in step.get("tool_calls") or []
            ]
            out += pending
        elif step["type"] == "tool_result" and pending:
            call = pending.pop(0)
            result = step.get("result") or {}
            call["success"] = succeeded(result)
            call["result"] = _result_text(result)
            call["nothing_found"] = (
                not call["success"]
                and call["tool"].split("_")[0] not in WRITING
                and bool(NOT_FOUND.search(call["result"]))
            )
    return out


def _names(call: dict, column: str, value: object) -> bool:
    """Whether a call's arguments or result carry ``column`` with ``value``.

    Args:
        call: A call from ``calls``.
        column: The column name.
        value: The value looked for.

    Returns:
        ``True`` when either the arguments or the result name the pair.
    """
    pattern = re.compile(
        rf"\b{re.escape(column)}\W{{1,4}}['\"]?{re.escape(str(value))}['\"]?(?!\w)"
    )
    return bool(
        pattern.search(json.dumps(call["args"]))
        or pattern.search(call.get("result", ""))
    )


# Words in a tool's name that say nothing about the table it writes.
FILLER = {
    "new",
    "to",
    "for",
    "by",
    "details",
    "a",
    "an",
    "the",
    "with",
    "from",
    "in",
    "on",
}


def writes(tool: str, table: str) -> bool:
    """Whether a tool writes a table, going by its name: a writing verb, then the table's words.

    Args:
        tool: The tool's name (``create_incident``).
        table: The table (``incident``).

    Returns:
        ``True`` when the name says so.
    """
    verb, *rest = tool.split("_")
    return verb in WRITING and set(rest) - FILLER == set(table.split("_"))


def writers_of(agent_calls: list[dict], table: str, key: list[str]) -> list[dict]:
    """The calls that write ``table``: writing calls named after it, or, failing that, whose result echoes its key column.

    The name is the primary rule because arguments mislead: a task is created
    with its case's id, which does not make the call a write to the case.

    Args:
        agent_calls: The calls from ``calls``.
        table: The table a check queries.
        key: The table's primary key columns.

    Returns:
        The calls, in order; empty when none wrote the table.
    """
    named = [c for c in agent_calls if writes(c["tool"], table)]
    return named or [
        c
        for c in agent_calls
        if c["tool"].split("_")[0] in WRITING
        and c.get("success")
        and any(f'"{k}"' in c.get("result", "") for k in key)
    ]


def trace(
    result: dict, agent_calls: list[dict], keys: dict, arguments: dict[str, list[str]]
) -> dict:
    """How a failed check's row was handled: the calls that wrote it, where each cell's value came from, the failure.

    Args:
        result: A failed check's result with its ``diagnosis``.
        agent_calls: The calls from ``calls``.
        keys: Each table's primary key columns.
        arguments: Each tool's argument names; empty when the evaluation
            saved no tool list.

    Returns:
        ``failure`` (one of ``FAILURES``; empty when the diagnosis names no
        table), ``related`` (the linked calls: turn, tool, success) and
        ``set_by``: each column a condition tests to the ``turn`` and
        ``tool`` its value came from and ``how``. ``passed``, with the
        ``value``, when a linked call's arguments name the column; otherwise
        the call that wrote the row ``left out`` an argument its tool takes,
        or its tool has ``no argument`` of that name, or the column was
        ``not passed`` and the tool's arguments are not known.
    """
    found = result.get("diagnosis") or {}
    table = found.get("table", "")
    if not table:
        return {"failure": "", "related": [], "set_by": {}}
    writers = writers_of(agent_calls, table, keys.get(table, []))
    # Only a cells diagnosis carries condition rows with the touched row's
    # key; a too_many_rows one carries the surplus rows themselves.
    conditions = (
        found.get("rows", []) if found.get("kind") in ("cells", "value") else []
    )
    # Among the calls that write the table, those that name the touched
    # row's key; all of them when none does (a result that echoes no id).
    rows = [row["row"] for row in conditions if "row" in row]
    pairs = {(k, v) for row in rows for k, v in row.items()}
    linked = [c for c in writers if any(_names(c, k, v) for k, v in pairs)] or writers
    # The call that wrote the row: the first linked one that succeeded.
    writer = next((c for c in linked if c.get("success")), None)
    set_by = {}
    for column in dict.fromkeys(row["column"] for row in conditions):
        passing = [c for c in linked if column in c["args"]]
        if not column or not (passing or writer):
            continue
        call = passing[-1] if passing else writer
        set_by[column] = {"turn": call["turn"], "tool": call["tool"]}
        if passing:
            set_by[column] |= {"how": "passed", "value": call["args"][column]}
        elif call["tool"] not in arguments:
            set_by[column]["how"] = "not passed"
        else:
            takes = column in arguments[call["tool"]]
            set_by[column]["how"] = "left out" if takes else "no argument"
    if found.get("kind") in ("cells", "value"):
        failure = "wrong values"
    elif found.get("kind") == "too_many_rows":
        failure = "too many rows"
    elif not writers:
        failure = "table not written"
    elif any(call.get("success") is False for call in writers):
        failure = "call failed"
    else:
        failure = "no row written"
    return {
        "failure": failure,
        "related": [
            {"turn": c["turn"], "tool": c["tool"], "success": c.get("success")}
            for c in linked
        ],
        "set_by": set_by,
    }


def _clip(value: object, max_chars: int | None) -> str:
    """A value as text: one line of at most ``max_chars`` characters, or whole.

    Args:
        value: A string, taken as it is, or any other value, taken as JSON.
        max_chars: The length kept before `` ...`` is added, with runs of
            whitespace collapsed first; ``None`` keeps the text whole, line
            breaks included.

    Returns:
        The text.
    """
    text = value if isinstance(value, str) else json.dumps(value)
    if max_chars is None:
        return text
    text = " ".join(text.split())
    return text if len(text) <= max_chars else text[:max_chars] + " ..."


def _fields(data: object, max_chars: int | None) -> list[str]:
    """A call's arguments, a tool's answer or a row, one line per field.

    Args:
        data: A mapping (one aligned ``name = value`` line per field, a list
            value with its items beneath), a list (one line per item, the
            first ``LISTED``) or anything else (one line).
        max_chars: The length a value is clipped to, each on its own, so a
            long description never hides the fields after it; ``None`` keeps
            every value whole, a line break continuing on an indented line.

    Returns:
        The lines.
    """
    if isinstance(data, list):
        more = len(data) - LISTED
        return [_clip(item, max_chars) for item in data[:LISTED]] + (
            [f"... {more} more"] if more > 0 else []
        )
    if not isinstance(data, dict):
        return [_clip(data, max_chars)]
    width = max(map(len, data), default=0)
    lines = []
    for name, value in data.items():
        if isinstance(value, list) and value:
            count = f"{len(value)} item{'s' if len(value) > 1 else ''}"
            lines.append(f"{name:<{width}} = {count}")
            lines += [f"  {line}" for line in _fields(value, max_chars)]
        else:
            first, *rest = _clip(value, max_chars).split("\n")
            lines.append(f"{name:<{width}} = {first}")
            lines += [" " * (width + 3) + line for line in rest]
    return lines


def _own_table(result: dict) -> str:
    """The table a check belongs to: the one whose rows it counts or reads a value from.

    Args:
        result: A check's result with its ``query``.

    Returns:
        The table in lower case; for a query that cannot be taken apart, the
        table it names when it names one only, and empty otherwise.
    """
    query = result.get("query", "")
    parsed = sql.parse(query, {})
    if parsed:
        return parsed["table"].lower()
    named = sql.tables(query)
    return next(iter(named)) if len(named) == 1 else ""


def _check_lines(
    result: dict,
    changes: dict | None,
    max_chars: int,
    given: list[str],
    arguments: dict[str, list[str]],
) -> list[str]:
    """Under a failed check: what it wants against what the attempt left, and where each value came from.

    Args:
        result: A failed check's result with its ``diagnosis`` and ``trace``.
        changes: The attempt's changes, as ``states.diff`` gives them, or
            ``None`` when they were not read.
        max_chars: The length a long value is clipped to, ``TABLE_CHARS`` at
            most.
        given: The tools the task was given.
        arguments: Each tool's argument names; empty when the evaluation
            saved no tool list.

    Returns:
        The lines, as Markdown; empty for a kind with nothing to show.
    """
    found = result.get("diagnosis") or {}
    kind = found.get("kind")
    followed = result.get("trace") or {}
    linked = followed.get("related", [])

    def cell(value: object) -> str:
        return _clip(value, min(max_chars, TABLE_CHARS)).replace("|", "\\|")

    if kind in ("cells", "value"):
        touched = (changes or {}).get(found["table"], {})
        writer = next((c for c in linked if c.get("success")), None)
        by_row: dict[str, list[dict]] = {}
        for row in found["rows"]:
            label = ", ".join(f"{k} {v}" for k, v in row.get("row", {}).items())
            by_row.setdefault(label, []).append(row)
        lines = []
        for label, rows in by_row.items():
            key = rows[0].get("row")
            # An updated row shows each changed cell's value before.
            old = key and next(
                (u["changes"] for u in touched.get("updated", []) if u["key"] == key),
                None,
            )
            if key:
                how = "in the seed, updated" if old is not None else "inserted"
                where = (
                    f" at turn {writer['turn']} by `{writer['tool']}`" if writer else ""
                )
                lines += [f"Row `{label}`: {how}{where}.", ""]
            body = []
            for row in rows:
                column = row["column"]
                setter = followed.get("set_by", {}).get(column, {})
                how = setter.get("how")
                if how == "passed":
                    source = f"passed at turn {setter['turn']}: `{setter['tool']}({column}={setter['value']!r})`"
                elif how == "left out":
                    source = f"left out at turn {setter['turn']}: `{setter['tool']}` takes `{column}`"
                elif how == "no argument":
                    source = (
                        f"set by the server: `{setter['tool']}` takes no `{column}`"
                    )
                else:
                    source = "not passed in any call" if how else ""
                after = ", ".join(_shown(v) for v in row.get("actual", []))
                cells = [f"`{column}`" if column else "(several)", cell(row["wanted"])]
                if old is not None:
                    cells.append(
                        cell(_shown(old[column][0])) if column in old else "unchanged"
                    )
                cells += [cell(after), "ok" if row["ok"] else "WRONG", cell(source)]
                body.append(cells)
            before = ["before"] if old is not None else []
            header = [
                "column",
                "wanted",
                *before,
                "found",
                "ok",
                "where the value came from",
            ]
            lines += [markdown.table(header, body), ""]
        return lines[:-1]
    if kind == "no_rows":
        table = found["table"]
        writing = [tool for tool in given if writes(tool, table)]
        if linked:
            tried = ", ".join(
                f"turn {c['turn']} `{c['tool']}` "
                + ("failed" if c.get("success") is False else "succeeded")
                for c in linked
            )
            lines = [
                f"No row of `{table}` was inserted or updated. Calls that write it: {tried}."
            ]
        elif writing:
            tools = " and ".join(f"`{tool}`" for tool in writing)
            lines = [
                f"No call wrote `{table}`. The task was given {tools} to write it."
            ]
        else:
            lines = [f"No call wrote `{table}`."]
        cut = sql.clauses(result.get("query", ""))
        if cut:
            body = []
            for condition in cut[1]:
                column = sql.column_of(condition)
                takers = [t for t in writing if column in arguments.get(t, ())]
                known = any(tool in arguments for tool in writing)
                body.append(
                    [
                        cell(condition),
                        "no row",
                        ", ".join(f"`{t}`" for t in takers)
                        or ("none" if known and column else ""),
                    ]
                )
            lines += [
                "",
                markdown.table(
                    ["check wants", "found", "tool given that takes it"], body
                ),
            ]
        return lines
    if kind == "too_many_rows":
        return [_clip(diagnosis.describe(found), max_chars)]
    return []


def _shown(value: object) -> str:
    """A database value for a table cell; an empty cell says so.

    Args:
        value: The value as the database returned it.

    Returns:
        ``(empty)`` for ``None``, else the value as text.
    """
    return "(empty)" if value is None else str(value)


def attempt_text(
    task_id: str,
    attempt: int,
    execution: dict,
    changes: dict | None,
    given: list[str],
    arguments: dict[str, list[str]],
    max_chars: int = 300,
) -> str:
    """One attempt as a page: the request, how it did table by table, the tools it had, every check, the turns and the rows it changed.

    The page goes from the whole to the detail. After the request, one row
    per table the checks look at says whether the attempt got it right
    (``table_rows``). Then the tools the task was given, the turns each was
    called at and the arguments each takes, and every check with, under a failure, what it wanted
    against what was found and where each value came from. Then the turns:
    one reply of the model each, its text as a quote and each tool call it
    asked for with the arguments, never cut, and what came back (fields the
    tool left empty are not shown; the record has them). Last, every row the
    attempt changed: an inserted row with all its columns, an updated row
    with each changed cell before and after, a deleted row by its key.

    Args:
        task_id: The task's dataset ID.
        attempt: The attempt number, from 1.
        execution: The executor's account of the attempt.
        changes: The attempt's changes, as ``states.diff`` gives them, or
            ``None`` when they were not read.
        given: The tools the task was given.
        arguments: Each tool's argument names; empty when the evaluation
            saved no tool list.
        max_chars: The length one field of an answer, one cell of a row or
            one cell of a check's table is clipped to.

    Returns:
        The page as Markdown.
    """
    results = execution.get("verification_results", {})
    # An excluded check is listed on the page and is not counted.
    scored = [r for r in results.values() if r.get("patch") != "excluded"]
    passed = sum(1 for r in scored if r["passed"])
    verdict = "PASS" if execution.get("overall_success") else "FAIL"
    lines = [
        f"# {task_id}, attempt {attempt}: {verdict} ({passed} of {len(scored)} checks passed)",
        "",
    ]
    flow = execution.get("conversation_flow", [])
    if execution.get("error") and not flow:
        return "\n".join(
            lines + [f"Raised before it started: {execution['error']}", ""]
        )
    request = next((s["content"] for s in flow if s["type"] == "user_message"), "")
    lines += ["## Request", "", request, "", "## Tables the checks look at", ""]
    if changes is None:
        lines += ["The attempt's changes were not saved.", ""]
    else:
        tables = table_rows(task_id, attempt, results, changes)
        names: dict[str, list[str]] = {}
        for name, result in results.items():
            if result.get("patch") != "excluded":
                names.setdefault(_own_table(result), []).append(name)
        judged = [t["result"] for t in tables if t["checks"]]
        counts = ", ".join(
            f"{judged.count(r)} {r}" for r in ("correct", "wrong", "missed")
        )
        body = [
            [
                f"`{t['table']}`",
                ", ".join(names.get(t["table"], [])) or "none",
                ", ".join(
                    f"{t[how]} {how}"
                    for how in ("inserted", "updated", "deleted")
                    if t[how]
                )
                or "none",
                t["result"],
            ]
            for t in tables
        ]
        lines += [
            f"{len(judged)} tables with a check on them: {counts}.",
            "",
            markdown.table(["table", "checks on it", "rows changed", "result"], body),
            "",
        ]
        if names.get(""):
            lines += [
                "Checks whose table could not be read from their query: "
                + ", ".join(names[""])
                + ".",
                "",
            ]
    agent_calls = calls(execution)
    turns: dict[str, list[str]] = {}
    for call in agent_calls:
        turns.setdefault(call["tool"], []).append(str(call["turn"]))
    # Every tool when the task has a few; only those called when it was
    # given the whole toolbox.
    listed = given if len(given) <= 2 * LISTED else [t for t in given if t in turns]
    lines += ["## Tools the task was given", ""]
    if len(listed) < len(given):
        lines += [
            f"{len(given)} tools; the {len(listed)} the agent called are shown, "
            "the rest are in `tools.json`.",
            "",
        ]
    lines += [
        markdown.table(
            ["tool", "called at turn", "arguments"],
            [
                [
                    f"`{tool}`",
                    ", ".join(turns.get(tool, [])) or "never",
                    str(len(arguments[tool])) if tool in arguments else "not saved",
                ]
                for tool in listed
            ],
        ),
        "",
    ]
    if any(tool in arguments for tool in listed):
        # Each tool with the arguments it takes, wrapped: one tool takes 26.
        lines += ["What each tool takes:", "", "```"]
        lines += [
            textwrap.fill(
                f"{tool}({', '.join(arguments[tool])})",
                width=100,
                subsequent_indent="    ",
            )
            for tool in listed
            if tool in arguments
        ]
        lines += ["```", ""]
    lines += ["## Checks", ""]
    for name, result in results.items():
        excluded = result.get("patch") == "excluded"
        if excluded:
            verdict = "pass" if result["benchmark_passed"] else "fail"
            lines.append(
                f"### EXCLUDED  {name} (no correct attempt can satisfy it; "
                f"the benchmark's own verdict: {verdict})"
            )
        else:
            flag = "PASS" if result["passed"] else "FAIL"
            patched = " (repaired)" if result.get("patch") == "repaired" else ""
            lines.append(
                f"### {flag}  {name}{patched} (expected {result.get('expected')!r}, got {result.get('actual')!r})"
            )
        lines.append("")
        if result.get("query"):
            lines += ["```sql", " ".join(result["query"].split()), "```", ""]
        if not excluded and not result["passed"]:
            lines += [
                *_check_lines(result, changes, max_chars, given, arguments),
                "",
            ]
    lines += ["## What the agent did", ""]
    replies = [step for step in flow if step["type"] == "ai_message"]
    for turn, reply in enumerate(replies, start=1):
        lines += [f"### Turn {turn}", ""]
        if reply.get("content"):
            lines += [f"> {line}".rstrip() for line in reply["content"].splitlines()]
            lines.append("")
        for call in agent_calls:
            if call["turn"] != turn:
                continue
            flag = (
                "nothing found"
                if call.get("nothing_found")
                else {True: "ok", False: "ERROR"}.get(call.get("success"), "no answer")
            )
            lines += [f"`{call['tool']}` → {flag}", ""]
            if call["args"]:
                lines += ["```", *_fields(call["args"], None), "```", ""]
            if "result" not in call:
                continue
            try:
                answer = json.loads(call["result"])
            except ValueError:
                answer = call["result"]
            if isinstance(answer, dict):
                answer = {k: v for k, v in answer.items() if v is not None}
            lines += ["returned", "", "```", *_fields(answer, max_chars), "```", ""]
    lines += ["## Rows the attempt changed", ""]
    if changes is None:
        lines.append("not saved")
    elif not changes:
        lines.append("none")
    for table, change in (changes or {}).items():
        lines += [f"### {table}"]
        # An inserted row is labelled by its first column, the key in
        # every table of the three schemas; the others carry their key.
        touched = (
            [("inserted", next(iter(r.items())), r) for r in change["inserted"]]
            + [("updated", next(iter(u["key"].items())), u) for u in change["updated"]]
            + [("deleted", next(iter(k.items())), None) for k in change["deleted"]]
        )
        for how, (column, value), row in touched[:LISTED]:
            lines += [
                "",
                f"Row `{column} {value}`, {how}" + ("." if how == "deleted" else ":"),
            ]
            if how == "inserted":
                shown = {k: _shown(v) for k, v in row.items()}
                lines += ["", "```", *_fields(shown, max_chars), "```"]
            elif how == "updated":
                width = max(map(len, row["changes"]), default=0)
                lines += (
                    ["", "```"]
                    + [
                        f"{c:<{width}} = {_clip(_shown(before), max_chars)} -> {_clip(_shown(after), max_chars)}"
                        for c, (before, after) in row["changes"].items()
                    ]
                    + ["```"]
                )
        if len(touched) > LISTED:
            lines += ["", f"... {len(touched) - LISTED} more rows"]
        lines.append("")
    return "\n".join(lines)


def cell_rows(task_id: str, attempt: int, results: dict) -> list[dict]:
    """``cells.csv`` rows: one per failed check, touched row and condition.

    Args:
        task_id: The task's dataset ID.
        attempt: The attempt number, from 1.
        results: The attempt's ``verification_results``, each failure with its
            diagnosis and trace.

    Returns:
        The rows (``CELL_COLUMNS``).
    """
    rows = []
    for name, result in results.items():
        found = result.get("diagnosis") or {}
        if result["passed"] or found.get("kind") not in ("cells", "value"):
            continue
        set_by = (result.get("trace") or {}).get("set_by", {})
        for row in found["rows"]:
            setter = set_by.get(row["column"], {})
            rows.append(
                {
                    "task_id": task_id,
                    "attempt": attempt,
                    "check": name,
                    "table": found.get("table", ""),
                    "row": ", ".join(f"{k}={v}" for k, v in row.get("row", {}).items()),
                    "column": row["column"],
                    "condition": row["condition"],
                    "wanted": row["wanted"],
                    "found": ", ".join(str(v) for v in row.get("actual", [])),
                    "ok": row["ok"],
                    "how": setter.get("how", ""),
                    "turn": setter.get("turn", ""),
                    "tool": setter.get("tool", ""),
                }
            )
    return rows


def table_rows(
    task_id: str, attempt: int, results: dict, changes: dict | None
) -> list[dict]:
    """``tables.csv`` rows: one per table a check counts rows of or the attempt changed, and how the attempt did on it.

    A check belongs to the one table whose rows it counts or reads a value
    from; a check whose query cannot be taken apart belongs to the table it
    names when it names one only, and to none otherwise. An excluded check
    is not counted.

    Args:
        task_id: The task's dataset ID.
        attempt: The attempt number, from 1.
        results: The attempt's ``verification_results``.
        changes: The attempt's changes, as ``states.diff`` gives them, or
            ``None`` when they were not read.

    Returns:
        The rows (``TABLE_COLUMNS``), the tables with checks first; empty
        when the changes were not read.
    """
    if changes is None:
        return []
    verdicts: dict[str, list[bool]] = {}
    read = set()
    for result in results.values():
        query = result.get("query", "")
        named = sql.tables(query)
        read |= named
        if result.get("patch") == "excluded":
            continue
        own = _own_table(result)
        if own:
            verdicts.setdefault(own, []).append(bool(result["passed"]))
    changed = {table.lower(): change for table, change in changes.items()}
    rows = []
    for table in [*verdicts, *(t for t in changed if t not in verdicts)]:
        counts = {
            how: len(changed.get(table, {}).get(how, []))
            for how in ("inserted", "updated", "deleted")
        }
        passed = verdicts.get(table, [])
        if not passed:
            result = "read by a check" if table in read else "not asked for"
        elif all(passed):
            result = "correct"
        else:
            result = "wrong" if any(counts.values()) else "missed"
        rows.append(
            {
                "task_id": task_id,
                "attempt": attempt,
                "table": table,
                "checks": len(passed),
                "checks_passed": sum(passed),
            }
            | counts
            | {"result": result}
        )
    return rows


def summary(
    name: str,
    settings: dict,
    outcomes: list[dict],
    check_rows: list[dict],
    cells: list[dict],
    tables: list[dict],
) -> str:
    """``summary.md``: how the evaluation went and what went wrong, by table and column.

    Args:
        name: The evaluation folder's name.
        settings: ``domain``, ``mode`` and ``model``, for the heading.
        outcomes: The ``tasks.csv`` rows, one per attempt.
        check_rows: The ``checks.csv`` rows.
        cells: The ``cells.csv`` rows.
        tables: The ``tables.csv`` rows.

    Returns:
        The page as Markdown.
    """
    attempts = len(outcomes)
    passed = sum(r["success"] for r in outcomes)
    by_task: dict[str, list[dict]] = {}
    for r in outcomes:
        by_task.setdefault(r["task_id"], []).append(r)
    every = sum(all(r["success"] for r in rs) for rs in by_task.values())
    checks_passed = sum(r["checks_passed"] for r in outcomes)
    checks_total = sum(r["checks_total"] for r in outcomes)
    errored = sum(r.get("tool_errors", 0) > 0 for r in outcomes)
    unasked = {
        (t["task_id"], t["attempt"]) for t in tables if t["result"] == "not asked for"
    }
    failed = [c for c in check_rows if c["passed"] is False]
    failures = Counter(c["failure"] for c in failed if c["failure"])
    unwritten = Counter(
        c["table"] for c in failed if c["failure"] == "table not written"
    )
    broke = Counter(c["table"] for c in failed if c["failure"] == "call failed")
    # A failing bracketed group names no single column and is left out here.
    wrong = Counter(c["column"] for c in cells if not c["ok"] and c["column"])
    pct = f"{100 * passed / attempts:.1f}%" if attempts else "n/a"
    lines = [
        f"# {name}",
        "",
        f"{settings.get('domain', '?')}, {settings.get('mode', '?')}, model {settings.get('model', '?')}: "
        f"{len(by_task)} tasks, {attempts} attempts.",
        "",
        markdown.table(
            [
                "attempts passed",
                "tasks passed every attempt",
                "checks passed",
                "attempts with a tool error",
                "attempts changing a table no check mentions",
            ],
            [
                [
                    f"{passed} of {attempts} ({pct})",
                    f"{every} of {len(by_task)}",
                    f"{checks_passed} of {checks_total}",
                    str(errored),
                    str(len(unasked)),
                ]
            ],
        ),
        "",
        "## What went wrong",
        "",
        "Failed checks by failure: "
        + (", ".join(f"{f} {failures[f]}" for f in FAILURES if failures[f]) or "none")
        + ".",
    ]
    if unwritten:
        lines.append(
            "Tables not written (no call wrote the table): " + _listed(unwritten) + "."
        )
    if broke:
        lines.append("Tables whose call returned an error: " + _listed(broke) + ".")
    if wrong:
        lines.append("Cells wrong, by column: " + _listed(wrong) + ".")
    lines += [
        "",
        f"## Worst tasks ({WORST_TASKS} shown; every task is in tasks.csv)",
        "",
    ]
    # A task's dominant failure: the most common label over its failed
    # checks, a failure by table or a wrong cell by column.
    failures_by_task: dict[str, Counter] = {}
    for c in failed:
        if c["failure"] and c["failure"] != "wrong values":
            failures_by_task.setdefault(c["task_id"], Counter())[
                f"{c['failure']}: {c['table']}"
            ] += 1
    for cell in cells:
        if not cell["ok"] and cell["column"]:
            failures_by_task.setdefault(cell["task_id"], Counter())[
                f"wrong {cell['column']}"
            ] += 1
    ranked = sorted(
        by_task,
        key=lambda t: (sum(r["success"] for r in by_task[t]) / len(by_task[t]), t),
    )
    worst = []
    for task in ranked[:WORST_TASKS]:
        wins = sum(r["success"] for r in by_task[task])
        if wins == len(by_task[task]):
            break
        top = failures_by_task.get(task, Counter()).most_common(1)
        worst.append(
            [task, f"{wins} of {len(by_task[task])}", top[0][0] if top else ""]
        )
    lines += [
        markdown.table(["task", "passed", "dominant failure"], worst),
        "",
        "## Where things are",
        "",
        "`tasks.csv` one row per attempt; `checks.csv` one row per attempt and check with its kind and failure; "
        "`cells.csv` one row per failed check, touched row and condition, wanted against found; "
        "`tables.csv` one row per attempt and table the checks look at or the attempt changed; "
        "`failures.csv` one row per table, column and kind of failure; "
        "`tasks/<task_id>/attempt-<n>.md` the attempt as a page, beside its full record (`.json`) and its "
        "changes against the seed (`.changes.json`).",
        "",
    ]
    return "\n".join(lines)


def _listed(counts: Counter) -> str:
    """Counts as ``key n`` pairs, most common first.

    Args:
        counts: What to list.

    Returns:
        The pairs joined with commas.
    """
    return ", ".join(f"{key} {n}" for key, n in counts.most_common())
