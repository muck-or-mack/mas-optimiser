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
import sys
import time
from contextlib import nullcontext
from dataclasses import dataclass
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
from benchmark.llm_client import get_text_content
from benchmark.mcp_client import MCPClient
from benchmark.models import BenchmarkConfig, LLMConfig
from orchestrators.base import AgentOrchestrator

HF_DATASET = "ServiceNow-AI/EnterpriseOps-Gym"
#: Dataset commit of 30/04/2026, pinned so that a later edit of the dataset
#: cannot change the tasks between runs.
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
#: Every key an experiment file may set, with its default. ``model`` and
#: ``domain`` have none: the file or the command line must supply them.
#: ``domain`` may be a list, giving one evaluation per domain.
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
    # What one attempt may use at most, held outside the harness: its seconds
    # and its calls to the model.
    "attempt_timeout": 3600,
    "attempt_calls": 100,
    # An attempt that ended in an error is no verdict on the harness while a
    # service it needs is not answering: the model service, asked a one-word
    # question after a call that failed for good or an attempt cut off at its
    # limit, or the domain's server, asked for its health after any error.
    # Such an attempt is run again up to this many times, each time once the
    # service answers, which is waited for at most this many seconds.
    "attempt_retries": 2,
    "service_wait": 1800,
    "harness": "default",
    # The rest is read by ``mas-optimiser evolve`` only: the model of the
    # search roles (the agent's when not given), the rounds of the loop, the
    # candidates proposed in each, the most independent edits one candidate
    # may hold (in the first round and in the last, with RRSI's schedule from
    # the one to the other), the attempts at each stage where they are to
    # differ from ``attempts`` (the training tasks, the filter, the gate and
    # the test tasks), what the proposer is told of its earlier proposals
    # (the outcome alone, or also how each did on the training tasks), the
    # error budget of the whole experiment,
    # the filter a candidate passes before the full test (the share of the
    # validation tasks it is run on first, none at 0, and the gain in
    # percentage points it must be above there), the pool of training tasks
    # whose traces the search roles may read in a round (its share of the
    # training set, and how it is picked: spread evenly over the tasks ranked
    # by score, or from the two ends of that ranking), the types of file in
    # the harness folder a proposal may edit, and whether a person approves
    # each change before it is run.
    "proposer_model": None,
    "rounds": 10,
    "candidates": 1,
    "drafting": "independent",
    "edits_first": 1,
    "edits_last": 1,
    "attempts_training": None,
    "attempts_filter": None,
    "attempts_gate": None,
    "attempts_test": None,
    "feedback": "outcome",
    "alpha": 0.05,
    "alpha_per": "experiment",
    "second_look": 0.0,
    "filter_share": 0.5,
    "filter_threshold": 0.0,
    "pool_share": 0.5,
    "pool_pick": "spread",
    "editable": [".md", ".py"],
    "approval": "none",
}
#: How the pool of training tasks is picked from the tasks ranked by score:
#: evenly over the ranking, or its lowest and its highest.
POOL_PICKS = ("spread", "ends")
#: What the proposer is told of an earlier proposal: whether it was accepted,
#: or that and how it did on the training tasks.
FEEDBACK = ("outcome", "training")
#: How a round's candidates are drafted when there are several: each with
#: no knowledge of the others, or one after another, each told what those
#: before it in the round proposed.
DRAFTING = ("independent", "in_turn")
#: What ``alpha`` is the chance of: a useless change kept anywhere in the
#: experiment, shared out over its decisions, or at one decision.
ALPHA_PER = ("experiment", "decision")
# The providers whose client takes the time limit on a model call and the
# number of tries of a failed one:
# Anthropic's, and the OpenAI-style client the benchmark builds for the other
# three (``vllm`` for a self-hosted server, ``openrouter`` for a hosted one).
TIMEOUT_PROVIDERS = ("anthropic", "openai", "azureopenai", "vllm", "openrouter")
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
#: Anthropic's mark for prompt caching, on the request as a whole or on a
#: tool. The provider stores a request up to a mark and bills that part at the
#: ``cache_read`` rate when it is sent again within five minutes; the model
#: receives the same input either way. The mark is Anthropic's own: other
#: providers cache by other means, which nothing here switches on.
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


class Repeat(Exception):
    """An attempt ended by a failure of a service it needs, to be run again."""


class TaskExecutor(BenchmarkExecutor):
    """The benchmark's executor with tracing attached, every check counted and the changes kept.

    The task's checks are renamed before the run so that none overwrites
    another, then taken through the patch list (see ``checks``). After the
    checks run, and before the executor deletes the run's database, its final
    state is read and compared with the seed, and each failed verdict is
    diagnosed against the rows the attempt touched (see ``states`` and
    ``diagnosis``).

    The executor also keeps its own record of the conversation, taken where
    the harness reaches the model and the tools (``seen``). The harness's
    record is returned only by an attempt that finishes, so an attempt that
    ends in an error is recorded from this one.

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
        calls: The most calls to the model the attempt may make, or ``None``
            for no cap; a call past it raises inside the harness.
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
        calls: int | None = None,
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
        self.calls = calls
        self.seed, self.layout = seed
        self.skip = states.STAMPED.get(domain, frozenset())
        self.changes = None
        self.diagnosis_error = None
        # What the last call to the model raised once the client's own tries
        # were spent; ``None`` if that call was answered.
        self.call_error = None
        # The conversation as it passes through the clients the harness is
        # handed: the first call's instructions and request, each answer of
        # the model and each answer of a tool, in the entries the starting
        # harness records.
        self.seen: list[dict] = []

    async def initialize(self):
        """Build the clients as the benchmark does, then attach tracing, the thinking setting, prompt caching, the time limit, the tries of a failed call to the model and the record of the conversation.

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
        retries = self.llm_config.max_retries
        if retries is not None:
            # How many more times the provider's library sends a call that
            # was refused or got no answer, waiting between tries for as long
            # as the service asks. It goes on the two clients the time limit
            # goes on, in the same two ways.
            if hasattr(model, "root_async_client"):
                model.root_async_client.max_retries = retries
            else:
                model.max_retries = retries
        # Where the harness reaches the model, in a client the harness is
        # handed and does not build: the calls are counted here, so that no
        # change to the harness lifts the cap, and a call that fails once the
        # client's own tries are spent is noted here, whatever the provider.
        # Each call that is answered is added to the executor's record.
        invoke, made = self.llm_client.invoke_with_tools, 0
        opening = {"system": "system_message", "human": "user_message"}

        async def reached(*args, **kwargs):
            nonlocal made
            made += 1
            if self.calls and made > self.calls:
                raise RuntimeError(
                    f"the attempt made more than {self.calls} calls to the model"
                )
            if not self.seen:
                self.seen += [
                    {"type": opening[message.type], "content": message.content}
                    for message in (args[0] if args else kwargs.get("messages", []))
                    if getattr(message, "type", None) in opening
                ]
            try:
                answer = await invoke(*args, **kwargs)
            except Exception as exc:
                self.call_error = repr(exc)
                raise
            self.call_error = None
            self.seen.append(
                {
                    "type": "ai_message",
                    "content": get_text_content(getattr(answer, "content", "")),
                    "usage_metadata": getattr(answer, "usage_metadata", None) or {},
                    "response_metadata": getattr(answer, "response_metadata", None)
                    or {},
                    "tool_calls": [
                        {"name": call["name"], "args": call["args"]}
                        for call in getattr(answer, "tool_calls", None) or []
                    ],
                }
            )
            return answer

        self.llm_client.invoke_with_tools = reached

        def noted(server: str, call_tool):
            # One server's tool call, with each answer added to the record.
            async def call(tool_name, *args, **kwargs):
                result = await call_tool(tool_name, *args, **kwargs)
                self.seen.append(
                    {
                        "type": "tool_result",
                        "tool_name": tool_name,
                        "result": result,
                        "gym_server": server,
                    }
                )
                return result

            return call

        for server, client in self.mcp_clients.items():
            client.call_tool = noted(server, client.call_tool)

    async def service_answers(self, wait: float) -> bool:
        """Ask the model service a one-word question, again each minute until it answers.

        A call of an attempt can fail because of what the harness sent or
        because the service is not answering, and the two read differently
        from one provider to the next. A request that owes nothing to the
        harness tells them apart on any provider. Each ask is given a minute:
        the provider's library may go on sending a call that gets no answer
        for far longer than any wait (``max_retries`` in the model file).

        Args:
            wait: The longest to go on asking, in seconds; an ask begun
                within it may end up to a minute past it.

        Returns:
            Whether the service answered within that time.
        """
        deadline = time.monotonic() + wait
        while True:
            try:
                await asyncio.wait_for(self.llm_client.llm.ainvoke("ping"), 60)
            except Exception:  # noqa: BLE001 - any failure is no answer
                if time.monotonic() >= deadline:
                    return False
                await asyncio.sleep(min(60, max(1, deadline - time.monotonic())))
            else:
                return True

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
        The settings, plus ``experiment`` (the file's stem), ``experiment_file``,
        ``overrides`` (the values applied) and ``changed`` (each setting whose
        value is not its default); ``domain`` is always a list.

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
    for key in ("attempts", "concurrency", "attempt_calls"):
        if not isinstance(settings[key], int) or settings[key] < 1:
            raise SystemExit(f"error: {key} must be a whole number of at least 1")
    for key in ("model_timeout", "attempt_timeout"):
        limit = settings[key]
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
            raise SystemExit(f"error: {key} must be a number of seconds above 0")
    retries, wait = settings["attempt_retries"], settings["service_wait"]
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise SystemExit("error: attempt_retries must be a whole number, zero or more")
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or wait < 0:
        raise SystemExit(
            "error: service_wait must be a number of seconds, zero or more"
        )
    if settings["approval"] not in ("none", "each"):
        raise SystemExit("error: approval must be none or each")
    for key in ("rounds", "candidates", "edits_first", "edits_last"):
        if not isinstance(settings[key], int) or settings[key] < 1:
            raise SystemExit(f"error: {key} must be a whole number of at least 1")
    stages = ("attempts_training", "attempts_filter", "attempts_gate", "attempts_test")
    for key in stages:
        value = settings[key]
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise SystemExit(
                f"error: {key} must be a whole number of at least 1, or left out"
            )
    if not isinstance(settings["alpha"], float) or not 0 < settings["alpha"] < 1:
        raise SystemExit("error: alpha must be a number between 0 and 1")
    share, threshold = settings["filter_share"], settings["filter_threshold"]
    if (
        isinstance(share, bool)
        or not isinstance(share, (int, float))
        or not 0 <= share < 1
    ):
        raise SystemExit(
            "error: filter_share must be a number from 0 (no filter) to below 1"
        )
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise SystemExit(
            "error: filter_threshold must be a number of percentage points"
        )
    pool = settings["pool_share"]
    if (
        isinstance(pool, bool)
        or not isinstance(pool, (int, float))
        or not 0 < pool <= 1
    ):
        raise SystemExit(
            "error: pool_share must be a number above 0, up to 1 (every training task)"
        )
    again = settings["second_look"]
    if (
        isinstance(again, bool)
        or not isinstance(again, (int, float))
        or not 0 <= again < 1
    ):
        raise SystemExit(
            "error: second_look must be a p-value from 0 (no second look) to below 1"
        )
    if settings["alpha_per"] not in ALPHA_PER:
        raise SystemExit(
            f"error: alpha_per must be one of {', '.join(ALPHA_PER)}, "
            f"not {settings['alpha_per']!r}"
        )
    if settings["drafting"] not in DRAFTING:
        raise SystemExit(
            f"error: drafting must be one of {', '.join(DRAFTING)}, "
            f"not {settings['drafting']!r}"
        )
    if settings["feedback"] not in FEEDBACK:
        raise SystemExit(
            f"error: feedback must be one of {', '.join(FEEDBACK)}, "
            f"not {settings['feedback']!r}"
        )
    if settings["pool_pick"] not in POOL_PICKS:
        raise SystemExit(
            f"error: pool_pick must be one of {', '.join(POOL_PICKS)}, "
            f"not {settings['pool_pick']!r}"
        )
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
    # A proposer named as the agent's model is the default by another name.
    changed = {
        key: settings[key]
        for key, default in EXPERIMENT_DEFAULTS.items()
        if settings[key] != default
        and not (key == "proposer_model" and settings[key] == settings["model"])
    }
    return settings | {
        "experiment": path.stem,
        "experiment_file": str(path),
        "overrides": applied,
        "changed": changed,
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
        errors) and the sums of ``SUMMED`` over the rows. An attempt that
        ended because a service it needs did not answer is in none of them:
        it is no attempt at the task.
    """
    outcomes = [r for r in outcomes if not r.get("service_error")]
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

    The run's pace is given beside them: its attempts and its tokens, input
    and output, over the time on the clock from its start to its end. That
    is what the model service let the run do at its number of attempts at
    once, where the table's hours are the attempts' own, added up.

    Args:
        run_dir: The run's folder, holding one evaluation folder per domain.
        experiment: The experiment's name.

    Returns:
        The record written to ``run.json``; its ``pace`` is ``None`` for a
        run that began and ended within one second.
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
    clock = (
        datetime.fromisoformat(record["finished"])
        - datetime.fromisoformat(record["started"])
    ).total_seconds()
    tokens = pooled.get("input_tokens", 0) + pooled.get("output_tokens", 0)
    record["pace"] = (
        {
            "minutes": round(clock / 60, 1),
            "attempts_an_hour": round(pooled["total_attempts"] * 3600 / clock),
            "tokens_a_minute": round(tokens * 60 / clock),
        }
        if clock > 0
        else None
    )
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
    ]
    if record["pace"]:
        at_once = first.get("concurrency")
        lines += [
            "",
            f"By the clock the run took {record['pace']['minutes']:g} minutes"
            + (f" at {at_once} attempts at once" if at_once else "")
            + f": {record['pace']['attempts_an_hour']:,} attempts an hour and "
            f"{record['pace']['tokens_a_minute']:,} tokens a minute, input and "
            "output together.",
        ]
    lines += [
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

    A server that is busy with many attempts can take longer than the three
    seconds of one check to answer, which is not a server that is down: each
    server is asked up to four times, ten seconds apart, before the command
    stops.

    Args:
        urls: The servers' base URLs.

    Raises:
        SystemExit: If a server does not answer its health check.
    """
    for url in sorted(urls):
        for tries_left in (3, 2, 1, 0):
            try:
                httpx.get(f"{url}/health", timeout=3).raise_for_status()
                break
            except httpx.HTTPError as exc:
                if not tries_left:
                    raise SystemExit(
                        f"error: domain server {url} is not answering "
                        f"({type(exc).__name__}); start the servers with: "
                        "docker compose -f docker/compose.yaml up -d"
                    ) from exc
                time.sleep(10)


async def servers_answer(urls: list[str], wait: float) -> bool:
    """Ask each domain server for its health, again each minute until all answer.

    An attempt can end in an error because of what the harness did or
    because its domain server is down, and a server that is down ends every
    attempt in an error, whatever the harness. A request that owes nothing
    to the harness tells the two apart.

    Args:
        urls: The servers' base URLs.
        wait: The longest to go on asking, in seconds; 0 asks once.

    Returns:
        Whether every server answered within that time.
    """
    deadline = time.monotonic() + wait
    async with httpx.AsyncClient(timeout=3) as client:
        while True:
            try:
                for url in urls:
                    (await client.get(f"{url}/health")).raise_for_status()
            except httpx.HTTPError:
                if time.monotonic() >= deadline:
                    return False
                await asyncio.sleep(min(60, max(1, deadline - time.monotonic())))
            else:
                return True


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


@dataclass
class ModelConfig(LLMConfig):
    """The benchmark's model config, with one setting of the provider's library.

    Attributes:
        max_retries: How many more times the provider's library sends a call
            that failed in a way worth repeating (a refusal for a rate limit,
            a time-out, a server error), waiting between tries for as long as
            the service asks; ``None`` leaves the library's own number.
    """

    max_retries: int | None = None


def load_model(path: Path) -> tuple[ModelConfig, dict]:
    """The model config and the price table from a model file.

    The file holds ``ModelConfig`` fields and ``pricing``, the ``PRICING_KEYS``
    in US dollars per million tokens. What belongs to one's own account is
    kept out of the file: a key ending in ``_env`` names the variable of the
    environment that holds the value of the field it is named after
    (``llm_api_endpoint_env`` for ``llm_api_endpoint``), and ``api_key_env``
    names the one that holds the API key. A self-hosted server that asks for
    no key leaves ``api_key_env`` out, and its file may hold a ``server``
    block, which says how the server is started (``mas_optimiser.server``)
    and is not read here.

    Args:
        path: The model file.

    Returns:
        The config and the price table.

    Raises:
        SystemExit: If a price is missing, before any money is spent on runs
            whose cost could not be reported, or if ``max_retries`` is not a
            whole number or is set for a provider outside
            ``TIMEOUT_PROVIDERS``.
        RuntimeError: If the file names a variable of the environment and it
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
    for name in [name for name in fields if name.endswith("_env")]:
        variable = fields.pop(name)
        if not os.environ.get(variable):
            raise RuntimeError(
                f"{variable} is not set; add it to .env (see .env.example)"
            )
        field = "llm_api_key" if name == "api_key_env" else name.removesuffix("_env")
        fields[field] = os.environ[variable]
    retries = fields.get("max_retries")
    if retries is not None:
        if type(retries) is not int or retries < 0:
            raise SystemExit(
                f"error: {path}: max_retries must be a whole number, zero or more"
            )
        if fields.get("llm_provider") not in TIMEOUT_PROVIDERS:
            raise SystemExit(
                f"error: {path}: max_retries is built for the providers "
                f"{', '.join(TIMEOUT_PROVIDERS)}"
            )
    return ModelConfig(**{"llm_api_key": ""} | fields), pricing


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


def model_usage(execution: dict) -> list[dict]:
    """The token counts of an attempt's answered calls to the model, one per call.

    Args:
        execution: The executor's account of the attempt, one entry of the
            ``runs`` list of its record.

    Returns:
        One LangChain ``usage_metadata`` dict per call that was answered.
    """
    return [
        step["usage_metadata"]
        for step in execution.get("conversation_flow", [])
        if step["type"] == "ai_message" and step.get("usage_metadata")
    ]


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
            raised; such an attempt counts as failed with none of its checks
            passed, and its tokens are those of the calls to the model that
            were answered before the error.
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
    usage = model_usage(execution)
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
        "service_error": bool(execution.get("service_error")),
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
    seconds: float | None = None,
    calls: int | None = None,
    retry: bool = False,
    wait: float = 0,
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Run one task once with the harness's agent, save the attempt and return its rows.

    An attempt ended by a call to the model that failed for good, or cut off
    at its time limit, is no verdict on the harness until the model service
    has been asked a one-word question: through an outage the provider's
    library may go on sending a call for longer than the limit, and the
    limit is then what ends the attempt. If the service answers, the attempt
    can be run again; if it does not within ``wait`` seconds, the attempt is
    recorded with ``service_error`` set.

    An attempt that ended in any error is likewise no verdict until the
    domain's server has been asked for its health. If the server answers at
    once, the error is the harness's own and the attempt is a failure. If
    it does not, it is waited for: once it answers the attempt can be run
    again, and if it does not within ``wait`` seconds the attempt is
    recorded with ``service_error`` set.

    An attempt that ends in an error returns nothing of the harness's own
    record. It is saved with the executor's record of the conversation up to
    the error and the time it took, so that its page shows what led to the
    error and its tokens are counted; its checks are not run and the rows it
    changed are not read.

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
        seconds: The longest the attempt may take, or ``None`` for no limit;
            past it the attempt is ended, and recorded as an error unless
            the model service proves silent or the attempt is run again.
        calls: The most calls to the model the attempt may make, or ``None``.
        retry: Whether the attempt may be asked for again.
        wait: The longest to wait for the model service or the domain's
            server to answer, in seconds.

    Returns:
        What ``outputs`` returns.

    Raises:
        Repeat: If a call to the model failed, the attempt ran past its
            limit or the domain's server was down, the service answers again
            and ``retry`` is set; nothing of the attempt is saved.
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
        calls=calls,
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
        cut_off, started = False, time.monotonic()
        try:
            result = await asyncio.wait_for(executor.execute_benchmark(), seconds)
        except TimeoutError:
            # Ended from outside the harness, whatever the harness was doing.
            said = f"the attempt ran past its limit of {seconds:g} seconds"
            result = {"runs": [{"run_number": 1, "error": said}]}
            # A call the provider's library goes on sending through an outage
            # never fails, so the limit is what ends the attempt. Before the
            # model client is built no call can have been waiting.
            cut_off = executor.llm_client is not None
        except Exception as exc:
            # Raised before the attempt started, e.g. a server that is not up.
            result = {"runs": [{"run_number": 1, "error": repr(exc)}]}
        execution = result["runs"][0]
        if execution.get("error") and "conversation_flow" not in execution:
            execution["conversation_flow"] = executor.seen
            execution["execution_time_ms"] = int(1000 * (time.monotonic() - started))
        if executor.call_error or cut_off:
            # The attempt's last call to the model failed, whether or not the
            # harness went on to end the attempt in its own way, or the
            # attempt was cut off at its limit.
            if not await executor.service_answers(wait):
                execution["service_error"] = True
            elif retry:
                raise Repeat
        if execution.get("error") and not execution.get("service_error"):
            # Whatever the error says, it is the harness's own only if the
            # domain's server is answering: one that is down ends every
            # attempt in an error.
            urls = [server["mcp_server_url"] for server in config["gym_servers_config"]]
            if not await servers_answer(urls, 0):
                if not await servers_answer(urls, wait):
                    execution["service_error"] = True
                elif retry:
                    raise Repeat
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
    seconds: float | None = None,
    calls: int | None = None,
    retries: int = 0,
    wait: float = 0,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Run every task ``attempts`` times, at most ``concurrency`` runs at once, and gather the rows.

    Each run seeds its own database on the domain server, so runs share
    nothing and can overlap; the limit keeps the server and the model API's
    rate limits in range.

    Once an attempt has been recorded with ``service_error``, no attempt is
    started any more: a service has been waited for and did not answer, and
    each further attempt would wait for it as long. An attempt not started
    leaves no row.

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
        seconds: The longest one attempt may take, or ``None`` for no limit.
        calls: The most calls to the model one attempt may make, or ``None``.
        retries: How many times an attempt ended by a failed call to the
            model, or while the domain's server was down, is run again.
        wait: The longest to wait for that service before each of those,
            in seconds.

    Returns:
        The ``tasks.csv`` rows, then the ``checks.csv``, ``cells.csv`` and
        ``tables.csv`` rows, in task order and then attempt order, of the
        attempts that were started.
    """
    await save_tools(out, tasks)
    arguments = tool_arguments(out)
    semaphore = asyncio.Semaphore(concurrency)
    total = len(tasks) * attempts
    done, silent = 0, False

    async def run_one(task_id: str, config: dict, attempt: int) -> tuple | None:
        nonlocal done, silent
        async with semaphore:
            if silent:
                return None
            # The last time round nothing asks for a repeat, so the loop
            # always ends with an outcome.
            for left in range(retries, -1, -1):
                try:
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
                        seconds=seconds,
                        calls=calls,
                        retry=left > 0,
                        wait=wait,
                    )
                except Repeat:
                    continue
                break
            silent = silent or bool(outcome.get("service_error"))
        done += 1
        status = (
            "no service"
            if outcome.get("service_error")
            else "error"
            if outcome["error"]
            else "pass"
            if outcome["success"]
            else "fail"
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
    results = [result for result in results if result]
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
    parser.add_argument(
        "--attempt-timeout",
        type=float,
        help="seconds one attempt may take before it is ended and counted as an "
        "error (3600 by default)",
    )
    parser.add_argument(
        "--attempt-calls",
        type=int,
        help="calls to the model one attempt may make before it is ended and "
        "counted as an error (100 by default)",
    )
    parser.add_argument(
        "--attempt-retries",
        type=int,
        help="times an attempt is run again when a call to the model failed "
        "for good, the attempt ran past its seconds or the domain's server "
        "was down (2 by default)",
    )
    parser.add_argument(
        "--service-wait",
        type=float,
        help="seconds to wait for the model service or the domain's server "
        "to answer again before such an attempt is recorded as a failure of "
        "the service and not of the harness (1800 by default)",
    )


def run(args: argparse.Namespace) -> None:
    """Run the experiment named by the parsed arguments, one evaluation per domain.

    Args:
        args: The parsed arguments.

    Raises:
        SystemExit: If the experiment's harness has no folder with an
            ``agent.py``, before any money is spent; or, at the end, if
            attempts ended because a service they need did not answer.
    """
    # The keys only ``evolve`` reads are no options of this command.
    overrides = {key: getattr(args, key, None) for key in EXPERIMENT_DEFAULTS}
    experiment = load_experiment(args.experiment, overrides)
    print("Settings that differ from the defaults:")
    for key, value in experiment["changed"].items():
        print(f"  {key:<17}{value}")
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
    # Running a file does not put its folder on the import path, so the
    # folder is added, last: ``agent.py`` can import another file of the
    # harness, and none stands in for an installed module of the same name.
    sys.path.append(str(folder))
    agent = runpy.run_path(str(folder / "agent.py"))["Agent"]

    load_dotenv(REPO_ROOT / ".env")
    model_file = config_path("models", experiment["model"])
    model, pricing = load_model(model_file)
    written = yaml.safe_load(model_file.read_text())
    # How a self-hosted model's server is started, kept with the results.
    server = written.get("server")
    # An address taken from the environment is that of one's own account:
    # the results name the variable it came from and not the address.
    kept_out = (
        {"llm_api_endpoint": f"${written['llm_api_endpoint_env']}"}
        if "llm_api_endpoint_env" in written
        else {}
    )
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
    unanswered = 0
    for domain in experiment["domain"]:
        unanswered += evaluate(
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
            server=server,
            kept_out=kept_out,
        )
    if not experiment["out"]:
        pace = write_run_summary(run_dir, experiment["experiment"])["pace"]
        print(f"Run summary in {run_dir / 'summary.md'}")
        if pace:
            print(
                f"Pace by the clock: {pace['attempts_an_hour']:,} attempts an hour "
                f"and {pace['tokens_a_minute']:,} tokens a minute."
            )
    if trace:
        tracing.shutdown()
    if unanswered:
        raise SystemExit(
            f"error: {unanswered} attempts ended because a service they need "
            "did not answer (the model service or the domain's server), after "
            f"{experiment['service_wait']:g} seconds of waiting for it. They "
            "are marked service_error in tasks.csv and say nothing of the "
            "harness; run again when the service answers."
        )


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
    server: dict | None = None,
    kept_out: dict | None = None,
) -> int:
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
        server: The ``server`` block of a self-hosted model's file, recorded
            in ``evaluation.json`` as the file gives it; ``None`` for a
            hosted model.
        kept_out: What ``evaluation.json`` records in place of a model
            field's value, by field: for an address taken from the
            environment, the variable it came from.

    Returns:
        The number of attempts that ended because a service they need did
        not answer.

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
                seconds=experiment["attempt_timeout"],
                calls=experiment["attempt_calls"],
                retries=experiment["attempt_retries"],
                wait=experiment["service_wait"],
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
    kept = (
        "experiment",
        "experiment_file",
        "label",
        "overrides",
        "changed",
        "mode",
        "limit",
        "attempt_timeout",
        "attempt_calls",
        "attempt_retries",
        "service_wait",
    )
    settings = {key: experiment[key] for key in kept} | {
        "evaluation": name,
        "run": "" if experiment["out"] else run_dir.name,
        "split": experiment["split"],
        "harness": experiment["harness"],
        "model": {k: v for k, v in vars(model).items() if k != "llm_api_key"}
        | (kept_out or {}),
        "pricing": pricing,
        "server": server,
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
    unanswered = sum(r["service_error"] for r in outcomes)
    if unanswered:
        left = len(tasks) * experiment["attempts"] - len(outcomes)
        print(
            f"{unanswered} more attempts ended because a service they need "
            f"did not answer, and are counted nowhere above; {left} were not "
            "started after that."
        )
    return unanswered
