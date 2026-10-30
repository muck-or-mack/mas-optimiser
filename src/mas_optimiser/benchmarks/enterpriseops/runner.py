"""Run EnterpriseOps-Gym tasks through the benchmark's executor and score them.

The runner loads one domain's tasks, runs each with the benchmark's ReAct
agent against the Docker domain servers, and writes one evaluation folder
under ``runs/<experiment>/``: ``summary.md`` (how the run went and what went
wrong), ``tasks.csv`` with a row per attempt, ``verifiers.csv``,
``cells.csv`` and ``changes.csv`` (see ``report``), ``run.json`` with the
settings and totals, ``run.log``, and per attempt under
``tasks/<task_id>/`` the executor's full record (``attempt-<n>.json``), its
changes against the seed (``attempt-<n>.changes.json``) and a readable page
(``attempt-<n>.md``). Each experiment folder keeps an ``index.csv`` with one
row per evaluation, so the evaluations of one experiment can be compared
without opening them.

An experiment is a named question (``baseline-haiku``); an evaluation is one
model on one domain's tasks, named by its start time and domain plus anything
that makes it differ from the experiment file: a label, an overridden model or
mode, or a task limit. Model, domain and date are therefore columns of the
index, not levels of the folder tree, which keeps every cut of the results one
table away.

Tasks come from the Hugging Face dataset at a pinned revision; in oracle mode
the submodule's corrected copies in ``data/revised`` replace the dataset's
versions of the same tasks. Each task gets a fresh ``BenchmarkExecutor``,
which seeds a database, runs the agent once, runs every verifier against the
final database state and deletes the database. ``score`` flattens the
executor's record into the CSV row.

The benchmark's own ``evaluate.py`` is not used because the runner has to own
the loop: later agents plug in here as orchestrator classes. Two other
differences are deliberate. The model's API key is read from the environment
(``.env``), never written to a config file. A run that raises is recorded and
scored as a failure, where ``evaluate.py`` retries the task up to five times
and ``compute_score.py`` averages whatever is left, counting a still-failing
run as zero and silently dropping a task that raised before any run started.

Scoring counts every verifier of a task and compares count checks numerically,
where the executor loses verifiers that share a name and fails checks whose
expected value is a number written as a string; the executor's own verdict is
kept beside the corrected one (see ``verifiers``).

An experiment file in ``configs/experiments/`` names the model, the domain or
domains, mode, task limit, label and tracing options, and is referred to by
its bare name; any option given on the command line overrides the file. Usage::

    mas-optimiser run smoke
    mas-optimiser run demo --limit 1 --label first-try

``python -m mas_optimiser.benchmarks.enterpriseops.runner`` takes the same arguments.
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import re
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import httpx
import yaml
from datasets import load_dataset
from dotenv import load_dotenv

import mas_optimiser
from mas_optimiser import tracing
from mas_optimiser.benchmarks.enterpriseops import (
    GYM_ROOT,
    REPO_ROOT,
    diagnosis,
    report,
    splits,
    states,
    verifiers,
)

from benchmark.executor import BenchmarkExecutor
from benchmark.models import BenchmarkConfig, LLMConfig
from orchestrators.react import ReactOrchestrator

HF_DATASET = "ServiceNow-AI/EnterpriseOps-Gym"
# Dataset commit of 30/04/2026, pinned so that an upstream edit cannot change
# the tasks between runs.
HF_REVISION = "c8e538eae8a6205294f0a86675fefdc1fac408f6"
# Dataset columns stored as JSON strings, and columns that are not part of a
# task config (as in evaluate.py).
JSON_COLUMNS = {"gym_servers_config", "verifiers"}
ID_COLUMNS = {"task_id", "domain"}

CONFIGS = REPO_ROOT / "configs"
RUNS = REPO_ROOT / "runs"
DESCRIPTION = __doc__.split("\n\n")[0]
DOMAINS = ["itsm", "csm", "hr"]
MODES = ["oracle", "plus_5_tools", "plus_10_tools", "plus_15_tools"]
# Every key an experiment file may set, with its default. ``model`` and
# ``domain`` have none: the file or the command line must supply them.
# ``domain`` may be a list, giving one evaluation per domain.
EXPERIMENT_DEFAULTS = {
    "model": None,
    "domain": None,
    "mode": "oracle",
    "limit": None,
    "split": None,
    "attempts": 1,
    "concurrency": 4,
    "label": None,
    "out": None,
    "trace": True,
    "thinking_summary": False,
}
# Command-line overrides of these keys change what an evaluation measures, so
# they go into its folder name; the rest (tracing, output folder) do not.
NAMED_OVERRIDES = ("model", "mode")
# What a model file's price table must give; see ``cost``.
PRICING_KEYS = {
    "input",
    "output",
    "cache_read",
    "cache_write",
    "long_prompt_tokens",
    "long_prompt_multiplier",
}

# Asks the model for a readable summary of its reasoning on every call, which
# the trace then shows. Thinking happens either way; only its visibility
# changes, at some cost in output tokens.
THINKING_SUMMARY = {"type": "adaptive", "display": "summarized"}


class TaskExecutor(BenchmarkExecutor):
    """The benchmark's executor with tracing attached, every verifier counted and the changes kept.

    The executor constructs its model client inside ``initialize()``, which
    ``execute_benchmark()`` calls itself, so that is the first point at which
    the model object exists and the callback can be set on it. The task's
    verifiers are renamed before the run so that none overwrites another, and
    each verdict is rechecked after they run, with the executor's own verdict
    kept beside it (see ``verifiers``); ``names`` maps the unique names back
    to the task's. Before the executor deletes the run's database, its final
    state is read and compared with the seed, so ``changes`` holds what the
    attempt did (see ``states``) and each failed verdict is diagnosed against
    those rows.

    Args:
        config: The task's ``BenchmarkConfig``.
        trace: Attach the Langfuse callback to the model.
        thinking: A ``thinking`` setting to put on the model, or ``None`` to
            leave the benchmark's default. Changes the agent's behaviour and
            cost, so it stays ``None`` for any run that produces a reported
            number.
        seed: The task's seed copy and its layout, from ``states.seed_copy``.
        domain: The task's domain, for the columns its server stamps.
    """

    def __init__(
        self,
        config: BenchmarkConfig,
        *args,
        trace: bool,
        thinking: dict | None,
        seed: tuple,
        domain: str,
        **kwargs,
    ):
        config.verifiers, self.names = verifiers.unique_names(config.verifiers)
        self.verifier_gyms = {v["name"]: v.get("gym_name") for v in config.verifiers}
        super().__init__(config, *args, **kwargs)
        self.trace = trace
        self.thinking = thinking
        self.seed, self.layout = seed
        self.skip = states.STAMPED.get(domain, frozenset())
        self.changes = None
        self.diagnosis_error = None

    async def initialize(self):
        await super().initialize()
        model = self.llm_client.llm
        if self.trace:
            tracing.attach(model)
        if self.thinking:
            model.thinking = self.thinking

    async def _run_verifiers(self, task_result):
        # The executor deletes the run's database once execute_benchmark
        # returns, so the final state is read and each failed verdict
        # diagnosed here, while it still exists.
        results = await super()._run_verifiers(task_result)
        for result in results.values():
            verifiers.recheck(result)
        try:
            self.changes = await self.read_changes()
            for name, result in results.items():
                if not result["passed"] and result.get("query"):
                    result["diagnosis"] = await diagnosis.diagnose(
                        result, self.changes, self.layout["keys"], self.sql_runner(name)
                    )
        except Exception as exc:  # noqa: BLE001
            # An exception here would reach the executor's own catch, which
            # keeps only the error and drops the whole run; the run's record
            # is worth more than its diagnosis, so the failure is kept on it.
            self.diagnosis_error = repr(exc)
        return results

    async def read_changes(self) -> dict:
        """What the run changed against the seed, read from its database on the server."""
        # One gym per task in this dataset, so one database per run.
        [database] = self.auto_created_databases
        state = await asyncio.to_thread(
            states.read_state,
            database["gym_url"],
            database["database_id"],
            self.layout["columns"],
            self.layout["keys"],
        )
        return states.diff(self.seed, state, self.layout["keys"], self.skip)

    def sql_runner(self, name: str) -> diagnosis.RunSQL:
        """A query function on the database of the gym the named verifier checks.

        Goes through the verifier engine's own SQL runner, so queries reach
        the same server, database and headers as the verifier did.
        """
        gym_name = self.verifier_gyms[name]
        engine = self.verifier_engine

        async def run_sql(query: str):
            raw = await engine._execute_sql_query(query, None, None, gym_name)
            return (
                engine._extract_value_from_sql_result(raw) if raw["success"] else None
            )

        return run_sql


def load_tasks(domain: str, mode: str) -> dict[str, dict]:
    """Load one domain's task configs, keyed by task ID, in dataset order.

    Args:
        domain: Dataset split, e.g. ``"itsm"``.
        mode: Dataset config: ``"oracle"`` (only the tools a task needs) or
            ``"plus_5_tools"`` and so on (that many distractor tools added).

    Returns:
        Task configs ready for ``BenchmarkConfig(**config)``, with each seed
        database path made absolute so runs do not depend on the working
        directory.
    """
    revised = GYM_ROOT / "data" / "revised" / domain
    tasks = {}
    for row in load_dataset(HF_DATASET, mode, split=domain, revision=HF_REVISION):
        # The revised files are oracle tasks. The other modes' rows differ
        # from oracle in their tools and, for a few tasks, their verifiers, so
        # they cannot be patched from these. Revised files for tasks missing
        # from the dataset are not added.
        fixed = revised / f"{row['task_id']}.json"
        if mode == "oracle" and fixed.exists():
            config = json.loads(fixed.read_text())
        else:
            config = {
                key: json.loads(value) if key in JSON_COLUMNS else value
                for key, value in row.items()
                if key not in ID_COLUMNS
            }
        for server in config["gym_servers_config"]:
            server["seed_database_file"] = str(GYM_ROOT / server["seed_database_file"])
        tasks[row["task_id"]] = config
    return tasks


def config_path(kind: str, spec: str) -> Path:
    """Resolve a config given by name or by path.

    Args:
        kind: The folder under ``configs/``: ``experiments`` or ``models``.
        spec: A bare name such as ``smoke``, meaning ``configs/<kind>/smoke.yaml``,
            or a path (anything with a slash or a ``.yaml`` suffix), used as given.

    Returns:
        The file's path.

    Raises:
        SystemExit: For a bare name with no such file, listing the names that exist.
    """
    if "/" in spec or spec.endswith((".yaml", ".yml")):
        return Path(spec)
    path = CONFIGS / kind / f"{spec}.yaml"
    if not path.exists():
        names = ", ".join(sorted(p.stem for p in path.parent.glob("*.yaml")))
        raise SystemExit(f"error: no {kind} config named {spec!r}; available: {names}")
    return path


def load_experiment(spec: str, overrides: dict) -> dict:
    """Read an experiment file and apply the command-line overrides.

    Args:
        spec: The experiment's name or path, as ``config_path`` takes it.
        overrides: One entry per key of ``EXPERIMENT_DEFAULTS``; ``None`` means
            the command line did not set that option.

    Returns:
        The settings: the defaults, then the file, then the overrides, with
        ``domain`` always a list, plus ``experiment`` (the file's stem),
        ``experiment_file`` (its path) and ``overrides`` (the command-line
        values that were applied).

    Raises:
        SystemExit: For an unknown key in the file, a missing model or domain,
            a domain or mode the benchmark does not have, a limit below 1, or
            a label that cannot be part of a folder name.
    """
    path = config_path("experiments", spec)
    from_file = yaml.safe_load(path.read_text()) or {}
    unknown = sorted(set(from_file) - set(EXPERIMENT_DEFAULTS))
    if unknown:
        raise SystemExit(
            f"error: {path} has unknown keys {unknown}; "
            f"allowed: {sorted(EXPERIMENT_DEFAULTS)}"
        )
    applied = {key: value for key, value in overrides.items() if value is not None}
    settings = EXPERIMENT_DEFAULTS | from_file | applied
    for key in ("model", "domain"):
        if settings[key] is None:
            raise SystemExit(
                f"error: {key} is set neither in {path} nor on the command line"
            )
    domains = settings["domain"]
    settings["domain"] = [domains] if isinstance(domains, str) else list(domains)
    for domain in settings["domain"]:
        if domain not in DOMAINS:
            raise SystemExit(f"error: domain must be one of {DOMAINS}, not {domain!r}")
    if settings["mode"] not in MODES:
        raise SystemExit(
            f"error: mode must be one of {MODES}, not {settings['mode']!r}"
        )
    if settings["limit"] is not None and settings["limit"] < 1:
        raise SystemExit("error: limit must be at least 1")
    if settings["split"] is not None:
        name, _, subset = str(settings["split"]).partition(":")
        if not name or subset not in splits.SETS:
            raise SystemExit(
                f"error: split must be <name>:<set> with set one of {splits.SETS}, "
                f"not {settings['split']!r}"
            )
    for key in ("attempts", "concurrency"):
        if not isinstance(settings[key], int) or settings[key] < 1:
            raise SystemExit(f"error: {key} must be a whole number of at least 1")
    label = settings["label"]
    if label is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", str(label)):
        raise SystemExit(
            f"error: label {label!r} must be letters, digits, '-' or '_' (it names a folder)"
        )
    return settings | {
        "experiment": path.stem,
        "experiment_file": str(path),
        "overrides": applied,
    }


def evaluation_name(settings: dict, domain: str, started: datetime) -> str:
    """Name an evaluation folder so that it reads as what was run.

    The start time orders the evaluations of an experiment, then the domain,
    the label if any, every overridden key in ``NAMED_OVERRIDES`` as
    ``key=value`` so that an experiment folder never silently holds a
    different model or mode, the set when one set of a split ran, and
    ``limitN`` when only the first N tasks ran.
    """
    parts = [f"{started:%Y%m%d-%H%M%S}", domain]
    if settings["label"]:
        parts.append(str(settings["label"]))
    for key in NAMED_OVERRIDES:
        if key in settings["overrides"]:
            parts.append(f"{key}={Path(str(settings[key])).stem}")
    if settings["split"] is not None:
        parts.append(str(settings["split"]).partition(":")[2])
    if settings["limit"] is not None:
        parts.append(f"limit{settings['limit']}")
    return "-".join(parts)


def append_index(experiment_dir: Path, row: dict) -> None:
    """Add one evaluation's row to the experiment's ``index.csv``."""
    path = experiment_dir / "index.csv"
    new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        if new:
            writer.writeheader()
        writer.writerow(row)


def check_servers(urls: set[str]) -> None:
    """Stop with the command that starts the domain servers if one is not answering."""
    for url in sorted(urls):
        try:
            httpx.get(f"{url}/health", timeout=3).raise_for_status()
        except httpx.HTTPError as exc:
            raise SystemExit(
                f"error: domain server {url} is not answering ({type(exc).__name__}); "
                "start the servers with: docker compose -f docker/compose.yaml up -d"
            ) from exc


def load_model(path: Path) -> tuple[LLMConfig, dict]:
    """Build the benchmark's model config and price table from a YAML file.

    Args:
        path: YAML file of ``LLMConfig`` fields plus ``api_key_env``, the name
            of the environment variable that holds the API key, and
            ``pricing``, a table with the keys in ``PRICING_KEYS`` in US
            dollars per million tokens.

    Returns:
        The config, with the key filled in from the environment, and the
        price table.

    Raises:
        RuntimeError: If that environment variable is unset or empty.
        SystemExit: If the price table is missing a key, before any money is
            spent on runs whose cost could not be reported.
    """
    fields = yaml.safe_load(path.read_text())
    pricing = fields.pop("pricing", None) or {}
    missing = sorted(PRICING_KEYS - set(pricing))
    if missing:
        raise SystemExit(
            f"error: {path} needs pricing keys {missing} "
            "(US dollars per million tokens, from the provider's price list)"
        )
    key_name = fields.pop("api_key_env")
    key = os.environ.get(key_name)
    if not key:
        raise RuntimeError(f"{key_name} is not set; add it to .env (see .env.example)")
    return LLMConfig(llm_api_key=key, **fields), pricing


def cost(usage: list[dict], pricing: dict) -> float:
    """US dollars for a run's model calls under a model's price table.

    Args:
        usage: One LangChain ``usage_metadata`` per call. Its ``input_tokens``
            include the cached tokens that ``input_token_details`` breaks out.
        pricing: The table from ``load_model``. A call whose prompt is over
            ``long_prompt_tokens`` is charged ``long_prompt_multiplier`` times
            the rates, as the Claude API does.
    """
    total = 0.0
    for call in usage:
        details = call.get("input_token_details") or {}
        cache_read = details.get("cache_read") or 0
        cache_write = details.get("cache_creation") or 0
        long_prompt = call["input_tokens"] > pricing["long_prompt_tokens"]
        total += (pricing["long_prompt_multiplier"] if long_prompt else 1) * (
            (call["input_tokens"] - cache_read - cache_write) * pricing["input"]
            + cache_read * pricing["cache_read"]
            + cache_write * pricing["cache_write"]
            + call["output_tokens"] * pricing["output"]
        )
    return total / 1_000_000


def task_record(
    task_id: str,
    attempt: int,
    run: dict,
    names: verifiers.Names,
    pricing: dict,
) -> dict:
    """Flatten the executor's record of one task run into a ``tasks.csv`` row.

    A run that raised has no verifier results, so it counts as a failed task
    with none of its verifiers passed, rather than being dropped. The
    ``upstream_`` columns give the benchmark's own count for the run.

    Args:
        task_id: The task's dataset ID.
        attempt: Which attempt of the task this run was, from 1.
        run: One entry of the ``runs`` list that
            ``BenchmarkExecutor.execute_benchmark`` returns, or a stand-in
            holding the error if it raised before the run started.
        names: The executor's verifier name maps; one entry per kept verifier.
        pricing: The model's price table, for ``cost``.

    Returns:
        The flat row. Token counts and cost cover the agent's model calls; the
        executor keeps none for a run that raised, so those read zero.
    """
    usage = [
        step["usage_metadata"]
        for step in run.get("conversation_flow", [])
        if step["type"] == "ai_message" and step.get("usage_metadata")
    ]
    details = [u.get("input_token_details") or {} for u in usage]
    results = run.get("verification_results", {})
    upstream_passed, upstream_total, upstream_success = verifiers.upstream_verdict(
        results, names
    )
    return {
        "task_id": task_id,
        "attempt": attempt,
        "success": bool(run.get("overall_success")),
        "verifiers_passed": sum(r["passed"] for r in results.values()),
        "verifiers_total": len(names.original),
        "upstream_success": upstream_success,
        "upstream_verifiers_passed": upstream_passed,
        "upstream_verifiers_total": upstream_total,
        "error": run.get("error", ""),
        "tool_errors": sum(
            not (step.get("result") or {}).get("success")
            for step in run.get("conversation_flow", [])
            if step["type"] == "tool_result"
        ),
        "model_calls": len(usage),
        "input_tokens": sum(u["input_tokens"] for u in usage),
        "output_tokens": sum(u["output_tokens"] for u in usage),
        "cache_read_tokens": sum(d.get("cache_read") or 0 for d in details),
        "cache_write_tokens": sum(d.get("cache_creation") or 0 for d in details),
        "cost_usd": round(cost(usage, pricing), 6),
        "seconds": run.get("execution_time_ms", 0) / 1000,
    }


def outputs(
    task_id: str,
    attempt: int,
    run: dict,
    changes: dict | None,
    keys: dict,
    names: verifiers.Names,
    pricing: dict,
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Trace each failed check, then build one attempt's rows for every table.

    The trace is attached to the check's result before anything is written,
    so the record, the CSVs and the page agree.

    Returns:
        The ``tasks.csv`` row and the ``verifiers.csv``, ``cells.csv`` and
        ``changes.csv`` rows.
    """
    results = run.get("verification_results", {})
    agent_calls = report.calls(run)
    for check in results.values():
        if not check["passed"] and check.get("diagnosis"):
            check["trace"] = report.trace(check, agent_calls, keys)
    queries = [r.get("query", "") for r in results.values()]
    return (
        task_record(task_id, attempt, run, names, pricing),
        verifiers.verifier_records(task_id, attempt, results, names),
        report.cell_rows(task_id, attempt, results),
        report.change_rows(task_id, attempt, changes, queries),
    )


def save_attempt(
    folder: Path, attempt: int, result: dict, changes: dict | None
) -> None:
    """Write an attempt's record, its changes against the seed and its page into the task's folder."""
    folder.mkdir(exist_ok=True)
    (folder / f"attempt-{attempt}.json").write_text(
        json.dumps(result, indent=2, default=str)
    )
    if changes is not None:
        (folder / f"attempt-{attempt}.changes.json").write_text(
            json.dumps({"changes": changes}, indent=1, default=str)
        )
    (folder / f"attempt-{attempt}.md").write_text(
        report.attempt_text(folder.name, attempt, result["runs"][0], changes)
    )


def write_tables(
    out: Path,
    settings: dict,
    records: list[dict],
    verifier_rows: list[dict],
    cell_rows: list[dict],
    change_rows: list[dict],
) -> None:
    """Write an evaluation's ``tasks.csv``, ``verifiers.csv``, ``cells.csv``, ``changes.csv`` and ``summary.md``.

    Args:
        out: The evaluation folder.
        settings: Its settings; ``domain``, ``mode`` and ``model`` (a name, or
            the model config as ``run.json`` keeps it) head the summary.
    """
    model = settings.get("model")
    if isinstance(model, dict):
        model = model.get("llm_model", "?")
    for name, columns, rows in (
        ("tasks.csv", list(records[0]) if records else [], records),
        ("verifiers.csv", verifiers.COLUMNS, verifier_rows),
        ("cells.csv", report.CELL_COLUMNS, cell_rows),
        ("changes.csv", report.CHANGE_COLUMNS, change_rows),
    ):
        with (out / name).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
    (out / "summary.md").write_text(
        report.summary(
            out.name,
            {
                "domain": settings.get("domain"),
                "mode": settings.get("mode"),
                "model": model,
            },
            records,
            verifier_rows,
            cell_rows,
            change_rows,
        )
    )


async def run_task(
    task_id: str,
    config: dict,
    attempt: int,
    model: LLMConfig,
    pricing: dict,
    out: Path,
    *,
    domain: str,
    trace: bool,
    thinking: dict | None,
    tags: list[str],
) -> tuple[dict, list[dict]]:
    """Run one task once with the benchmark's ReAct agent, save the record and score it.

    Args:
        task_id: The task's dataset ID.
        config: Its task config, as from ``load_tasks``.
        attempt: Which attempt this is, from 1; names the saved record.
        model: The agent's model config.
        pricing: The model's price table.
        out: Evaluation folder; the record goes to ``out/tasks/<task_id>``,
            with the run's changes against the seed beside it.
        domain: The task's domain.
        trace: Send the run to Langfuse as one trace, scored with its verdict.
        thinking: Model ``thinking`` setting, or ``None`` for the default.
        tags: Labels put on the trace, such as the domain and model.

    Returns:
        The row from ``task_record``, the rows from ``verifier_records``, and
        the attempt's ``cells.csv`` and ``changes.csv`` rows (see ``report``).
    """
    # One executor call per attempt, so that each run is its own trace and its
    # own unit of concurrency; the executor's own run loop stays at one.
    executor = TaskExecutor(
        BenchmarkConfig(**(config | {"number_of_runs": 1})),
        model,
        orchestrator_class=ReactOrchestrator,
        trace=trace,
        thinking=thinking,
        seed=states.seed_copy(
            Path(config["gym_servers_config"][0]["seed_database_file"]), domain
        ),
        domain=domain,
    )
    # The evaluation's experiment and folder name group the traces as one session.
    span_context = (
        tracing.task_trace(
            task_id,
            session_id=f"{out.parent.name}/{out.name}",
            tags=tags + [f"attempt:{attempt}"],
            metadata={
                "task_id": task_id,
                "attempt": attempt,
                "dataset_revision": HF_REVISION,
            },
        )
        if trace
        else nullcontext()
    )
    with span_context as span:
        try:
            result = await executor.execute_benchmark()
        except Exception as exc:
            # Raised before the run started, e.g. a server that is not up.
            result = {"runs": [{"run_number": 1, "error": repr(exc)}]}
        run = result["runs"][0]
        if executor.diagnosis_error:
            run["diagnosis_error"] = executor.diagnosis_error
        changes = executor.changes
        record, rows, cells, changed = outputs(
            task_id,
            attempt,
            run,
            changes,
            executor.layout["keys"],
            executor.names,
            pricing,
        )
        results = run.get("verification_results", {})
        if span is not None:
            fraction = (
                record["verifiers_passed"] / record["verifiers_total"]
                if record["verifiers_total"]
                else 0.0
            )
            span.score_trace(
                name="task_success", value=float(record["success"]), data_type="BOOLEAN"
            )
            span.score_trace(
                name="verifier_fraction", value=fraction, data_type="NUMERIC"
            )
            span.update(
                output={
                    k: record[k]
                    for k in (
                        "success",
                        "verifiers_passed",
                        "verifiers_total",
                        "upstream_success",
                        "error",
                    )
                }
                | {
                    "verifiers": {
                        f"{row['position']} {row['name']}": {
                            "passed": row["passed"],
                            "expected": row["expected"],
                            "actual": row["actual"],
                            "why": row["diagnosis"],
                        }
                        for row in rows
                    },
                    "changes": (
                        states.summary(
                            changes, [r.get("query", "") for r in results.values()]
                        )
                        if changes is not None
                        else None
                    ),
                }
            )
    save_attempt(out / "tasks" / task_id, attempt, result, changes)
    return record, rows, cells, changed


async def run_tasks(
    tasks: dict[str, dict],
    model: LLMConfig,
    pricing: dict,
    out: Path,
    *,
    domain: str,
    attempts: int,
    concurrency: int,
    trace: bool,
    thinking: dict | None,
    tags: list[str],
) -> tuple[list[dict], list[dict]]:
    """Run every task ``attempts`` times, at most ``concurrency`` runs at once.

    Each run seeds its own database on the domain server, so runs share
    nothing and can overlap; the limit keeps the server and the model API's
    rate limits in range.

    Args:
        tasks: Task configs keyed by task ID, as from ``load_tasks``.
        model: The agent's model config.
        pricing: The model's price table.
        out: Evaluation folder.
        domain: The tasks' domain.
        attempts: Runs per task.
        concurrency: Runs in flight at once.
        trace: Send each run to Langfuse as one trace.
        thinking: Model ``thinking`` setting, or ``None`` for the default.
        tags: Labels put on every trace, such as the domain and model.

    Returns:
        One ``tasks.csv`` row per run, in task order and then attempt order,
        and the ``verifiers.csv``, ``cells.csv`` and ``changes.csv`` rows of
        every run in the same order.
    """
    semaphore = asyncio.Semaphore(concurrency)
    total = len(tasks) * attempts
    done = 0

    async def run_one(task_id: str, config: dict, attempt: int) -> tuple:
        nonlocal done
        async with semaphore:
            record, rows, cells, changed = await run_task(
                task_id,
                config,
                attempt,
                model,
                pricing,
                out,
                domain=domain,
                trace=trace,
                thinking=thinking,
                tags=tags,
            )
        done += 1
        outcome = (
            "error" if record["error"] else "pass" if record["success"] else "fail"
        )
        print(
            f"[{done}/{total}] {task_id} attempt {attempt} {outcome}: verifiers "
            f"{record['verifiers_passed']}/{record['verifiers_total']}, "
            f"{record['model_calls']} calls, {record['input_tokens']:,} in / "
            f"{record['output_tokens']:,} out tokens, ${record['cost_usd']:.4f}, "
            f"{record['seconds']:.0f} s"
        )
        return record, rows, cells, changed

    results = await asyncio.gather(
        *(
            run_one(task_id, config, attempt)
            for task_id, config in tasks.items()
            for attempt in range(1, attempts + 1)
        )
    )
    return (
        [r[0] for r in results],
        [row for r in results for row in r[1]],
        [cell for r in results for cell in r[2]],
        [change for r in results for change in r[3]],
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the runner's arguments to ``parser``; shared with the ``mas-optimiser`` command.

    Every option defaults to ``None``, so only options given on the command
    line override the experiment file.
    """
    parser.add_argument(
        "experiment",
        help="experiment name in configs/experiments/ (or a path to a YAML file)",
    )
    parser.add_argument("--model", help="model name in configs/models/, or a YAML path")
    parser.add_argument(
        "--domain",
        choices=DOMAINS,
        help="benchmark domain: IT service management, customer service or HR",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        help="oracle gives the agent only the task's own tools; plus_N_tools adds N distractors",
    )
    parser.add_argument("--limit", type=int, help="run only the first N tasks")
    parser.add_argument(
        "--split",
        help="run only one set of a split in configs/splits/, as <name>:<set>, "
        "e.g. haiku-20-40-40:training",
    )
    parser.add_argument("--attempts", type=int, help="runs per task")
    parser.add_argument("--concurrency", type=int, help="runs in flight at once")
    parser.add_argument(
        "--label", help="a word added to the evaluation's folder name, e.g. after-fix"
    )
    parser.add_argument(
        "--out", type=Path, help="evaluation folder (default: runs/<experiment>/<name>)"
    )
    parser.add_argument(
        "--trace",
        action=argparse.BooleanOptionalAction,
        help="send traces to Langfuse (needs the keys in .env)",
    )
    parser.add_argument(
        "--thinking-summary",
        action=argparse.BooleanOptionalAction,
        help="ask the model for reasoning summaries (demonstrations only)",
    )


def run(args: argparse.Namespace) -> None:
    """Run the experiment named by the parsed arguments, one evaluation per domain."""
    overrides = {key: getattr(args, key) for key in EXPERIMENT_DEFAULTS}
    experiment = load_experiment(args.experiment, overrides)

    load_dotenv(REPO_ROOT / ".env")
    model, pricing = load_model(config_path("models", experiment["model"]))
    trace = tracing.configured() and experiment["trace"]
    thinking = THINKING_SUMMARY if experiment["thinking_summary"] else None
    if trace:
        # The Anthropic SDK also writes a metadata-only span per call into any
        # registered tracer; the LangChain callback already records each call
        # with its content, so those duplicates are switched off.
        os.environ.setdefault("ANTHROPIC_OPEN_TELEMETRY", "false")
    # Taken once, before any task runs: an edit or commit made while a long
    # run goes on must not be stamped on results the edited code never made.
    version = mas_optimiser.code_version()
    for domain in experiment["domain"]:
        evaluate(
            experiment,
            domain,
            model,
            pricing,
            trace=trace,
            thinking=thinking,
            version=version,
        )
    if trace:
        tracing.shutdown()


def evaluate(
    experiment: dict,
    domain: str,
    model: LLMConfig,
    pricing: dict,
    *,
    trace: bool,
    thinking: dict | None,
    version: str,
) -> None:
    """Run one domain's tasks as one evaluation and write its folder and index row.

    Args:
        experiment: Settings from ``load_experiment``.
        domain: The domain to run; one of ``experiment["domain"]``.
        model: The agent's model config.
        pricing: The model's price table.
        trace: Send each task to Langfuse.
        thinking: Model ``thinking`` setting, or ``None`` for the default.
        version: The code's version, from ``mas_optimiser.code_version`` when
            the run began, recorded in ``run.json`` and the index.
    """
    tasks = load_tasks(domain, experiment["mode"])
    if experiment["split"]:
        name, _, subset = experiment["split"].partition(":")
        members = splits.load_split(name)["tasks"]
        tasks = {
            task_id: config
            for task_id, config in tasks.items()
            if members.get(task_id, {}).get("set") == subset
        }
    tasks = dict(list(tasks.items())[: experiment["limit"]])
    check_servers(
        {
            s["mcp_server_url"]
            for config in tasks.values()
            for s in config["gym_servers_config"]
        }
    )
    # With the UTC offset, so that start times order correctly across a
    # change of the clocks.
    started = datetime.now().astimezone()
    out = (
        Path(experiment["out"])
        if experiment["out"]
        else RUNS
        / experiment["experiment"]
        / evaluation_name(experiment, domain, started)
    )
    (out / "tasks").mkdir(parents=True)
    # The benchmark logs every request and tool call at INFO; keep that in the
    # evaluation folder rather than on the terminal. One handler per
    # evaluation, since one process may run several domains.
    handler = logging.FileHandler(out / "run.log")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    tags = [domain, experiment["mode"], "arm:default", model.llm_model]
    try:
        records, verifier_rows, cell_rows, change_rows = asyncio.run(
            run_tasks(
                tasks,
                model,
                pricing,
                out,
                domain=domain,
                attempts=experiment["attempts"],
                concurrency=experiment["concurrency"],
                trace=trace,
                thinking=thinking,
                tags=tags,
            )
        )
    finally:
        root.removeHandler(handler)
        handler.close()

    write_tables(
        out,
        {"domain": domain, "mode": experiment["mode"], "model": model.llm_model},
        records,
        verifier_rows,
        cell_rows,
        change_rows,
    )
    by_task: dict[str, list[bool]] = {}
    for r in records:
        by_task.setdefault(r["task_id"], []).append(r["success"])
    totals = {
        "tasks": len(tasks),
        "attempts": experiment["attempts"],
        "runs": len(records),
        "successes": sum(r["success"] for r in records),
        "tasks_passed_every_attempt": sum(all(v) for v in by_task.values()),
        "errors": sum(bool(r["error"]) for r in records),
        "verifiers_passed": sum(r["verifiers_passed"] for r in records),
        "verifiers_total": sum(r["verifiers_total"] for r in records),
        "upstream_successes": sum(r["upstream_success"] for r in records),
        "upstream_verifiers_passed": sum(
            r["upstream_verifiers_passed"] for r in records
        ),
        "upstream_verifiers_total": sum(r["upstream_verifiers_total"] for r in records),
        "input_tokens": sum(r["input_tokens"] for r in records),
        "output_tokens": sum(r["output_tokens"] for r in records),
        "cache_read_tokens": sum(r["cache_read_tokens"] for r in records),
        "cache_write_tokens": sum(r["cache_write_tokens"] for r in records),
        "cost_usd": round(sum(r["cost_usd"] for r in records), 4),
        "seconds": round(sum(r["seconds"] for r in records), 1),
    }
    settings = {
        "experiment": experiment["experiment"],
        "experiment_file": experiment["experiment_file"],
        "evaluation": out.name,
        "label": experiment["label"],
        "overrides": experiment["overrides"],
        "harness": "default",
        "model": {k: v for k, v in vars(model).items() if k != "llm_api_key"},
        "pricing": pricing,
        "domain": domain,
        "mode": experiment["mode"],
        "limit": experiment["limit"],
        "attempts": experiment["attempts"],
        "concurrency": experiment["concurrency"],
        "tasks": list(tasks),
        "dataset": HF_DATASET,
        "dataset_revision": HF_REVISION,
        "thinking": thinking,
        "traced": trace,
        "mas_optimiser_version": version,
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (out / "run.json").write_text(json.dumps(settings | {"totals": totals}, indent=2))
    append_index(
        out.parent,
        {
            "evaluation": out.name,
            "started": settings["started"],
            "domain": domain,
            "mode": experiment["mode"],
            "model": model.llm_model,
            "harness": "default",
            "label": experiment["label"] or "",
            "overrides": " ".join(
                f"{k}={v}" for k, v in experiment["overrides"].items()
            ),
            "limit": experiment["limit"] or "",
            "code_version": version,
        }
        | totals,
    )

    print(
        f"\n{totals['successes']}/{totals['runs']} runs passed, "
        f"{totals['tasks_passed_every_attempt']}/{totals['tasks']} tasks passed every "
        f"attempt ({totals['errors']} errors, counted as failures); verifiers "
        f"{totals['verifiers_passed']}/{totals['verifiers_total']}; "
        f"{totals['input_tokens']:,} input and {totals['output_tokens']:,} output "
        f"tokens. Results in {out}"
        + (
            f"; traces in Langfuse session {out.parent.name}/{out.name}"
            if trace
            else ""
        )
    )


def main() -> None:
    """Command-line entry point; see the module docstring for usage."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    add_arguments(parser)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
