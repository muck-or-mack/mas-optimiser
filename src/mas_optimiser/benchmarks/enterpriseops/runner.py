"""Run EnterpriseOps-Gym tasks through the benchmark's executor and score them.

The runner loads one domain's tasks, runs each with the agent of the
experiment's harness (``harnesses/<name>/agent.py``; the starting harness,
``default``, is the benchmark's ReAct agent) against the Docker domain
servers, and writes one run folder under
``evaluations/<experiment>/`` with one evaluation folder per domain inside it
(``2026-11-03-0803/itsm``): ``summary.md``, ``tasks.csv`` (a row per
attempt), ``checks.csv``, ``cells.csv``, ``tables.csv`` and ``failures.csv``
(see ``report``), ``evaluation.json`` with the settings and totals, ``evaluation.log``, and per attempt under
``tasks/<task_id>/`` the executor's full record (``attempt-<n>.json``), its
changes against the seed (``attempt-<n>.changes.json``) and a readable page
(``attempt-<n>.md``). Each experiment folder keeps an ``index.csv`` with one
row per evaluation.

An experiment is a named question (``baseline-haiku``), set out in
``configs/experiments/``; a run is one invocation of it, named by its start
time and any ``--label`` word, with ``run.json`` and a pooled ``summary.md``
over its domains; an evaluation is one model on one domain's tasks, the
``<domain>`` folder inside the run. Every other setting is a column of
``index.csv`` and a field of ``evaluation.json`` rather than part of a name. Tasks come from the Hugging Face dataset at a pinned
revision; in oracle and all-tools mode the benchmark's corrected copies in
``data/revised`` replace the dataset's versions. Each attempt gets a fresh
``BenchmarkExecutor``, which seeds a database, runs the agent once, runs every
check against the final state and deletes the database.

The benchmark's own ``evaluate.py`` is not used because the runner has to own
the loop: a harness's agent plugs in here as an orchestrator class. The model's API
key is read from the environment (``.env``), never written to a config file,
and a run that raises is recorded and scored as a failure rather than retried
or dropped. Scoring counts every distinct check of a task and compares count
checks numerically, with the executor's own verdict kept beside the corrected
one (see ``checks``).

Sources: ``docs/how-it-works.md``, section 6, the rows for ``runner``.
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import runpy
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
import yaml
from datasets import load_dataset
from dotenv import load_dotenv

import mas_optimiser
from mas_optimiser import markdown, tracing
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
from benchmark.mcp_client import MCPClient
from benchmark.models import BenchmarkConfig, LLMConfig
from orchestrators.base import AgentOrchestrator

HF_DATASET = "ServiceNow-AI/EnterpriseOps-Gym"
# Dataset commit of 30/04/2026, pinned so that a later edit of the dataset
# cannot change the tasks between runs.
HF_REVISION = "c8e538eae8a6205294f0a86675fefdc1fac408f6"
# Dataset columns stored as JSON strings, and columns that are not part of a
# task config (as in evaluate.py).
JSON_COLUMNS = {"gym_servers_config", "verifiers"}
ID_COLUMNS = {"task_id", "domain"}

CONFIGS = REPO_ROOT / "configs"
EVALUATIONS = REPO_ROOT / "evaluations"
# The harnesses that can be named without a path: one folder each, holding
# the agent's code (``agent.py``, the class ``Agent``) and what it reads.
HARNESSES = REPO_ROOT / "harnesses"
DESCRIPTION = __doc__.split("\n\n")[0]
DOMAINS = ["itsm", "csm", "hr"]
# ``oracle`` and the ``plus_N_tools`` modes are the dataset's own configs;
# ``all_tools`` is the oracle tasks with the tool filter removed, so the agent
# sees every tool the domain's server offers.
MODES = ["oracle", "all_tools", "plus_5_tools", "plus_10_tools", "plus_15_tools"]
# Every key an experiment file may set, with its default. ``model`` and
# ``domain`` have none: the file or the command line must supply them.
# ``domain`` may be a list, giving one evaluation per domain.
EXPERIMENT_DEFAULTS = {
    "model": None,
    "domain": None,
    "mode": "all_tools",
    "limit": None,
    "split": None,
    "task": None,
    "attempts": 1,
    "concurrency": 4,
    "label": None,
    "out": None,
    "trace": True,
    "thinking_summary": False,
    "prompt_caching": True,
    "model_timeout": 300,
    "harness": "default",
    # The rest is read by ``mas-optimiser evolve`` only: the model of the
    # search roles (the agent's when not given), the rounds of the loop, the
    # candidates proposed in each, the error budget of the whole experiment,
    # and the types of file in the harness folder a proposal may edit.
    "proposer_model": None,
    "rounds": 10,
    "candidates": 1,
    "alpha": 0.05,
    "editable": [".md", ".py"],
}
# The providers whose client takes the time limit on a model call:
# Anthropic's, and the OpenAI-style client the benchmark builds for the other
# three (``vllm`` for a self-hosted server, ``openrouter`` for a hosted one).
TIMEOUT_PROVIDERS = ("anthropic", "openai", "vllm", "openrouter")
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
# ``benchmark_successes`` counts the rows' ``benchmark_success``, the rest are
# columns of the same name.
SUMMED = (
    "checks_passed",
    "checks_total",
    "benchmark_successes",
    "benchmark_checks_passed",
    "benchmark_checks_total",
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
# Anthropic's mark for prompt caching, on the request as a whole or on a
# tool. The provider stores a request up to a mark and bills that part at the
# ``cache_read`` rate when it is sent again within five minutes; the model
# receives the same input either way. The mark is Anthropic's own: other
# providers cache by other means, which nothing here switches on.
CACHE_MARK = {"type": "ephemeral"}
# Where LangChain's usage reports the tokens a call wrote to the cache: under
# the two lifetime keys when the provider gives that breakdown (the general
# key is then zero), under the general key otherwise.
CACHE_WRITE_KEYS = (
    "cache_creation",
    "ephemeral_5m_input_tokens",
    "ephemeral_1h_input_tokens",
)


def mark_tool_list(tools: list[dict]) -> list[dict]:
    """The model's tools with Anthropic's cache mark on the last one.

    A mark on the last tool stores the whole tool list, which is the same for
    every attempt given the same tools, so attempts at other tasks read it
    from the cache. LangChain keeps the mark only on a tool in Anthropic's
    own shape, so the last tool is rewritten from the function-call shape the
    benchmark's client converts to; the model receives the same definition.

    Args:
        tools: The tools as the benchmark's client converts them, each
            ``{"type": "function", "function": {...}}``.

    Returns:
        The same tools, the last one as ``name``, ``description``,
        ``input_schema`` and ``cache_control``.
    """
    if not tools:
        return tools
    last = tools[-1]["function"]
    return [
        *tools[:-1],
        {
            "name": last["name"],
            "description": last["description"],
            "input_schema": last["parameters"],
            "cache_control": CACHE_MARK,
        },
    ]


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
        caching: Switch on Anthropic's prompt caching for the model's calls.
            It changes the cost of repeated input and nothing the model
            receives; it must be ``False`` for any other provider.
        timeout: Seconds a call to the model may wait for its answer before
            the try is given up and repeated, or ``None`` to leave the model
            client as the benchmark builds it, with no limit. It is set on
            the provider's own client and must be ``None`` for a provider
            outside ``TIMEOUT_PROVIDERS``.
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
        caching: bool,
        timeout: float | None,
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
        self.caching = caching
        self.timeout = timeout
        self.seed, self.layout = seed
        self.skip = states.STAMPED.get(domain, frozenset())
        self.changes = None
        self.diagnosis_error = None

    async def initialize(self):
        """Build the clients as the benchmark does, then attach tracing, the thinking setting, prompt caching and the time limit to the model.

        The executor builds its model client here, so this is the first point
        at which the model object exists.
        """
        await super().initialize()
        model = self.llm_client.llm
        if self.trace:
            tracing.attach(model)
        if self.thinking:
            model.thinking = self.thinking
        if self.caching:
            # The mark on the request stores each call up to its end and
            # moves forward as the conversation grows, so every call of an
            # attempt reads what the call before it sent. The mark on the
            # tool list stores the tools by themselves, so that attempts at
            # other tasks given the same tools read them too.
            model.model_kwargs = {**model.model_kwargs, "cache_control": CACHE_MARK}
            convert = self.llm_client._convert_mcp_tools_to_langchain
            self.llm_client._convert_mcp_tools_to_langchain = lambda tools: (
                mark_tool_list(convert(tools))
            )
        if self.timeout:
            # The benchmark builds the model with no time limit, and the
            # LangChain class hands that to the provider's library as "none",
            # not as "unset", which switches off the library's own default of
            # ten minutes: a call on a connection that has gone silent then
            # waits for ever. A try that runs out is repeated by the library
            # and then by the benchmark's client.
            if hasattr(model, "root_async_client"):
                # The OpenAI-style client is built with the model, so the
                # limit goes on the client itself.
                model.root_async_client.timeout = self.timeout
            else:
                # Anthropic's client is built on the first call, so a limit
                # set on the model here applies.
                model.default_request_timeout = self.timeout

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
            The verdicts keyed by unique check name, each with ``benchmark_passed``
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
                        self.layout["columns"],
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
        mode: ``oracle`` (only the tools a task needs), ``all_tools`` (the
            oracle tasks with every tool the server offers) or
            ``plus_5_tools`` and so on (that many distractor tools added).

    Returns:
        Each task ID to its config.
    """
    revised = GYM_ROOT / "data" / "revised" / domain
    # The all-tools mode is the oracle dataset with the executor's tool filter
    # switched off: an empty ``selected_tools`` makes it keep every tool it
    # discovers on the server (benchmark/executor.py, "Filter tools").
    dataset_config = "oracle" if mode == "all_tools" else mode
    tasks = {}
    for row in load_dataset(
        HF_DATASET, dataset_config, split=domain, revision=HF_REVISION
    ):
        # The revised files are oracle tasks. The other modes' rows differ
        # from oracle in their tools and, for a few tasks, their verifiers, so
        # they cannot be patched from these.
        fixed = revised / f"{row['task_id']}.json"
        if dataset_config == "oracle" and fixed.exists():
            config = json.loads(fixed.read_text())
        else:
            config = {
                key: json.loads(value) if key in JSON_COLUMNS else value
                for key, value in row.items()
                if key not in ID_COLUMNS
            }
        if mode == "all_tools":
            config["selected_tools"] = []
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
        if not name or subset not in ("", *splits.SETS):
            raise SystemExit(
                f"error: split must be <name> or <name>:<set> with set one of "
                f"{splits.SETS}, not {settings['split']!r}"
            )
        if not (splits.SPLITS_DIR / f"{name}.json").is_file():
            raise SystemExit(
                f"error: no split named {name!r} in {splits.SPLITS_DIR}; "
                f"draw it with `mas-optimiser split --name {name}`"
            )
    for key in ("attempts", "concurrency"):
        if not isinstance(settings[key], int) or settings[key] < 1:
            raise SystemExit(f"error: {key} must be a whole number of at least 1")
    limit = settings["model_timeout"]
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
        raise SystemExit("error: model_timeout must be a number of seconds above 0")
    for key in ("rounds", "candidates"):
        if not isinstance(settings[key], int) or settings[key] < 1:
            raise SystemExit(f"error: {key} must be a whole number of at least 1")
    if not isinstance(settings["alpha"], float) or not 0 < settings["alpha"] < 1:
        raise SystemExit("error: alpha must be a number between 0 and 1")
    editable = settings["editable"]
    if not isinstance(editable, list) or not all(
        isinstance(e, str) and e.startswith(".") for e in editable
    ):
        raise SystemExit("error: editable must be a list of file types such as .md")
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


def run_name(settings: dict, started: datetime, seconds: bool = False) -> str:
    """A run folder's name: the start day and minute, then any ``--label`` word.

    ``2026-11-03-0803`` sorts by time and reads as one; the seconds are added
    only when a second run of the experiment starts within the same minute.

    Args:
        settings: The experiment's settings, from ``load_experiment``.
        started: The run's start time.
        seconds: Whether to add the seconds to the start time.

    Returns:
        The name.
    """
    name = f"{started:%Y-%m-%d-%H%M%S}" if seconds else f"{started:%Y-%m-%d-%H%M}"
    if settings["label"]:
        name += f"-{settings['label']}"
    return name


def run_folder(settings: dict, started: datetime) -> Path:
    """The folder of a new run under the experiment, unique within the minute.

    Args:
        settings: The experiment's settings, from ``load_experiment``.
        started: The run's start time.

    Returns:
        ``evaluations/<experiment>/<run>``, not yet created.
    """
    folder = EVALUATIONS / settings["experiment"] / run_name(settings, started)
    if folder.exists():
        folder = folder.with_name(run_name(settings, started, seconds=True))
    return folder


def totals(outcomes: list[dict], tasks: int, attempts: int) -> dict:
    """An evaluation's totals over its ``tasks.csv`` rows, as ``evaluation.json`` and the index keep them.

    Args:
        outcomes: The ``tasks.csv`` rows, one per attempt.
        tasks: The number of tasks the evaluation set out to run.
        attempts: The attempts per task it set out to make.

    Returns:
        The counts (attempts and successes, tasks passed on every attempt,
        errors) and the sums of ``SUMMED`` over the rows.
    """
    by_task: dict[str, list[bool]] = {}
    for r in outcomes:
        by_task.setdefault(r["task_id"], []).append(r["success"])
    counted = {
        "tasks": tasks,
        "attempts": attempts,
        "total_attempts": len(outcomes),
        "successes": sum(r["success"] for r in outcomes),
        "tasks_passed_every_attempt": sum(all(v) for v in by_task.values()),
        "errors": sum(bool(r["error"]) for r in outcomes),
    }
    for key in SUMMED:
        column = "benchmark_success" if key == "benchmark_successes" else key
        counted[key] = round(sum(r[column] for r in outcomes), 4)
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
        "run": settings.get("run", ""),
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
        "split": settings.get("split") or "",
        "code_version": settings.get("mas_optimiser_version", ""),
    } | counted


def write_index_row(experiment_dir: Path, row: dict) -> None:
    """Add one evaluation's row to the experiment's ``index.csv``, or replace the row that names the same evaluation.

    A column the file does not have yet is added at the end, empty for the
    earlier rows, so an older index keeps its rows when the row gains a field.
    An index written when the benchmark's own counts were named ``upstream_``
    is read under their present names, so its rows keep those counts.

    Args:
        experiment_dir: The ``evaluations/<experiment>`` folder.
        row: The evaluation's row, from ``index_row``.
    """
    path = experiment_dir / "index.csv"
    rows, columns = [], list(row)
    if path.exists():
        with path.open(newline="") as f:
            header = next(csv.reader(f), None)
            if header:
                columns = [c.replace("upstream_", "benchmark_") for c in header]
            reader = csv.DictReader(f, fieldnames=columns)
            rows = [r for r in reader if r["evaluation"] != row["evaluation"]]
        columns += [key for key in row if key not in columns]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=columns, extrasaction="ignore", restval=""
        )
        writer.writeheader()
        writer.writerows(rows + [row])


def write_run_summary(run_dir: Path, experiment: str) -> dict:
    """Write a run's ``run.json`` and pooled ``summary.md`` from its domains' ``evaluation.json``.

    The totals of every domain folder in the run are summed (``attempts`` per
    task is the same for all and kept as it is), so the headline of the run is
    the harness on the benchmark as a whole, with the domains as rows below it.

    Args:
        run_dir: The run's folder, holding one evaluation folder per domain.
        experiment: The experiment's name.

    Returns:
        The record written to ``run.json``.
    """
    records = {}
    for path in sorted(run_dir.glob("*/evaluation.json")):
        records[path.parent.name] = json.loads(path.read_text())
    domains = [d for d in DOMAINS if d in records] + sorted(
        d for d in records if d not in DOMAINS
    )
    pooled: dict = {}
    for domain in domains:
        for key, value in records[domain]["totals"].items():
            if key == "attempts":
                pooled[key] = value
            else:
                pooled[key] = round(pooled.get(key, 0) + value, 4)
    first = records[domains[0]]
    kept = ("mode", "model", "label", "overrides", "limit", "split", "attempts")
    record = {
        "run": run_dir.name,
        "experiment": experiment,
        "started": min(r["started"] for r in records.values()),
        "finished": max(r["finished"] for r in records.values()),
        "settings": {key: first.get(key) for key in kept}
        | {
            "harness": first.get("harness", "default"),
            "mas_optimiser_version": first.get("mas_optimiser_version", ""),
        },
        "domains": {d: records[d]["totals"] for d in domains},
        "totals": pooled,
    }
    (run_dir / "run.json").write_text(json.dumps(record, indent=2, default=str))
    model = first.get("model")
    model_name = model.get("llm_model", "?") if isinstance(model, dict) else model
    header = [
        "set",
        "tasks",
        "attempts fully passed",
        "tasks passed on every attempt",
        "errors",
        "checks passed",
        "cost (USD)",
        "hours",
    ]

    def cells(name: str, t: dict) -> list[str]:
        return [
            name,
            str(t["tasks"]),
            f"{t['successes']} of {t['total_attempts']}",
            f"{t['tasks_passed_every_attempt']} of {t['tasks']}",
            str(t["errors"]),
            f"{t['checks_passed']} of {t['checks_total']}",
            markdown.amount(t["cost_usd"]),
            markdown.amount(t["seconds"] / 3600),
        ]

    lines = [
        f"# {experiment} {run_dir.name}",
        "",
        f"{model_name}, mode {first.get('mode')}, {pooled['attempts']} attempts per "
        f"task, started {record['started']}. Each domain's own summary is "
        f"`<domain>/summary.md`.",
        "",
        markdown.table(
            header,
            [cells("all", pooled)]
            + [cells(domain, records[domain]["totals"]) for domain in domains],
        ),
    ]
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n")
    return record


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


async def save_tools(out: Path, tasks: dict[str, dict]) -> None:
    """Write every tool each domain server offers to ``out/tools.json``, once per evaluation.

    A task's ``selected_tools`` names the tools the agent is given, but their
    descriptions and argument schemas live only on the server, so without
    this file neither a reader nor a proposer can see what the agent could
    have called or what it could have passed. The file maps each server's URL
    to its tools as the server lists them.

    Args:
        out: The evaluation folder.
        tasks: Each task ID to its config, from ``load_tasks``.

    Raises:
        SystemExit: If a server does not accept a connection.
    """
    servers = {
        server["mcp_server_url"]: server
        for config in tasks.values()
        for server in config["gym_servers_config"]
    }
    tools = {}
    for url, server in sorted(servers.items()):
        client = MCPClient(
            base_url=url,
            auth_config=server.get("auth_config"),
            mcp_endpoint=server.get("mcp_endpoint", "/mcp"),
        )
        if not await client.connect():
            raise SystemExit(f"error: domain server {url} refused the tool listing")
        tools[url] = await client.list_tools()
    (out / "tools.json").write_text(json.dumps(tools, indent=2))


def tool_arguments(out: Path) -> dict[str, list[str]]:
    """Each tool's argument names in the tool's own order, from an evaluation's ``tools.json``.

    Args:
        out: The evaluation folder.

    Returns:
        The names by tool; empty for an evaluation saved before the tool
        list was kept.
    """
    path = out / "tools.json"
    if not path.exists():
        return {}
    return {
        tool["name"]: list((tool.get("inputSchema") or {}).get("properties", {}))
        for tools in json.loads(path.read_text()).values()
        for tool in tools
    }


def load_model(path: Path) -> tuple[LLMConfig, dict]:
    """The benchmark's model config and the price table from a model file.

    The file holds ``LLMConfig`` fields plus ``api_key_env``, the environment
    variable that holds the API key, and ``pricing``, the ``PRICING_KEYS`` in
    US dollars per million tokens. A self-hosted server that asks for no key
    leaves ``api_key_env`` out, and its file may hold a ``server`` block, which
    says how the server is started (``mas_optimiser.server``) and is not read
    here.

    Args:
        path: The model file.

    Returns:
        The config and the price table.

    Raises:
        SystemExit: If a price is missing, before any money is spent on runs
            whose cost could not be reported.
        RuntimeError: If the file names a variable for the API key and it
            is not set.
    """
    fields = yaml.safe_load(path.read_text())
    fields.pop("server", None)
    pricing = fields.pop("pricing", None) or {}
    missing = sorted(PRICING_KEYS - set(pricing))
    if missing:
        raise SystemExit(
            f"error: {path} needs pricing keys {missing} "
            "(US dollars per million tokens, from the provider's price list)"
        )
    key_name = fields.pop("api_key_env", None)
    key = os.environ.get(key_name, "") if key_name else ""
    if key_name and not key:
        raise RuntimeError(f"{key_name} is not set; add it to .env (see .env.example)")
    return LLMConfig(llm_api_key=key, **fields), pricing


def cost(usage: list[dict], pricing: dict) -> float:
    """US dollars for a run's model calls under a price table.

    ``input_tokens`` include the cached tokens that ``input_token_details``
    breaks out. A call whose prompt is over ``long_prompt_tokens`` is charged
    ``long_prompt_multiplier`` times the rates, as the Claude API does.
    Tokens written to the cache are charged ``cache_write``, the rate of the
    five-minute lifetime; the dearer one-hour lifetime is never asked for.

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
        cache_write = sum(details.get(key) or 0 for key in CACHE_WRITE_KEYS)
        long_prompt = call["input_tokens"] > pricing["long_prompt_tokens"]
        total += (pricing["long_prompt_multiplier"] if long_prompt else 1) * (
            (call["input_tokens"] - cache_read - cache_write) * pricing["input"]
            + cache_read * pricing["cache_read"]
            + cache_write * pricing["cache_write"]
            + call["output_tokens"] * pricing["output"]
        )
    return total / 1_000_000


def task_outcome(
    task_id: str,
    attempt: int,
    execution: dict,
    names: checks.Names,
    pricing: dict,
    tables: list[dict],
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
        tables: The attempt's ``tables.csv`` rows, from ``report.table_rows``;
            empty when its changes were not read.

    Returns:
        The row. The ``tables_`` columns count the tables with a check on
        them by result; the ``benchmark_`` columns give the benchmark's own
        count; ``lookups_empty`` counts the lookups that found no record,
        which are not among the ``tool_errors``.
    """
    usage = [
        step["usage_metadata"]
        for step in execution.get("conversation_flow", [])
        if step["type"] == "ai_message" and step.get("usage_metadata")
    ]
    details = [u.get("input_token_details") or {} for u in usage]
    results = execution.get("verification_results", {})
    benchmark_passed, benchmark_total, benchmark_success = checks.benchmark_verdict(
        results, names
    )
    agent_calls = report.calls(execution)
    judged = [table["result"] for table in tables if table["checks"]]
    return {
        "task_id": task_id,
        "attempt": attempt,
        "success": checks.passed(results),
        "checks_passed": sum(bool(r["passed"]) for r in results.values()),
        "checks_total": len(names.original)
        - sum(p["disposition"] == "excluded" for p in names.patched.values()),
        "tables_checked": len(judged),
        "tables_correct": judged.count("correct"),
        "tables_wrong": judged.count("wrong"),
        "tables_missed": judged.count("missed"),
        "benchmark_success": benchmark_success,
        "benchmark_checks_passed": benchmark_passed,
        "benchmark_checks_total": benchmark_total,
        "error": execution.get("error", ""),
        "tool_errors": sum(
            call.get("success") is False and not call["nothing_found"]
            for call in agent_calls
        ),
        "lookups_empty": sum(call.get("nothing_found", False) for call in agent_calls),
        "model_calls": len(usage),
        "input_tokens": sum(u["input_tokens"] for u in usage),
        "output_tokens": sum(u["output_tokens"] for u in usage),
        "cache_read_tokens": sum(d.get("cache_read") or 0 for d in details),
        "cache_write_tokens": sum(
            d.get(key) or 0 for d in details for key in CACHE_WRITE_KEYS
        ),
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
    arguments: dict[str, list[str]],
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Trace each failed check, then one attempt's rows for ``tasks.csv``, ``checks.csv``, ``cells.csv`` and ``tables.csv``.

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
        arguments: Each tool's argument names, from ``tool_arguments``.

    Returns:
        The ``tasks.csv`` row, then the ``checks.csv``, ``cells.csv`` and
        ``tables.csv`` rows.
    """
    results = execution.get("verification_results", {})
    agent_calls = report.calls(execution)
    for check in results.values():
        if not check["passed"] and check.get("diagnosis"):
            check["trace"] = report.trace(check, agent_calls, keys, arguments)
    tables = report.table_rows(task_id, attempt, results, changes)
    return (
        task_outcome(task_id, attempt, execution, names, pricing, tables),
        checks.check_records(task_id, attempt, results),
        report.cell_rows(task_id, attempt, results),
        tables,
    )


def save_attempt(
    folder: Path,
    attempt: int,
    result: dict,
    changes: dict | None,
    selected: list[str],
    arguments: dict[str, list[str]],
) -> None:
    """Write an attempt's record, its changes against the seed and its page into the task's folder.

    Args:
        folder: The task's folder under the evaluation's ``tasks/``.
        attempt: The attempt number, from 1.
        result: What the executor returned (its ``runs`` list holds the one execution).
        changes: The attempt's changes, or ``None`` when they were not read.
        selected: The task's ``selected_tools``; empty when the agent was
            given every tool the server offers.
        arguments: Each tool's argument names, from ``tool_arguments``.
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
        report.attempt_text(
            folder.name,
            attempt,
            result["runs"][0],
            changes,
            selected or list(arguments),
            arguments,
        )
    )


def write_tables(
    out: Path,
    settings: dict,
    outcomes: list[dict],
    check_rows: list[dict],
    cell_rows: list[dict],
    table_rows: list[dict],
) -> None:
    """Write an evaluation's ``tasks.csv``, ``checks.csv``, ``cells.csv``, ``tables.csv``, ``failures.csv`` and ``summary.md``.

    ``failures.csv`` counts the failures by where they are and of what
    kind, most frequent first: a failed check with no row to compare by its
    table, and a wrong cell by its table and column and by how its value
    came about. Each is counted once per attempt it occurs in.

    Args:
        out: The evaluation folder.
        settings: The ``evaluation`` name, ``domain``, ``mode``, ``model``
            (a name, or the model config as ``evaluation.json`` keeps it) and
            ``split`` that head the summary; without a name the folder's is
            used. The summary of a held-out set of a split leaves out what
            went wrong (``report.summary``); the files are written in full.
        outcomes: The ``tasks.csv`` rows, one per attempt.
        check_rows: The ``checks.csv`` rows.
        cell_rows: The ``cells.csv`` rows.
        table_rows: The ``tables.csv`` rows.
    """
    model = settings.get("model")
    if isinstance(model, dict):
        model = model.get("llm_model", "?")
    for file_name, columns, rows in (
        ("tasks.csv", list(outcomes[0]) if outcomes else [], outcomes),
        ("checks.csv", checks.COLUMNS, check_rows),
        ("cells.csv", report.CELL_COLUMNS, cell_rows),
        ("tables.csv", report.TABLE_COLUMNS, table_rows),
    ):
        with (out / file_name).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
    place = ["table", "column", "failure", "how"]
    failed = [
        c | {"column": "", "how": ""}
        for c in check_rows
        if c["passed"] is False and c["failure"] not in ("", "wrong values")
    ] + [
        c | {"failure": "wrong value"} for c in cell_rows if not c["ok"] and c["column"]
    ]
    failures = (
        pd.DataFrame(failed, columns=["task_id", "attempt", *place])
        .drop_duplicates()
        .groupby(place, as_index=False)
        .agg(attempts=("attempt", "size"), tasks=("task_id", "nunique"))
        .sort_values(
            ["attempts", "tasks", *place], ascending=[False, False, *[True] * 4]
        )
    )
    failures.to_csv(out / "failures.csv", index=False)
    (out / "summary.md").write_text(
        report.summary(
            settings.get("evaluation", out.name),
            {
                "domain": settings.get("domain"),
                "mode": settings.get("mode"),
                "model": model,
                "split": settings.get("split"),
            },
            outcomes,
            check_rows,
            cell_rows,
            table_rows,
            failures.to_dict("records"),
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
    caching: bool,
    timeout: float | None,
    agent: type[AgentOrchestrator],
    tags: list[str],
    arguments: dict[str, list[str]],
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Run one task once with the harness's agent, save the attempt and return its rows.

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
        caching: Use Anthropic's prompt caching.
        timeout: Seconds a try at a model call may wait, or ``None`` for no limit.
        agent: The harness's agent class, which the executor runs the task with.
        tags: The trace's tags; the attempt number is added.
        arguments: Each tool's argument names, from ``tool_arguments``.

    Returns:
        What ``outputs`` returns.
    """
    # One executor call per attempt, so that each attempt is its own trace and
    # its own unit of concurrency; the executor's own loop over runs stays at one.
    executor = TaskExecutor(
        BenchmarkConfig(**(config | {"number_of_runs": 1})),
        model,
        orchestrator_class=agent,
        trace=trace,
        thinking=thinking,
        caching=caching,
        timeout=timeout,
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
            outcome, rows, cells, changed = outputs(
                task_id,
                attempt,
                execution,
                changes,
                executor.layout["keys"],
                executor.names,
                pricing,
                arguments,
            )
        except Exception as exc:  # noqa: BLE001
            # A fault in the trace or the rows must not end the evaluation;
            # the attempt keeps its verdict and the fault is kept on the execution.
            execution["report_error"] = repr(exc)
            for check in execution.get("verification_results", {}).values():
                check.pop("trace", None)
            outcome = task_outcome(
                task_id, attempt, execution, executor.names, pricing, []
            )
            rows = checks.check_records(
                task_id, attempt, execution.get("verification_results", {})
            )
            cells, changed = [], []
        if span is not None:
            queries = [
                r.get("query", "")
                for r in execution.get("verification_results", {}).values()
            ]
            tracing.record_outcome(
                span,
                outcome,
                rows,
                states.summary(changes, queries) if changes is not None else None,
            )
    save_attempt(
        out / "tasks" / task_id,
        attempt,
        result,
        changes,
        config["selected_tools"],
        arguments,
    )
    return outcome, rows, cells, changed


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
    caching: bool,
    timeout: float | None,
    agent: type[AgentOrchestrator],
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
        caching: Use Anthropic's prompt caching.
        timeout: Seconds a try at a model call may wait, or ``None`` for no limit.
        agent: The harness's agent class.
        tags: The traces' tags.

    Returns:
        The ``tasks.csv`` rows, then the ``checks.csv``, ``cells.csv`` and
        ``tables.csv`` rows, in task order and then attempt order.
    """
    await save_tools(out, tasks)
    arguments = tool_arguments(out)
    semaphore = asyncio.Semaphore(concurrency)
    total = len(tasks) * attempts
    done = 0

    async def run_one(task_id: str, config: dict, attempt: int) -> tuple:
        nonlocal done
        async with semaphore:
            outcome, rows, cells, changed = await run_task(
                task_id,
                config,
                attempt,
                model,
                pricing,
                out,
                domain=domain,
                trace=trace,
                thinking=thinking,
                caching=caching,
                timeout=timeout,
                agent=agent,
                tags=tags,
                arguments=arguments,
            )
        done += 1
        status = (
            "error" if outcome["error"] else "pass" if outcome["success"] else "fail"
        )
        print(
            f"[{done}/{total}] {task_id} attempt {attempt} {status}: checks "
            f"{outcome['checks_passed']}/{outcome['checks_total']}, "
            f"{outcome['model_calls']} calls, {outcome['input_tokens']:,} in / "
            f"{outcome['output_tokens']:,} out tokens, ${outcome['cost_usd']:.4f}, "
            f"{outcome['seconds']:.0f} s"
        )
        return outcome, rows, cells, changed

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
        help="oracle gives the agent only the task's own tools, all_tools every tool "
        "the server offers, plus_N_tools the task's own plus N distractors",
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
        "e.g. seed-0:training",
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
    parser.add_argument(
        "--prompt-caching",
        action=argparse.BooleanOptionalAction,
        help="have the provider store repeated input and bill it at the cheaper "
        "cache rate; Anthropic models only, nothing the model receives changes "
        "(on by default)",
    )
    parser.add_argument(
        "--harness",
        help="the harness whose agent runs the tasks: a folder's name under "
        "harnesses/, or a path to such a folder (default: default)",
    )
    parser.add_argument(
        "--model-timeout",
        type=float,
        help="seconds a call to the model may wait for its answer before the try "
        "is given up and repeated (300 by default)",
    )


def run(args: argparse.Namespace) -> None:
    """Run the experiment named by the parsed arguments, one evaluation per domain.

    Args:
        args: The parsed arguments.

    Raises:
        SystemExit: If the experiment's harness has no folder with an
            ``agent.py``, before any money is spent.
    """
    # The keys only ``evolve`` reads are no options of this command.
    overrides = {key: getattr(args, key, None) for key in EXPERIMENT_DEFAULTS}
    experiment = load_experiment(args.experiment, overrides)
    harness = str(experiment["harness"])
    folder = Path(harness) if "/" in harness else HARNESSES / harness
    if not (folder / "agent.py").is_file():
        names = ", ".join(sorted(p.name for p in HARNESSES.iterdir() if p.is_dir()))
        raise SystemExit(
            f"error: no harness at {folder} (a folder holding agent.py); "
            f"harnesses that can be named: {names}"
        )
    # Run from its file and not imported, so that a copy of the folder
    # anywhere, such as a candidate's, loads the same way as a named one.
    agent = runpy.run_path(str(folder / "agent.py"))["Agent"]

    load_dotenv(REPO_ROOT / ".env")
    model, pricing = load_model(config_path("models", experiment["model"]))
    trace = tracing.configured() and experiment["trace"]
    thinking = THINKING_SUMMARY if experiment["thinking_summary"] else None
    # The caching here is Anthropic's: for any other provider nothing is
    # switched on, and the run says so.
    caching = experiment["prompt_caching"] and model.llm_provider == "anthropic"
    if experiment["prompt_caching"] and not caching:
        print(
            "Prompt caching is built for Anthropic models only: "
            f"not used with provider {model.llm_provider!r}."
        )
    # The time limit is set on the provider's own client, so a provider
    # whose client is not known here runs without one, and the run says so.
    timeout = (
        experiment["model_timeout"] if model.llm_provider in TIMEOUT_PROVIDERS else None
    )
    if timeout is None:
        print(
            f"The time limit on model calls is built for the providers "
            f"{', '.join(TIMEOUT_PROVIDERS)}: not set with provider "
            f"{model.llm_provider!r}."
        )
    if trace:
        # The Anthropic SDK also writes a metadata-only span per call into any
        # registered tracer; the LangChain callback already records each call
        # with its content, so those duplicates are switched off.
        os.environ.setdefault("ANTHROPIC_OPEN_TELEMETRY", "false")
    # Taken once, before any task runs: an edit or commit made while a long
    # run goes on must not be stamped on results the edited code never made.
    version = mas_optimiser.code_version()
    # With the UTC offset, so that start times order correctly across a
    # change of the clocks.
    run_dir = run_folder(experiment, datetime.now().astimezone())
    for domain in experiment["domain"]:
        evaluate(
            experiment,
            domain,
            model,
            pricing,
            run_dir=run_dir,
            trace=trace,
            thinking=thinking,
            caching=caching,
            timeout=timeout,
            agent=agent,
            version=version,
        )
    if not experiment["out"]:
        write_run_summary(run_dir, experiment["experiment"])
        print(f"Run summary in {run_dir / 'summary.md'}")
    if trace:
        tracing.shutdown()


def evaluate(
    experiment: dict,
    domain: str,
    model: LLMConfig,
    pricing: dict,
    *,
    run_dir: Path,
    trace: bool,
    thinking: dict | None,
    caching: bool,
    timeout: float | None,
    agent: type[AgentOrchestrator],
    version: str,
) -> None:
    """Run one domain's tasks as one evaluation: the folder, its tables, ``evaluation.json`` and the index row.

    Args:
        experiment: The settings from ``load_experiment``.
        domain: The domain to run.
        model: The benchmark's model config.
        pricing: The price table from the model file.
        run_dir: The run's folder; the evaluation is its ``<domain>`` subfolder
            unless the experiment names an ``out`` folder of its own.
        trace: Send each run to Langfuse.
        thinking: A ``thinking`` setting for the model, or ``None``.
        caching: Use Anthropic's prompt caching.
        timeout: Seconds a try at a model call may wait, or ``None`` for no limit.
        agent: The agent class of the experiment's harness.
        version: The code's version when the run began, from
            ``mas_optimiser.code_version``, recorded in ``evaluation.json`` and the index.

    Raises:
        SystemExit: If a chosen task is not one of the domain's.
    """
    tasks = load_tasks(domain, experiment["mode"])
    # A split's name without a set names all three sets, which is every task.
    name, _, subset = (experiment["split"] or "").partition(":")
    if subset:
        members = set(splits.load_split(name)[subset])
        tasks = {
            task_id: config for task_id, config in tasks.items() if task_id in members
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
    started = datetime.now().astimezone()
    out = Path(experiment["out"]) if experiment["out"] else run_dir / domain
    # The evaluation's name within the experiment: ``<run>/<domain>``, or the
    # bare folder name when ``out`` was given.
    name = out.name if experiment["out"] else f"{run_dir.name}/{domain}"
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
    tags = [domain, experiment["mode"], f"arm:{experiment['harness']}", model.llm_model]
    try:
        outcomes, check_rows, cell_rows, table_rows = asyncio.run(
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
                caching=caching,
                timeout=timeout,
                agent=agent,
                tags=tags,
            )
        )
    finally:
        root.removeHandler(handler)
        handler.close()

    write_tables(
        out,
        {
            "evaluation": name,
            "domain": domain,
            "mode": experiment["mode"],
            "model": model.llm_model,
            "split": experiment["split"],
        },
        outcomes,
        check_rows,
        cell_rows,
        table_rows,
    )
    counted = totals(outcomes, len(tasks), experiment["attempts"])
    kept = ("experiment", "experiment_file", "label", "overrides", "mode", "limit")
    settings = {key: experiment[key] for key in kept} | {
        "evaluation": name,
        "run": "" if experiment["out"] else run_dir.name,
        "split": experiment["split"],
        "harness": experiment["harness"],
        "model": {k: v for k, v in vars(model).items() if k != "llm_api_key"},
        "pricing": pricing,
        "domain": domain,
        "attempts": experiment["attempts"],
        "concurrency": experiment["concurrency"],
        "tasks": list(tasks),
        "dataset": HF_DATASET,
        "dataset_revision": HF_REVISION,
        "thinking": thinking,
        "prompt_caching": caching,
        "model_timeout": timeout,
        "traced": trace,
        "mas_optimiser_version": version,
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    # ``default=str``: an ``--out`` path among the overrides is not JSON.
    (out / "evaluation.json").write_text(
        json.dumps(settings | {"totals": counted}, indent=2, default=str)
    )
    write_index_row(
        out.parent if experiment["out"] else run_dir.parent,
        index_row(settings, counted),
    )
    print(
        f"\n{counted['successes']}/{counted['total_attempts']} attempts passed, "
        f"{counted['tasks_passed_every_attempt']}/{counted['tasks']} tasks passed every "
        f"attempt ({counted['errors']} errors, counted as failures); checks "
        f"{counted['checks_passed']}/{counted['checks_total']}. Results in {out}"
        + (
            f"; traces in Langfuse session {experiment['experiment']}/{name}"
            if trace
            else ""
        )
    )
