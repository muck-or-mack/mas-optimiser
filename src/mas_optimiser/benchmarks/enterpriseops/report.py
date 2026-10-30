"""Write what a run produced in the form its readers need: a page per attempt, tables per evaluation, a summary.

A run's record is JSON for code. The files here are for a person or a
proposing model reading the results. Beside each record, ``attempt-<n>.md``:
the request, what the agent did turn by turn, what changed against the seed,
and every check with, under a failure, each condition marked ok or WRONG
against the row the agent wrote and the turn that wrote it. Beside
``tasks.csv`` and ``checks.csv``, ``cells.csv`` (one row per failed
check, touched row and condition, with the turn that set a wrong cell) and
``changes.csv`` (one row per attempt and changed table). And ``summary.md``,
a page that says how the run went and what went wrong, by table and column.

The trace from a failed check back to the agent's turns is mechanical. A
call is related to a check when its arguments or its result name the
touched row's key (``hr_case_id: 61``), or, when no row was touched, when
it is a writing call that names the table's key column or carries the
table's name. A wrong cell was set by the last related call whose arguments
name that column; a column no call names was left to the server. The
related calls give each failed check a ``step``: ``skipped`` (no related
call), ``failed`` (a related call returned an error), ``wrong`` (the row is
there with wrong cells) or ``attempted`` (the related calls succeeded and
the check still fails, as when the row went elsewhere).
"""

import json
import re
from collections import Counter

from mas_optimiser.benchmarks.enterpriseops import diagnosis, states

CELL_COLUMNS = (
    "task_id",
    "attempt",
    "check",
    "table",
    "row",
    "condition",
    "ok",
    "actual",
    "turn",
    "tool",
)
CHANGE_COLUMNS = (
    "task_id",
    "attempt",
    "table",
    "inserted",
    "updated",
    "deleted",
    "mentioned",
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
STEPS = ("skipped", "failed", "wrong", "attempted")


def result_text(result: dict) -> str:
    """A tool result's payload as text; MCP servers wrap it as content blocks."""
    content = result.get("result", {})
    if isinstance(content, dict) and content.get("content"):
        return str(content["content"][0].get("text", ""))
    return content if isinstance(content, str) else json.dumps(content)


def calls(run: dict) -> list[dict]:
    """The agent's tool calls in order, each with its turn, arguments and result."""
    out, pending, turn = [], [], 0
    for step in run.get("conversation_flow", []):
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
            call["success"] = bool(result.get("success"))
            call["result"] = result_text(result)
    return out


def names(call: dict, column: str, value: object) -> bool:
    """Whether a call's arguments or result carry ``column`` with ``value``."""
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


def writers_of(agent_calls: list[dict], table: str, key: list[str]) -> list[dict]:
    """The calls that write ``table``: writing calls named after it, or, failing that, whose result echoes its key column.

    The name is the primary rule because arguments mislead: a task is created
    with its case's id, which does not make the call a write to the case.
    """
    writing = [c for c in agent_calls if c["tool"].split("_")[0] in WRITING]
    named = [
        c
        for c in writing
        if set(c["tool"].split("_")[1:]) - FILLER == set(table.split("_"))
    ]
    return named or [
        c
        for c in writing
        if c.get("success") and any(f'"{k}"' in c.get("result", "") for k in key)
    ]


def column_of(condition: str) -> str:
    """The bare column a condition tests, without alias or function."""
    match = diagnosis.COLUMN.match(condition)
    if not match:
        return ""
    name = re.sub(r"^\w+\((.*)\)$", r"\1", match[1])
    return name.split(".")[-1]


def trace(result: dict, agent_calls: list[dict], keys: dict) -> dict:
    """How a failed check's row was handled: the calls that wrote it, who set each wrong cell, the step."""
    found = result.get("diagnosis") or {}
    table = found.get("table", "")
    if not table:
        return {"step": "", "related": [], "set_by": {}}
    writers = writers_of(agent_calls, table, keys.get(table, []))
    # Among the calls that write the table, those that name the touched
    # row's key; all of them when none does (a result that echoes no id).
    rows = [row["row"] for row in found.get("rows", []) if "row" in row]
    pairs = {(k, v) for row in rows for k, v in row.items()}
    linked = [c for c in writers if any(names(c, k, v) for k, v in pairs)] or writers
    set_by = {}
    for row in found.get("rows", []):
        column = column_of(row["condition"])
        if row.get("ok") or not column:
            continue
        for call in linked:
            if column in call["args"]:
                set_by[column] = {
                    "turn": call["turn"],
                    "tool": call["tool"],
                    "value": call["args"][column],
                }
    if found.get("kind") in ("cells", "value"):
        step = "wrong"
    elif not writers:
        step = "skipped"
    elif any(call.get("success") is False for call in writers):
        step = "failed"
    else:
        step = "attempted"
    return {
        "step": step,
        "related": [
            {"turn": c["turn"], "tool": c["tool"], "success": c.get("success")}
            for c in linked
        ],
        "set_by": set_by,
    }


def clip(text: str, max_chars: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= max_chars else text[:max_chars] + " ..."


def check_lines(result: dict, max_chars: int) -> list[str]:
    """Under a failed check: each touched row and condition, and the turns behind them."""
    found = result.get("diagnosis") or {}
    kind = found.get("kind")
    followed = result.get("trace") or {}
    linked = followed.get("related", [])
    if kind in ("cells", "value"):
        lines, shown = [], None
        for row in found["rows"]:
            key = row.get("row")
            if key is not None and key != shown:
                writer = next((c for c in linked if c.get("success")), None)
                where = (
                    f" (written at turn {writer['turn']} by {writer['tool']})"
                    if writer
                    else ""
                )
                lines.append(
                    "row " + ", ".join(f"{k} {v}" for k, v in key.items()) + where
                )
                shown = key
            if row["ok"]:
                lines.append(f"  ok     {row['condition']}")
                continue
            actual = ", ".join(str(v) for v in row.get("actual", [])) or "no value"
            setter = followed.get("set_by", {}).get(column_of(row["condition"]))
            by = (
                f"set at turn {setter['turn']} by {setter['tool']}"
                f"({column_of(row['condition'])}={setter['value']!r})"
                if setter
                else "not in any call's arguments"
            )
            lines.append(f"  WRONG  {row['condition']}   actual: {actual}   {by}")
        return lines
    if kind == "no_rows":
        line = f"no row inserted or updated in {found['table']}"
        if not linked:
            return [f"{line}; no call wrote {found['table']}"]
        return [line] + [
            f"  turn {c['turn']} {c['tool']} "
            + ("failed" if c.get("success") is False else "ok")
            for c in linked
        ]
    if kind == "too_many_rows":
        return [clip(diagnosis.describe(found), max_chars)]
    return []


def attempt_text(
    task_id: str, attempt: int, run: dict, changes: dict | None, max_chars: int = 300
) -> str:
    """One attempt as a page: request, turns, changes, and every check with its table and trace."""
    results = run.get("verification_results", {})
    passed = sum(1 for r in results.values() if r["passed"])
    verdict = "PASS" if run.get("overall_success") else "FAIL"
    lines = [
        f"# {task_id}, attempt {attempt}: {verdict} ({passed} of {len(results)} checks passed)",
        "",
    ]
    flow = run.get("conversation_flow", [])
    if run.get("error") and not flow:
        return "\n".join(lines + [f"Raised before it started: {run['error']}", ""])
    request = next((s["content"] for s in flow if s["type"] == "user_message"), "")
    lines += ["## Request", "", request, "", "## What the agent did", ""]
    turn = 0
    for step in flow:
        if step["type"] == "ai_message":
            turn += 1
            if step.get("content"):
                lines.append(f"turn {turn}: {clip(step['content'], max_chars)}")
            for call in step.get("tool_calls") or []:
                lines.append(
                    f"turn {turn}: {call['name']} {clip(json.dumps(call.get('args')), max_chars)}"
                )
        elif step["type"] == "tool_result":
            result = step.get("result") or {}
            flag = "ok" if result.get("success") else "ERROR"
            lines.append(f"    -> {flag}: {clip(result_text(result), max_chars)}")
    lines += ["", "## Changes against the seed", ""]
    if changes is None:
        lines.append("not saved")
    else:
        queries = [r.get("query", "") for r in results.values()]
        lines += [f"- {line}" for line in states.summary(changes, queries)]
    lines += ["", "## Checks", ""]
    for name, result in results.items():
        if result.get("patch") == "excluded":
            verdict = "pass" if result["upstream_passed"] else "fail"
            lines.append(
                f"### EXCLUDED  {name} (no correct attempt can satisfy it; "
                f"the benchmark's own verdict: {verdict})"
            )
            if result.get("query"):
                lines.append(f"    {' '.join(result['query'].split())}")
            lines.append("")
            continue
        flag = "PASS" if result["passed"] else "FAIL"
        patched = " (repaired)" if result.get("patch") == "repaired" else ""
        lines.append(
            f"### {flag}  {name}{patched} (expected {result.get('expected')!r}, got {result.get('actual')!r})"
        )
        if result.get("query"):
            lines.append(f"    {' '.join(result['query'].split())}")
        if not result["passed"]:
            lines += [f"    {line}" for line in check_lines(result, max_chars)]
        lines.append("")
    return "\n".join(lines)


def cell_rows(task_id: str, attempt: int, results: dict) -> list[dict]:
    """``cells.csv`` rows: one per failed check, touched row and condition."""
    rows = []
    for name, result in results.items():
        found = result.get("diagnosis") or {}
        if result["passed"] or found.get("kind") not in ("cells", "value"):
            continue
        set_by = (result.get("trace") or {}).get("set_by", {})
        for row in found["rows"]:
            setter = set_by.get(column_of(row["condition"]), {})
            rows.append(
                {
                    "task_id": task_id,
                    "attempt": attempt,
                    "check": name,
                    "table": found.get("table", ""),
                    "row": ", ".join(f"{k}={v}" for k, v in row.get("row", {}).items()),
                    "condition": row["condition"],
                    "ok": row["ok"],
                    "actual": ", ".join(str(v) for v in row.get("actual", [])),
                    "turn": setter.get("turn", ""),
                    "tool": setter.get("tool", ""),
                }
            )
    return rows


def change_rows(
    task_id: str, attempt: int, changes: dict | None, queries: list[str]
) -> list[dict]:
    """``changes.csv`` rows: one per changed table, and whether any check mentions it."""
    mentioned = {t.lower() for q in queries for t in states.TABLES.findall(q)}
    return [
        {
            "task_id": task_id,
            "attempt": attempt,
            "table": table,
            "inserted": len(change["inserted"]),
            "updated": len(change["updated"]),
            "deleted": len(change["deleted"]),
            "mentioned": table.lower() in mentioned,
        }
        for table, change in (changes or {}).items()
    ]


def summary(
    name: str,
    settings: dict,
    records: list[dict],
    check_rows: list[dict],
    cells: list[dict],
    change_list: list[dict],
) -> str:
    """``summary.md``: how the evaluation went and what went wrong, by table and column."""
    attempts = len(records)
    passed = sum(r["success"] for r in records)
    by_task: dict[str, list[dict]] = {}
    for r in records:
        by_task.setdefault(r["task_id"], []).append(r)
    every = sum(all(r["success"] for r in rs) for rs in by_task.values())
    checks_passed = sum(r["checks_passed"] for r in records)
    checks_total = sum(r["checks_total"] for r in records)
    errored = sum(r.get("tool_errors", 0) > 0 for r in records)
    unasked = {(c["task_id"], c["attempt"]) for c in change_list if not c["mentioned"]}
    failed = [c for c in check_rows if c["passed"] is False]
    steps = Counter(c["step"] for c in failed if c["step"])
    skipped = Counter(c["table"] for c in failed if c["step"] == "skipped")
    broke = Counter(c["table"] for c in failed if c["step"] == "failed")
    wrong = Counter(column_of(c["condition"]) for c in cells if not c["ok"])
    pct = f"{100 * passed / attempts:.1f}%" if attempts else "n/a"
    lines = [
        f"# {name}",
        "",
        f"{settings.get('domain', '?')}, {settings.get('mode', '?')}, model {settings.get('model', '?')}: "
        f"{len(by_task)} tasks, {attempts} attempts.",
        "",
        "| attempts passed | tasks passed every attempt | checks passed | attempts with a tool error | attempts changing a table no check mentions |",
        "|---|---|---|---|---|",
        f"| {passed} of {attempts} ({pct}) | {every} of {len(by_task)} | {checks_passed} of {checks_total} "
        f"| {errored} | {len(unasked)} |",
        "",
        "## What went wrong",
        "",
        "Failed checks by step: "
        + (", ".join(f"{s} {steps[s]}" for s in STEPS if steps[s]) or "none")
        + ".",
    ]
    if skipped:
        lines.append(
            "Steps skipped (no call wrote the table), by table: "
            + listed(skipped)
            + "."
        )
    if broke:
        lines.append(
            "Steps whose call returned an error, by table: " + listed(broke) + "."
        )
    if wrong:
        lines.append("Cells wrong, by column: " + listed(wrong) + ".")
    lines += [
        "",
        f"## Worst tasks ({WORST_TASKS} shown; every task is in tasks.csv)",
        "",
        "| task | passed | dominant failure |",
        "|---|---|---|",
    ]
    # A task's dominant failure: the most common label over its failed
    # checks, a skipped or failed step by table, or a wrong cell by column.
    failures_by_task: dict[str, Counter] = {}
    for c in failed:
        if c["step"] and c["step"] != "wrong":
            failures_by_task.setdefault(c["task_id"], Counter())[
                f"{c['step']} {c['table']}"
            ] += 1
    for cell in cells:
        if not cell["ok"]:
            failures_by_task.setdefault(cell["task_id"], Counter())[
                f"wrong {column_of(cell['condition'])}"
            ] += 1
    ranked = sorted(
        by_task,
        key=lambda t: (sum(r["success"] for r in by_task[t]) / len(by_task[t]), t),
    )
    for task in ranked[:WORST_TASKS]:
        wins = sum(r["success"] for r in by_task[task])
        if wins == len(by_task[task]):
            break
        top = failures_by_task.get(task, Counter()).most_common(1)
        lines.append(
            f"| {task} | {wins} of {len(by_task[task])} | {top[0][0] if top else ''} |"
        )
    lines += [
        "",
        "## Where things are",
        "",
        "`tasks.csv` one row per attempt; `checks.csv` one row per attempt and check with its kind and step; "
        "`cells.csv` one row per failed check, touched row and condition, with the turn that set a wrong cell; "
        "`changes.csv` one row per attempt and changed table; `tasks/<task_id>/attempt-<n>.md` the attempt as a "
        "page, beside its full record (`.json`) and its changes against the seed (`.changes.json`).",
        "",
    ]
    return "\n".join(lines)


def listed(counts: Counter) -> str:
    return ", ".join(f"{key} {n}" for key, n in counts.most_common())
