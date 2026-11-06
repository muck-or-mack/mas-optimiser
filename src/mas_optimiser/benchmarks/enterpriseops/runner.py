"""Run EnterpriseOps-Gym tasks through the benchmark's executor and score them.

The runner loads one domain's tasks, runs each with the benchmark's ReAct
agent against the Docker domain servers, and writes one evaluation folder
under ``evaluations/<experiment>/``: ``summary.md``, ``tasks.csv`` (a row per
attempt), ``checks.csv``, ``cells.csv`` and ``changes.csv`` (see ``report``),
``evaluation.json`` with the settings and totals, ``evaluation.log``, and per attempt under
``tasks/<task_id>/`` the executor's full record (``attempt-<n>.json``), its
changes against the seed (``attempt-<n>.changes.json``) and a readable page
(``attempt-<n>.md``). Each experiment folder keeps an ``index.csv`` with one
row per evaluation.

An experiment is a named question (``baseline-haiku``), set out in
``configs/experiments/``; an evaluation is one model on one domain's tasks,
named by its start time and domain plus whatever makes it differ from the
experiment file. Tasks come from the Hugging Face dataset at a pinned
revision; in oracle mode the benchmark's corrected copies in ``data/revised``
replace the dataset's versions. Each attempt gets a fresh
``BenchmarkExecutor``, which seeds a database, runs the agent once, runs every
check against the final state and deletes the database.

The benchmark's own ``evaluate.py`` is not used because the runner has to own
the loop: later agents plug in here as orchestrator classes. The model's API
key is read from the environment (``.env``), never written to a config file,
and a run that raises is recorded and scored as a failure rather than retried
or dropped. Scoring counts every distinct check of a task and compares count
checks numerically, with the executor's own verdict kept beside the corrected
one (see ``checks``).
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
    checks,
    diagnosis,
    report,
    splits,
    states,
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
EVALUATIONS = REPO_ROOT / "evaluations"
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
    "task": None,
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
# The totals an evaluation sums over its ``tasks.csv`` rows, in index order;
# ``upstream_successes`` counts the rows' ``upstream_success``, the rest are
# columns of the same name.
SUMMED = (
    "checks_passed",
    "checks_total",
    "upstream_successes",
    "upstream_checks_passed",
    "upstream_checks_total",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
    "seconds",
)
# Asks the model for a readable summary of its reasoning on every call, which
# the trace then shows. Thinking happens either way; only its visibility
# changes, at some cost in output tokens.
THINKING_SUMMARY = {"type": "adaptive", "display": "summarized"}


class TaskExecutor(BenchmarkExecutor):
    """The benchmark's executor with tracing attached, every check counted and the changes kept.

    The task's checks are renamed before the run so that none overwrites
    another, then taken through the patch list (see ``checks``). After the
    checks run, and before the executor deletes the run's database, its final
    state is read and compared with the seed, and each failed verdict is
    diagnosed against the rows the attempt touched (see ``states`` and
    ``diagnosis``).

    Args:
        config: The task's config; its ``verifiers`` are replaced by the
            prepared checks.
        *args: Passed on to ``BenchmarkExecutor`` (the model config).
        trace: Attach the Langfuse callback to the model.
        thinking: A ``thinking`` setting for the model, or ``None`` for the
            benchmark's default. It changes the agent's behaviour and cost,
            so it stays ``None`` for any run that produces a reported number.
        seed: The task's seed copy and its layout, from ``states.seed_copy``.
        domain: The task's domain, for the columns its server stamps.
        task_id: The task's dataset ID, for its entry in the patch list.
        **kwargs: Passed on to ``BenchmarkExecutor`` (the orchestrator class).
    """

    def __init__(
        self,
        config: BenchmarkConfig,
        *args,
        trace: bool,
        thinking: dict | None,
        seed: tuple,
        domain: str,
        task_id: str,
        **kwargs,
    ):
        config.verifiers, self.names, self.check_gyms = checks.prepare(
            task_id, config.verifiers
        )
        super().__init__(config, *args, **kwargs)
        self.trace = trace
        self.thinking = thinking
        self.seed, self.layout = seed
        self.skip = states.STAMPED.get(domain, frozenset())
        self.changes = None
        self.diagnosis_error = None

    async def initialize(self):
        """Build the clients as the benchmark does, then attach tracing and the thinking setting to the model.

        The executor builds its model client here, so this is the first point
        at which the model object exists.
        """
        await super().initialize()
        model = self.llm_client.llm
        if self.trace:
            tracing.attach(model)
        if self.thinking:
            model.thinking = self.thinking

    async def _run_verifiers(self, task_result: dict) -> dict[str, dict]:
        """Run the checks as the benchmark does, then correct, record and diagnose the verdicts.

        The final state is read and each failure diagnosed here because the
        executor deletes the run's database as soon as this returns. A fault in
        that work is kept on the executor as ``diagnosis_error`` rather than
        raised: the executor's own catch would keep only the error and drop the
        whole run, and the record is worth more than its diagnosis.

        Args:
            task_result: The orchestrator's result, as the benchmark passes it.

        Returns:
            The verdicts keyed by unique check name, each with ``upstream_passed``
            and, when failed, its ``diagnosis``.
        """
        results = await super()._run_verifiers(task_result)
        for result in results.values():
            checks.recheck(result)
        try:
            await checks.original_verdicts(results, self.names, self._sql_runner)
            self.changes = await self.read_changes()
            for name, result in results.items():
                if result["passed"] is False and result.get("query"):
                    result["diagnosis"] = await diagnosis.diagnose(
                        result,
                        self.changes,
                        self.layout["keys"],
                        self._sql_runner(name),
                    )
        except Exception as exc:  # noqa: BLE001
            self.diagnosis_error = repr(exc)
        return results

    async def read_changes(self) -> dict:
        """What the run changed against the seed, read from its database on the server.

        Returns:
            The changes, as ``states.diff`` gives them.
        """
        # One gym per task in this dataset, so one database per attempt.
        [database] = self.auto_created_databases
        state = await asyncio.to_thread(
            states.read_state,
            database["gym_url"],
            database["database_id"],
            self.layout["columns"],
            self.layout["keys"],
        )
        return states.diff(self.seed, state, self.layout["keys"], self.skip)

    def _sql_runner(self, name: str) -> diagnosis.RunSQL:
        """A query function on the database of the gym the named check queries, through the engine's own runner.

        Args:
            name: A check's unique name.

        Returns:
            An async function of a query; a query the server rejects gives ``None``.
        """
        gym_name = self.check_gyms[name]
        engine = self.verifier_engine

        async def run_sql(query: str):
            raw = await engine._execute_sql_query(query, None, None, gym_name)
            return (
                engine._extract_value_from_sql_result(raw) if raw["success"] else None
            )

        return run_sql


def load_tasks(domain: str, mode: str) -> dict[str, dict]:
    """One domain's task configs, keyed by task ID, in dataset order, with absolute seed paths.

    Args:
        domain: The domain.
        mode: The dataset config: ``oracle`` (only the tools a task needs) or
            ``plus_5_tools`` and so on (that many distractor tools added).

    Returns:
        Each task ID to its config.
    """
    revised = GYM_ROOT / "data" / "revised" / domain
    tasks = {}
    for row in load_dataset(HF_DATASET, mode, split=domain, revision=HF_REVISION):
        # The revised files are oracle tasks. The other modes' rows differ
        # from oracle in their tools and, for a few tasks, their verifiers, so
        # they cannot be patched from these.
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
    """A config given by bare name (``configs/<kind>/<spec>.yaml``) or by path.

    Args:
        kind: The folder under ``configs/``: ``experiments`` or ``models``.
        spec: A bare name, or a path to a YAML file.

    Returns:
        The file's path.

    Raises:
        SystemExit: If a bare name has no file, naming the ones that exist.
    """
    if "/" in spec or spec.endswith((".yaml", ".yml")):
        return Path(spec)
    path = CONFIGS / kind / f"{spec}.yaml"
    if not path.exists():
        names = ", ".join(sorted(p.stem for p in path.parent.glob("*.yaml")))
        raise SystemExit(f"error: no {kind} config named {spec!r}; available: {names}")
    return path


def load_experiment(spec: str, overrides: dict) -> dict:
    """The settings of an experiment: the defaults, then its file, then the command-line overrides.

    Args:
        spec: The experiment's name, or the path of its file.
        overrides: One entry per key of ``EXPERIMENT_DEFAULTS``, ``None`` where
            the command line did not set the option.

    Returns:
        The settings, plus ``experiment`` (the file's stem), ``experiment_file``
        and ``overrides`` (the values applied); ``domain`` is always a list.

    Raises:
        SystemExit: For an unknown key in the file, a model or domain set
            nowhere, or a bad value, before any money is spent.
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
    if settings["task"] is not None:
        chosen = settings["task"]
        settings["task"] = [chosen] if isinstance(chosen, str) else list(chosen)
        if not all(isinstance(t, str) and t for t in settings["task"]):
            raise SystemExit("error: task must be a task ID or a list of them")
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
    """An evaluation folder's name: start time, domain, label, any overridden model or mode, the split set, the task limit.

    Args:
        settings: The experiment's settings, from ``load_experiment``.
        domain: The evaluation's domain.
        started: The evaluation's start time.

    Returns:
        The name.
    """
    parts = [f"{started:%Y%m%d-%H%M%S}", domain]
    if settings["label"]:
        parts.append(str(settings["label"]))
    for key in NAMED_OVERRIDES:
        if key in settings["overrides"]:
            parts.append(f"{key}={Path(str(settings[key])).stem}")
    if settings["split"] is not None:
        parts.append(str(settings["split"]).partition(":")[2])
    if settings["task"]:
        parts.append(f"tasks{len(settings['task'])}")
    if settings["limit"] is not None:
        parts.append(f"limit{settings['limit']}")
    return "-".join(parts)


def totals(records: list[dict], tasks: int, attempts: int) -> dict:
    """An evaluation's totals over its ``tasks.csv`` rows, as ``evaluation.json`` and the index keep them.

    Args:
        records: The ``tasks.csv`` rows.
        tasks: The number of tasks the evaluation set out to run.
        attempts: The attempts per task it set out to make.

    Returns:
        The counts (attempts and successes, tasks passed on every attempt,
        errors) and the sums of ``SUMMED`` over the rows.
    """
    by_task: dict[str, list[bool]] = {}
    for r in records:
        by_task.setdefault(r["task_id"], []).append(r["success"])
    counted = {
        "tasks": tasks,
        "attempts": attempts,
        "total_attempts": len(records),
        "successes": sum(r["success"] for r in records),
        "tasks_passed_every_attempt": sum(all(v) for v in by_task.values()),
        "errors": sum(bool(r["error"]) for r in records),
    }
    for key in SUMMED:
        column = "upstream_success" if key == "upstream_successes" else key
        counted[key] = round(sum(r[column] for r in records), 4)
    return counted


def index_row(settings: dict, counted: dict) -> dict:
    """One evaluation's row of the experiment's ``index.csv``: the settings that tell evaluations apart, then the totals.

    Args:
        settings: The evaluation's settings, as ``evaluation.json`` keeps them.
        counted: The totals, from ``totals``.

    Returns:
        The row.
    """
    model = settings.get("model")
    return {
        "evaluation": settings["evaluation"],
        "started": settings["started"],
        "domain": settings["domain"],
        "mode": settings["mode"],
        "model": model.get("llm_model", "?") if isinstance(model, dict) else model,
        "harness": settings.get("harness", "default"),
        "label": settings.get("label") or "",
        "overrides": " ".join(
            f"{k}={v}" for k, v in (settings.get("overrides") or {}).items()
        ),
        "limit": settings.get("limit") or "",
        "code_version": settings.get("mas_optimiser_version", ""),
    } | counted


def write_index_row(experiment_dir: Path, row: dict) -> None:
    """Add one evaluation's row to the experiment's ``index.csv``, or replace the row that names the same evaluation, in the columns the file already has.

    Args:
        experiment_dir: The ``evaluations/<experiment>`` folder.
        row: The evaluation's row, from ``index_row``.
    """
    path = experiment_dir / "index.csv"
    rows, columns = [], list(row)
    if path.exists():
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            columns = list(reader.fieldnames or columns)
            rows = [r for r in reader if r["evaluation"] != row["evaluation"]]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows + [row])


def check_servers(urls: set[str]) -> None:
    """Stop with the command that starts the domain servers if one is not answering.

    Args:
        urls: The servers' base URLs.

    Raises:
        SystemExit: If a server does not answer its health check.
    """
    for url in sorted(urls):
        try:
            httpx.get(f"{url}/health", timeout=3).raise_for_status()
        except httpx.HTTPError as exc:
            raise SystemExit(
                f"error: domain server {url} is not answering ({type(exc).__name__}); "
                "start the servers with: docker compose -f docker/compose.yaml up -d"
            ) from exc


def load_model(path: Path) -> tuple[LLMConfig, dict]:
    """The benchmark's model config and the price table from a model file.

    The file holds ``LLMConfig`` fields plus ``api_key_env``, the environment
    variable that holds the API key, and ``pricing``, the ``PRICING_KEYS`` in
    US dollars per million tokens.

    Args:
        path: The model file.

    Returns:
        The config and the price table.

    Raises:
        SystemExit: If a price is missing, before any money is spent on runs
            whose cost could not be reported.
        RuntimeError: If the API key's variable is not set.
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
    """US dollars for a run's model calls under a price table.

    ``input_tokens`` include the cached tokens that ``input_token_details``
    breaks out. A call whose prompt is over ``long_prompt_tokens`` is charged
    ``long_prompt_multiplier`` times the rates, as the Claude API does.

    Args:
        usage: One LangChain ``usage_metadata`` dict per model call.
        pricing: The price table from the model file.

    Returns:
        The cost.
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
    execution: dict,
    names: checks.Names,
    pricing: dict,
) -> dict:
    """One attempt's ``tasks.csv`` row from the executor's record of it.

    Args:
        task_id: The task's dataset ID.
        attempt: The attempt number, from 1.
        execution: The executor's account of the attempt, one entry of the
            ``runs`` list it returns, or a stand-in holding the error if it
            raised before the attempt started; such an attempt counts as
            failed with none of its checks passed and zero tokens.
        names: The maps from ``checks.prepare``.
        pricing: The price table from the model file.

    Returns:
        The row; the ``upstream_`` columns give the benchmark's own count.
    """
    usage = [
        step["usage_metadata"]
        for step in execution.get("conversation_flow", [])
        if step["type"] == "ai_message" and step.get("usage_metadata")
    ]
    details = [u.get("input_token_details") or {} for u in usage]
    results = execution.get("verification_results", {})
    upstream_passed, upstream_total, upstream_success = checks.upstream_verdict(
        results, names
    )
    return {
        "task_id": task_id,
        "attempt": attempt,
        "success": checks.passed(results),
        "checks_passed": sum(bool(r["passed"]) for r in results.values()),
        "checks_total": len(names.original)
        - sum(p["disposition"] == "excluded" for p in names.patched.values()),
        "upstream_success": upstream_success,
        "upstream_checks_passed": upstream_passed,
        "upstream_checks_total": upstream_total,
        "error": execution.get("error", ""),
        "tool_errors": sum(
            not report.succeeded(step.get("result") or {})
            for step in execution.get("conversation_flow", [])
            if step["type"] == "tool_result"
        ),
        "model_calls": len(usage),
        "input_tokens": sum(u["input_tokens"] for u in usage),
        "output_tokens": sum(u["output_tokens"] for u in usage),
        "cache_read_tokens": sum(d.get("cache_read") or 0 for d in details),
        "cache_write_tokens": sum(d.get("cache_creation") or 0 for d in details),
        "cost_usd": round(cost(usage, pricing), 6),
        "seconds": execution.get("execution_time_ms", 0) / 1000,
    }


def outputs(
    task_id: str,
    attempt: int,
    execution: dict,
    changes: dict | None,
    keys: dict,
    names: checks.Names,
    pricing: dict,
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Trace each failed check, then one attempt's rows for ``tasks.csv``, ``checks.csv``, ``cells.csv`` and ``changes.csv``.

    The trace is attached to the check's result before anything is written,
    so the record, the CSVs and the page agree.

    Args:
        task_id: The task's dataset ID.
        attempt: The attempt number, from 1.
        execution: The executor's account of the attempt, edited in place.
        changes: The attempt's changes, as ``states.diff`` gives them, or
            ``None`` when they were not read.
        keys: Each table's primary key columns.
        names: The maps from ``checks.prepare``.
        pricing: The price table from the model file.

    Returns:
        The ``tasks.csv`` row, then the ``checks.csv``, ``cells.csv`` and
        ``changes.csv`` rows.
    """
    results = execution.get("verification_results", {})
    agent_calls = report.calls(execution)
    for check in results.values():
        if not check["passed"] and check.get("diagnosis"):
            check["trace"] = report.trace(check, agent_calls, keys)
    queries = [r.get("query", "") for r in results.values()]
    return (
        task_record(task_id, attempt, execution, names, pricing),
        checks.check_records(task_id, attempt, results, names),
        report.cell_rows(task_id, attempt, results),
        report.change_rows(task_id, attempt, changes, queries),
    )


def save_attempt(
    folder: Path, attempt: int, result: dict, changes: dict | None
) -> None:
    """Write an attempt's record, its changes against the seed and its page into the task's folder.

    Args:
        folder: The task's folder under the evaluation's ``tasks/``.
        attempt: The attempt number, from 1.
        result: What the executor returned (its ``runs`` list holds the one execution).
        changes: The attempt's changes, or ``None`` when they were not read.
    """
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
    check_rows: list[dict],
    cell_rows: list[dict],
    change_rows: list[dict],
) -> None:
    """Write an evaluation's ``tasks.csv``, ``checks.csv``, ``cells.csv``, ``changes.csv`` and ``summary.md``.

    Args:
        out: The evaluation folder.
        settings: The ``domain``, ``mode`` and ``model`` (a name, or the model
            config as ``evaluation.json`` keeps it) that head the summary.
        records: The ``tasks.csv`` rows.
        check_rows: The ``checks.csv`` rows.
        cell_rows: The ``cells.csv`` rows.
        change_rows: The ``changes.csv`` rows.
    """
    model = settings.get("model")
    if isinstance(model, dict):
        model = model.get("llm_model", "?")
    for name, columns, rows in (
        ("tasks.csv", list(records[0]) if records else [], records),
        ("checks.csv", checks.COLUMNS, check_rows),
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
            check_rows,
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
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Run one task once with the benchmark's ReAct agent, save the attempt and return its rows.

    Args:
        task_id: The task's dataset ID.
        config: The task's config, from ``load_tasks``.
        attempt: The attempt number, from 1.
        model: The benchmark's model config.
        pricing: The price table from the model file.
        out: The evaluation folder; the record goes to ``out/tasks/<task_id>``.
        domain: The evaluation's domain.
        trace: Send the attempt to Langfuse as one trace, scored with its verdict.
        thinking: A ``thinking`` setting for the model, or ``None``.
        tags: The trace's tags; the attempt number is added.

    Returns:
        What ``outputs`` returns.
    """
    # One executor call per attempt, so that each attempt is its own trace and
    # its own unit of concurrency; the executor's own loop over runs stays at one.
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
        task_id=task_id,
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
            # Raised before the attempt started, e.g. a server that is not up.
            result = {"runs": [{"run_number": 1, "error": repr(exc)}]}
        execution = result["runs"][0]
        if executor.diagnosis_error:
            execution["diagnosis_error"] = executor.diagnosis_error
        # The executor's own verdict reads an excluded check as a failure.
        if "verification_results" in execution:
            execution["overall_success"] = checks.passed(
                execution["verification_results"]
            )
        changes = executor.changes
        try:
            record, rows, cells, changed = outputs(
                task_id,
                attempt,
                execution,
                changes,
                executor.layout["keys"],
                executor.names,
                pricing,
            )
        except Exception as exc:  # noqa: BLE001
            # A fault in the trace or the rows must not end the evaluation;
            # the attempt keeps its verdict and the fault is kept on the execution.
            execution["report_error"] = repr(exc)
            for check in execution.get("verification_results", {}).values():
                check.pop("trace", None)
            record = task_record(task_id, attempt, execution, executor.names, pricing)
            rows = checks.check_records(
                task_id,
                attempt,
                execution.get("verification_results", {}),
                executor.names,
            )
            cells, changed = [], []
        if span is not None:
            queries = [
                r.get("query", "")
                for r in execution.get("verification_results", {}).values()
            ]
            tracing.record_outcome(
                span,
                record,
                rows,
                states.summary(changes, queries) if changes is not None else None,
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
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Run every task ``attempts`` times, at most ``concurrency`` runs at once, and gather the rows.

    Each run seeds its own database on the domain server, so runs share
    nothing and can overlap; the limit keeps the server and the model API's
    rate limits in range.

    Args:
        tasks: Each task ID to its config, from ``load_tasks``.
        model: The benchmark's model config.
        pricing: The price table from the model file.
        out: The evaluation folder.
        domain: The evaluation's domain.
        attempts: Attempts per task.
        concurrency: Runs in flight at once.
        trace: Send each run to Langfuse.
        thinking: A ``thinking`` setting for the model, or ``None``.
        tags: The traces' tags.

    Returns:
        The ``tasks.csv`` rows, then the ``checks.csv``, ``cells.csv`` and
        ``changes.csv`` rows, in task order and then attempt order.
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
            f"[{done}/{total}] {task_id} attempt {attempt} {outcome}: checks "
            f"{record['checks_passed']}/{record['checks_total']}, "
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
    """The runner's command-line options; every one defaults to ``None`` so that only given options override the experiment file.

    Args:
        parser: The ``run`` subparser.
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
        "--task",
        action="append",
        metavar="TASK_ID",
        help="run only this task (repeat the option for several)",
    )
    parser.add_argument(
        "--split",
        help="run only one set of a split in configs/splits/, as <name>:<set>, "
        "e.g. haiku-20-40-40:training",
    )
    parser.add_argument("--attempts", type=int, help="attempts per task")
    parser.add_argument("--concurrency", type=int, help="runs in flight at once")
    parser.add_argument(
        "--label", help="a word added to the evaluation's folder name, e.g. after-fix"
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="evaluation folder (default: evaluations/<experiment>/<name>)",
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
    """Run the experiment named by the parsed arguments, one evaluation per domain.

    Args:
        args: The parsed arguments.
    """
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
    """Run one domain's tasks as one evaluation: the folder, its tables, ``evaluation.json`` and the index row.

    Args:
        experiment: The settings from ``load_experiment``.
        domain: The domain to run.
        model: The benchmark's model config.
        pricing: The price table from the model file.
        trace: Send each run to Langfuse.
        thinking: A ``thinking`` setting for the model, or ``None``.
        version: The code's version when the run began, from
            ``mas_optimiser.code_version``, recorded in ``evaluation.json`` and the index.

    Raises:
        SystemExit: If a chosen task is not one of the domain's.
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
    # A task whose every check the patch list excludes cannot be judged and
    # is not run.
    tasks = {
        task_id: config
        for task_id, config in tasks.items()
        if checks.prepare(task_id, config["verifiers"])[0]
    }
    if experiment["task"]:
        unknown = [t for t in experiment["task"] if t not in tasks]
        if unknown:
            raise SystemExit(f"error: no such task in {domain}: {', '.join(unknown)}")
        tasks = {t: tasks[t] for t in experiment["task"]}
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
        else EVALUATIONS
        / experiment["experiment"]
        / evaluation_name(experiment, domain, started)
    )
    (out / "tasks").mkdir(parents=True)
    # The benchmark logs every request and tool call at INFO; keep that in the
    # evaluation folder rather than on the terminal. One handler per
    # evaluation, since one process may run several domains.
    handler = logging.FileHandler(out / "evaluation.log")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    tags = [domain, experiment["mode"], "arm:default", model.llm_model]
    try:
        records, check_rows, cell_rows, change_rows = asyncio.run(
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
        check_rows,
        cell_rows,
        change_rows,
    )
    counted = totals(records, len(tasks), experiment["attempts"])
    kept = ("experiment", "experiment_file", "label", "overrides", "mode", "limit")
    settings = {key: experiment[key] for key in kept} | {
        "evaluation": out.name,
        "harness": "default",
        "model": {k: v for k, v in vars(model).items() if k != "llm_api_key"},
        "pricing": pricing,
        "domain": domain,
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
    (out / "evaluation.json").write_text(
        json.dumps(settings | {"totals": counted}, indent=2)
    )
    write_index_row(out.parent, index_row(settings, counted))
    print(
        f"\n{counted['successes']}/{counted['total_attempts']} attempts passed, "
        f"{counted['tasks_passed_every_attempt']}/{counted['tasks']} tasks passed every "
        f"attempt ({counted['errors']} errors, counted as failures); checks "
        f"{counted['checks_passed']}/{counted['checks_total']}. Results in {out}"
        + (
            f"; traces in Langfuse session {out.parent.name}/{out.name}"
            if trace
            else ""
        )
    )
