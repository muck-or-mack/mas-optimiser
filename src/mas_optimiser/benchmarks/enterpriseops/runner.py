"""Run EnterpriseOps-Gym tasks through the benchmark's executor and score them.

The runner loads one domain's tasks, runs each with the benchmark's ReAct
agent against the Docker domain servers, and writes one evaluation folder
under ``runs/<experiment>/``: ``tasks.csv`` with a row per task run,
``run.json`` with the settings and totals, ``run.log``, and
``tasks/<task_id>/repeat-<n>.json`` with the executor's full record of every
model message, tool call, tool result and verifier verdict. Each experiment
folder keeps an ``index.csv`` with one row per evaluation, so the evaluations
of one experiment can be compared without opening them.

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

Scoring is otherwise the executor's own, including one quirk kept for now: it
keys verifier results by name (``benchmark/executor.py:420`` and ``:474``), so
where a task gives the same name to different verifiers only the last decides
whether the task passes.

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
from mas_optimiser.benchmarks.enterpriseops import GYM_ROOT

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

REPO_ROOT = GYM_ROOT.parents[1]
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
    "label": None,
    "out": None,
    "trace": True,
    "thinking_summary": False,
}
# Command-line overrides of these keys change what an evaluation measures, so
# they go into its folder name; the rest (tracing, output folder) do not.
NAMED_OVERRIDES = ("model", "mode")

# Asks the model for a readable summary of its reasoning on every call, which
# the trace then shows. Thinking happens either way; only its visibility
# changes, at some cost in output tokens.
THINKING_SUMMARY = {"type": "adaptive", "display": "summarized"}


class TracedExecutor(BenchmarkExecutor):
    """The benchmark's executor with tracing attached to the model it builds.

    The executor constructs its model client inside ``initialize()``, which
    ``execute_benchmark()`` calls itself, so this is the first point at which
    the model object exists and the callback can be set on it.

    Args:
        trace: Attach the Langfuse callback to the model.
        thinking: A ``thinking`` setting to put on the model, or ``None`` to
            leave the benchmark's default. Changes the agent's behaviour and
            cost, so it stays ``None`` for any run that produces a reported
            number.
    """

    def __init__(self, *args, trace: bool, thinking: dict | None, **kwargs):
        super().__init__(*args, **kwargs)
        self.trace = trace
        self.thinking = thinking

    async def initialize(self):
        await super().initialize()
        model = self.llm_client.llm
        if self.trace:
            tracing.attach(model)
        if self.thinking:
            model.thinking = self.thinking


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
    different model or mode, and ``limitN`` when only the first N tasks ran.
    """
    parts = [f"{started:%Y%m%d-%H%M%S}", domain]
    if settings["label"]:
        parts.append(str(settings["label"]))
    for key in NAMED_OVERRIDES:
        if key in settings["overrides"]:
            parts.append(f"{key}={Path(str(settings[key])).stem}")
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


def load_model(path: Path) -> LLMConfig:
    """Build the benchmark's model config from a YAML file and the environment.

    Args:
        path: YAML file of ``LLMConfig`` fields plus ``api_key_env``, the name
            of the environment variable that holds the API key.

    Returns:
        The config, with the key filled in from the environment.

    Raises:
        RuntimeError: If that environment variable is unset or empty.
    """
    fields = yaml.safe_load(path.read_text())
    key_name = fields.pop("api_key_env")
    key = os.environ.get(key_name)
    if not key:
        raise RuntimeError(f"{key_name} is not set; add it to .env (see .env.example)")
    return LLMConfig(llm_api_key=key, **fields)


def score(task_id: str, config: dict, result: dict) -> list[dict]:
    """Turn the executor's result for one task into one record per run.

    A run that raised has no verifier results, so it counts as a failed task
    with none of its verifiers passed, rather than being dropped.

    Args:
        task_id: The task's dataset ID.
        config: The task config the run used.
        result: What ``BenchmarkExecutor.execute_benchmark`` returned, or a
            stand-in holding the error if it raised before any run.

    Returns:
        One flat record per run. Token counts cover the agent's model calls;
        the executor keeps none for a run that raised, so those read zero.
    """
    # Verifiers that share a name count once, as in the executor's summary;
    # a run that raised is counted the same way.
    distinct = len({v["name"] for v in config["verifiers"]})
    records = []
    for run in result["runs"]:
        usage = [
            step["usage_metadata"]
            for step in run.get("conversation_flow", [])
            if step["type"] == "ai_message" and step.get("usage_metadata")
        ]
        summary = run.get("verification_summary", {})
        records.append(
            {
                "task_id": task_id,
                "run": run["run_number"],
                "success": bool(run.get("overall_success")),
                "verifiers_passed": summary.get("passed", 0),
                "verifiers_total": summary.get("total", distinct),
                "error": run.get("error", ""),
                "model_calls": len(usage),
                "input_tokens": sum(u["input_tokens"] for u in usage),
                "output_tokens": sum(u["output_tokens"] for u in usage),
                "seconds": run.get("execution_time_ms", 0) / 1000,
            }
        )
    return records


async def run_tasks(
    tasks: dict[str, dict],
    model: LLMConfig,
    out: Path,
    *,
    trace: bool,
    thinking: dict | None,
    tags: list[str],
) -> list[dict]:
    """Run each task with the benchmark's ReAct agent and score it.

    Args:
        tasks: Task configs keyed by task ID, as from ``load_tasks``.
        model: The agent's model config.
        out: Run folder; each task's executor result is saved to ``out/tasks``.
        trace: Send each task to Langfuse as one trace, scored with its verdict.
        thinking: Model ``thinking`` setting, or ``None`` for the default.
        tags: Labels put on every trace, such as the domain and model.

    Returns:
        The records from ``score`` for every task, in order.
    """
    # TODO: run tasks in parallel with Ray before the full-split runs.
    records = []
    for i, (task_id, config) in enumerate(tasks.items(), start=1):
        executor = TracedExecutor(
            BenchmarkConfig(**config),
            model,
            orchestrator_class=ReactOrchestrator,
            trace=trace,
            thinking=thinking,
        )
        # The evaluation's experiment and folder name group the traces as one session.
        span_context = (
            tracing.task_trace(
                task_id,
                session_id=f"{out.parent.name}/{out.name}",
                tags=tags,
                metadata={"task_id": task_id, "dataset_revision": HF_REVISION},
            )
            if trace
            else nullcontext()
        )
        with span_context as span:
            try:
                result = await executor.execute_benchmark()
            except Exception as exc:
                # Raised before any run starts, e.g. a server that is not up.
                result = {"runs": [{"run_number": 1, "error": repr(exc)}]}
            task_records = score(task_id, config, result)
            if span is not None:
                # One run per task for now, so the first record is the run.
                r = task_records[0]
                fraction = (
                    r["verifiers_passed"] / r["verifiers_total"]
                    if r["verifiers_total"]
                    else 0.0
                )
                span.score_trace(
                    name="task_success", value=float(r["success"]), data_type="BOOLEAN"
                )
                span.score_trace(
                    name="verifier_fraction", value=fraction, data_type="NUMERIC"
                )
                span.update(
                    output={
                        k: r[k]
                        for k in (
                            "success",
                            "verifiers_passed",
                            "verifiers_total",
                            "error",
                        )
                    }
                )
        folder = out / "tasks" / task_id
        folder.mkdir(exist_ok=True)
        (folder / "repeat-1.json").write_text(json.dumps(result, indent=2, default=str))
        for r in task_records:
            records.append(r)
            outcome = "error" if r["error"] else "pass" if r["success"] else "fail"
            print(
                f"[{i}/{len(tasks)}] {task_id} {outcome}: verifiers "
                f"{r['verifiers_passed']}/{r['verifiers_total']}, "
                f"{r['model_calls']} calls, {r['input_tokens']:,} in / "
                f"{r['output_tokens']:,} out tokens, {r['seconds']:.0f} s"
            )
    return records


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
    model = load_model(config_path("models", experiment["model"]))
    trace = tracing.configured() and experiment["trace"]
    thinking = THINKING_SUMMARY if experiment["thinking_summary"] else None
    if trace:
        # The Anthropic SDK also writes a metadata-only span per call into any
        # registered tracer; the LangChain callback already records each call
        # with its content, so those duplicates are switched off.
        os.environ.setdefault("ANTHROPIC_OPEN_TELEMETRY", "false")
    for domain in experiment["domain"]:
        evaluate(experiment, domain, model, trace=trace, thinking=thinking)
    if trace:
        tracing.shutdown()


def evaluate(
    experiment: dict,
    domain: str,
    model: LLMConfig,
    *,
    trace: bool,
    thinking: dict | None,
) -> None:
    """Run one domain's tasks as one evaluation and write its folder and index row.

    Args:
        experiment: Settings from ``load_experiment``.
        domain: The domain to run; one of ``experiment["domain"]``.
        model: The agent's model config.
        trace: Send each task to Langfuse.
        thinking: Model ``thinking`` setting, or ``None`` for the default.
    """
    tasks = load_tasks(domain, experiment["mode"])
    tasks = dict(list(tasks.items())[: experiment["limit"]])
    check_servers(
        {
            s["mcp_server_url"]
            for config in tasks.values()
            for s in config["gym_servers_config"]
        }
    )
    started = datetime.now()
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
        records = asyncio.run(
            run_tasks(tasks, model, out, trace=trace, thinking=thinking, tags=tags)
        )
    finally:
        root.removeHandler(handler)
        handler.close()

    with (out / "tasks.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    totals = {
        "runs": len(records),
        "successes": sum(r["success"] for r in records),
        "errors": sum(bool(r["error"]) for r in records),
        "verifiers_passed": sum(r["verifiers_passed"] for r in records),
        "verifiers_total": sum(r["verifiers_total"] for r in records),
        "input_tokens": sum(r["input_tokens"] for r in records),
        "output_tokens": sum(r["output_tokens"] for r in records),
    }
    settings = {
        "experiment": experiment["experiment"],
        "experiment_file": experiment["experiment_file"],
        "evaluation": out.name,
        "label": experiment["label"],
        "overrides": experiment["overrides"],
        "harness": "default",
        "model": {k: v for k, v in vars(model).items() if k != "llm_api_key"},
        "domain": domain,
        "mode": experiment["mode"],
        "limit": experiment["limit"],
        "tasks": list(tasks),
        "dataset": HF_DATASET,
        "dataset_revision": HF_REVISION,
        "thinking": thinking,
        "traced": trace,
        "mas_optimiser_version": mas_optimiser.__version__,
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().isoformat(timespec="seconds"),
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
            "tasks": len(tasks),
            "code_version": mas_optimiser.__version__,
        }
        | totals,
    )

    print(
        f"\n{totals['successes']}/{totals['runs']} tasks passed "
        f"({totals['errors']} errors, counted as failures); verifiers "
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
