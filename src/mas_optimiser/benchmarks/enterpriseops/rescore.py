"""Score an evaluation's attempts against the check files, without running the agent again.

The check files are scored on an attempt's final database (see ``scoring``),
which the domain server deletes when the attempt ends. ``rescore`` gets it
back in one of two ways. If the attempt's changes were saved beside its
record (``attempt-<n>.changes.json``), they are applied to a local copy of
the seed. Otherwise the attempt's recorded tool calls are issued again, in
order, on a database freshly seeded on the domain server. The tools give the
same result for the same calls apart from the timestamps they stamp, so each
result must match the recorded one with timestamps masked; an attempt where
one differs is reported as not reproduced and left unscored. A reproduced
attempt's final state is read back and saved as its changes, and every
requirement's query is also run on the server's copy, so that each local
count is checked against the server's; those counts are saved with the
changes, by query text, so a later rescore still checks every requirement
whose query has not changed since.

A timestamp the server sets itself takes the time of the reissue, not of the
attempt. A check comparing such a column with a fixed time could score a
reissued attempt differently from the original; the requirement counts are
cross-checked on the server, but the original state is gone.

An attempt that raised before finishing has no final state and counts as
failed, as it does in ``tasks.csv``. Each attempt gets
``attempt-<n>.checks.json`` (the scores, the changes no requirement asked for
and the explanation); the evaluation gets ``checks.csv``, one row per attempt
beside the benchmark's two verdicts, and ``requirements.csv``, one row per
requirement per attempt. Usage::

    mas-optimiser rescore runs/baseline-haiku/20261027-052820-itsm [...]

``python -m mas_optimiser.benchmarks.enterpriseops.rescore`` takes the same arguments.
"""

import argparse
import asyncio
import csv
import json
import re
from collections import Counter
from pathlib import Path

from mas_optimiser.benchmarks.enterpriseops import (
    checks,
    runner,
    scoring,
    seeds,
    states,
)

from benchmark.mcp_client import MCPClient

STAMP = re.compile(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:?\d\d)?")
CHECKS_COLUMNS = [
    "task_id",
    "attempt",
    "state",
    "checks_passed",
    "requirements_met",
    "requirements_total",
    "stays_broken",
    "other_rows",
    "other_cells",
    "server_agrees",
    "success",
    "upstream_success",
]
REQUIREMENT_COLUMNS = [
    "task_id",
    "attempt",
    "requirement",
    "kind",
    "table",
    "source",
    "met",
    "count",
    "expected",
    "comparison",
    "audit",
    "server_count",
]
DESCRIPTION = __doc__.split("\n\n")[0]


def masked(value: object) -> str:
    """A tool result as text, keys sorted and timestamps masked, for comparison."""
    return STAMP.sub("<time>", json.dumps(value, sort_keys=True, default=str))


async def reissue(calls: list[dict], gym: dict, database_id: str) -> int | None:
    """Issue an attempt's recorded tool calls again, in order, on a seeded database.

    Args:
        calls: The run's ``tool_results``, each with the tool's name, the
            arguments and the result recorded at the time.
        gym: The task's server config.
        database_id: The freshly seeded database to call the tools on.

    Returns:
        ``None`` if every result matches the recorded one with timestamps
        masked, otherwise the position of the first that does not; the calls
        after it are not issued.
    """
    client = MCPClient(
        base_url=gym["mcp_server_url"],
        auth_config=gym.get("auth_config"),
        mcp_endpoint=gym.get("mcp_endpoint", "/mcp"),
        database_id=database_id,
        context=gym.get("context", {}),
    )
    if not await client.connect():
        raise RuntimeError(f"could not connect to {gym['mcp_server_url']}")
    for position, call in enumerate(calls):
        result = await client.call_tool(call["tool_name"], call["arguments"])
        if masked(result) != masked(call["result"]):
            return position
    return None


def server_final_state(
    calls: list[dict], gym: dict, seed_path: Path, layout: dict, queries: list[str]
) -> dict:
    """Rebuild an attempt's final database on the server and read it back.

    Blocking; ``rescore`` runs it in a worker thread.

    Args:
        calls: The run's recorded tool calls.
        gym: The task's server config.
        seed_path: The task's seed file.
        layout: Each table's columns and primary key, from the seed copy.
        queries: Each requirement's query, to run on the server's copy.

    Returns:
        ``differs`` (the first tool call whose result differs, or ``None``)
        and, when every result matched, the final ``state`` and the
        ``server_counts`` of the queries.
    """
    url = gym["mcp_server_url"]
    with seeds.server_database(url, seed_path) as database_id:
        differs = asyncio.run(reissue(calls, gym, database_id))
        if differs is not None:
            return {"differs": differs}
        state = states.read_state(url, database_id, layout["columns"], layout["keys"])
        counts = [
            next(iter(seeds.run_sql(url, database_id, query)[0].values()))
            for query in queries
        ]
    return {"differs": None, "state": state, "server_counts": counts}


async def rescore_attempt(
    folder: Path,
    row: dict,
    config: dict,
    document: dict,
    domain: str,
) -> dict:
    """Score one attempt and write its ``.checks.json`` (and ``.changes.json`` if rebuilt).

    Args:
        folder: The task's folder in the evaluation.
        row: The attempt's ``tasks.csv`` row.
        config: The task's config, as from ``runner.load_tasks``.
        document: The task's check file.
        domain: The task's domain.

    Returns:
        The attempt's ``.checks.json`` content.
    """
    (gym,) = config["gym_servers_config"]
    seed_path = Path(gym["seed_database_file"])
    seed, layout = states.seed_copy(seed_path, domain)
    stem = f"attempt-{row['attempt']}"
    saved = folder / f"{stem}.changes.json"
    run = json.loads((folder / f"{stem}.json").read_text())["runs"][0]
    # The benchmark's verdict as the runner recounted it, with each check it
    # failed and what the check file did with that check.
    benchmark = {
        "passed": row["success"] == "True",
        "failed": [
            {"name": name}
            | document["original_checks"].get(name, {"disposition": "not listed"})
            for name, verdict in run.get("verification_results", {}).items()
            if not verdict["passed"]
        ],
    }
    out = {"task_id": row["task_id"], "attempt": int(row["attempt"])}
    # The server's count for each requirement query, by query text, taken
    # when the attempt was rebuilt; a requirement whose query has changed
    # since has none.
    server_counts = {}
    if saved.exists():
        out["state"] = "saved"
        rebuilt = json.loads(saved.read_text())
        changes = rebuilt["changes"]
        server_counts = rebuilt.get("server_counts", {})
    elif "tool_results" not in run:
        out |= {
            "state": "raised",
            "checks_passed": False,
            "explanation": "FAIL: the attempt raised before it finished "
            f"({run.get('error', 'no error recorded')}), so it has no final state.",
        }
        changes = None
    else:
        found = await asyncio.to_thread(
            server_final_state,
            run["tool_results"],
            gym,
            seed_path,
            layout,
            [r["sql"] for r in document["requirements"]],
        )
        if found["differs"] is not None:
            out |= {
                "state": "not reproduced",
                "checks_passed": None,
                "explanation": "UNSCORED: reissuing the recorded tool calls gave a "
                f"different result at call {found['differs'] + 1}.",
            }
            changes = None
        else:
            out["state"] = "reissued"
            skip = states.STAMPED.get(domain, frozenset())
            changes = states.diff(seed, found["state"], layout["keys"], skip)
            queries = [r["sql"] for r in document["requirements"]]
            server_counts = dict(zip(queries, found["server_counts"], strict=True))
            saved.write_text(
                json.dumps(
                    {
                        "rebuilt_from": "reissued tool calls",
                        "tool_calls": len(run["tool_results"]),
                        "changes": changes,
                        "server_counts": server_counts,
                    },
                    indent=2,
                    default=str,
                )
            )
    if changes is not None:
        final = states.rebuild(seed, changes, layout["keys"])
        result = await scoring.score(document, final, seed, changes, layout["keys"])
        other = scoring.unasked(document, changes, final, seed, layout["keys"])
        compared = []
        for entry, requirement in zip(
            result["requirements"], document["requirements"], strict=True
        ):
            if requirement["sql"] in server_counts:
                entry["server_count"] = server_counts[requirement["sql"]]
                compared.append(entry["count"] == entry["server_count"])
        out |= {
            "checks_passed": result["passed"],
            "server_agrees": all(compared) if compared else None,
            "result": result,
            "unasked": other,
            "explanation": scoring.explain(result, other, benchmark),
        }
    (folder / f"{stem}.checks.json").write_text(json.dumps(out, indent=2, default=str))
    return out


def checks_row(out: dict, row: dict) -> dict:
    """An attempt's ``checks.csv`` row."""
    result = out.get("result", {})
    unasked = out.get("unasked", {})
    return {
        "task_id": out["task_id"],
        "attempt": out["attempt"],
        "state": out["state"],
        "checks_passed": out["checks_passed"],
        "requirements_met": result.get("met", ""),
        "requirements_total": result.get("total", ""),
        "stays_broken": result.get("stays_broken", ""),
        "other_rows": len(unasked["other_rows"]) if unasked else "",
        "other_cells": len(unasked["other_cells"]) if unasked else "",
        "server_agrees": out.get("server_agrees", ""),
        "success": row["success"],
        "upstream_success": row["upstream_success"],
    }


def requirement_rows(out: dict) -> list[dict]:
    """An attempt's ``requirements.csv`` rows; none if it was not scored."""
    return [
        {
            "task_id": out["task_id"],
            "attempt": out["attempt"],
            "requirement": entry["requirement"],
            "kind": entry["kind"],
            "table": entry["table"],
            "source": "; ".join(checks.source_names(entry["source"]) or ["new"]),
            "met": entry["met"],
            "count": entry["count"],
            "expected": entry["expected"],
            "comparison": entry["comparison"],
            "audit": entry.get("audit", {}).get("kind", ""),
            "server_count": entry.get("server_count", ""),
        }
        for entry in out.get("result", {}).get("requirements", [])
    ]


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


async def rescore(evaluation: Path, concurrency: int) -> Counter:
    """Score every attempt of one evaluation and write its CSVs.

    Args:
        evaluation: The evaluation folder, as the runner wrote it.
        concurrency: Attempts rebuilt on the server at once.

    Returns:
        Counts of the attempts by state and by the two verdicts.
    """
    settings = json.loads((evaluation / "run.json").read_text())
    domain = settings["domain"]
    configs = runner.load_tasks(domain, settings["mode"])
    documents = checks.load_checks()
    with (evaluation / "tasks.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    missing = sorted({r["task_id"] for r in rows} - documents.keys())
    if missing:
        raise SystemExit(f"error: {len(missing)} tasks have no check file")
    runner.check_servers(
        {c["gym_servers_config"][0]["mcp_server_url"] for c in configs.values()}
    )
    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async def one(row: dict) -> dict:
        nonlocal done
        async with semaphore:
            out = await rescore_attempt(
                evaluation / "tasks" / row["task_id"],
                row,
                configs[row["task_id"]],
                documents[row["task_id"]],
                domain,
            )
        done += 1
        verdict = {True: "pass", False: "fail", None: "unscored"}[out["checks_passed"]]
        print(
            f"[{done}/{len(rows)}] {row['task_id']} attempt {row['attempt']}: "
            f"checks {verdict}, benchmark {'pass' if row['success'] == 'True' else 'fail'} "
            f"({out['state']})"
        )
        return out

    outs = await asyncio.gather(*(one(row) for row in rows))
    write_csv(
        evaluation / "checks.csv",
        CHECKS_COLUMNS,
        [checks_row(out, row) for out, row in zip(outs, rows, strict=True)],
    )
    write_csv(
        evaluation / "requirements.csv",
        REQUIREMENT_COLUMNS,
        [r for out in outs for r in requirement_rows(out)],
    )
    counts = Counter(out["state"] for out in outs)
    for out, row in zip(outs, rows, strict=True):
        if out["checks_passed"] is not None:
            counts[
                f"checks {'pass' if out['checks_passed'] else 'fail'}, "
                f"benchmark {'pass' if row['success'] == 'True' else 'fail'}"
            ] += 1
        if out.get("server_agrees") is False:
            counts["server disagrees"] += 1
    return counts


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the rescore options to ``parser``; shared with the ``mas-optimiser`` command."""
    parser.add_argument(
        "evaluation", nargs="+", type=Path, help="evaluation folders under runs/"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="attempts rebuilt on the servers at once (default: 8)",
    )


def run(args: argparse.Namespace) -> None:
    """Rescore each evaluation and print the counts."""
    for evaluation in args.evaluation:
        counts = asyncio.run(rescore(evaluation, args.concurrency))
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
