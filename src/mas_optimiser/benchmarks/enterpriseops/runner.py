"""Run EnterpriseOps-Gym tasks with the benchmark's own executor and agent.

A thin layer over ``benchmark.executor.BenchmarkExecutor`` that differs from
the benchmark's ``evaluate.py`` in three ways:

* tasks come from the Hugging Face dataset at a pinned revision, and in oracle
  mode the corrected copies in the submodule's ``data/revised`` replace the
  dataset's versions of the same tasks;
* the model's API key is read from the environment (``.env``), never from a
  config file;
* a task whose run raises is scored as a failure and kept, where
  ``evaluate.py`` retries it and ``compute_score.py`` then leaves it out of
  the averages. Errors are counted separately.

Scoring is otherwise the executor's own, including one quirk kept for now: it
keys verifier results by name (``benchmark/executor.py:420-474``), so where a
task gives the same name to different verifiers, only the last of them decides
whether the task passes.

Usage::

    uv run python -m mas_optimiser.benchmarks.enterpriseops.runner \\
        --model configs/models/haiku-5-5.yaml --domain itsm --limit 5
"""

import argparse
import asyncio
import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import yaml
from datasets import load_dataset
from dotenv import load_dotenv

import mas_optimiser
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


async def run_tasks(tasks: dict[str, dict], model: LLMConfig, out: Path) -> list[dict]:
    """Run each task with the benchmark's ReAct agent and score it.

    Args:
        tasks: Task configs keyed by task ID, as from ``load_tasks``.
        model: The agent's model config.
        out: Run folder; each task's executor result is saved to ``out/tasks``.

    Returns:
        The records from ``score`` for every task, in order.
    """
    # TODO: run tasks in parallel with Ray before the full-split runs.
    records = []
    for i, (task_id, config) in enumerate(tasks.items(), start=1):
        executor = BenchmarkExecutor(
            BenchmarkConfig(**config), model, orchestrator_class=ReactOrchestrator
        )
        try:
            result = await executor.execute_benchmark()
        except Exception as exc:
            # Raised before any run starts, e.g. a server that is not up.
            result = {"runs": [{"run_number": 1, "error": repr(exc)}]}
        (out / "tasks" / f"{task_id}.json").write_text(
            json.dumps(result, indent=2, default=str)
        )
        for r in score(task_id, config, result):
            records.append(r)
            outcome = "error" if r["error"] else "pass" if r["success"] else "fail"
            print(
                f"[{i}/{len(tasks)}] {task_id} {outcome}: verifiers "
                f"{r['verifiers_passed']}/{r['verifiers_total']}, "
                f"{r['model_calls']} calls, {r['input_tokens']:,} in / "
                f"{r['output_tokens']:,} out tokens, {r['seconds']:.0f} s"
            )
    return records


def main() -> None:
    """Command-line entry point; see the module docstring for usage."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", type=Path, required=True, help="model YAML file")
    parser.add_argument("--domain", required=True, choices=["itsm", "csm", "hr"])
    parser.add_argument(
        "--mode",
        default="oracle",
        choices=["oracle", "plus_5_tools", "plus_10_tools", "plus_15_tools"],
    )
    parser.add_argument("--limit", type=int, help="run only the first N tasks")
    parser.add_argument("--out", type=Path, help="output folder (default: in runs/)")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    load_dotenv(REPO_ROOT / ".env")
    model = load_model(args.model)
    started = datetime.now()
    out = args.out or REPO_ROOT / "runs" / (
        f"{started:%Y%m%d-%H%M%S}-{args.domain}-{args.mode}-{args.model.stem}"
    )
    (out / "tasks").mkdir(parents=True)
    # The benchmark logs every request and tool call at INFO; keep that in the
    # run folder rather than on the terminal.
    logging.basicConfig(
        filename=out / "run.log",
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    tasks = load_tasks(args.domain, args.mode)
    tasks = dict(list(tasks.items())[: args.limit])
    records = asyncio.run(run_tasks(tasks, model, out))

    with (out / "results.csv").open("w", newline="") as f:
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
        "model": {k: v for k, v in vars(model).items() if k != "llm_api_key"},
        "domain": args.domain,
        "mode": args.mode,
        "tasks": list(tasks),
        "dataset": HF_DATASET,
        "dataset_revision": HF_REVISION,
        "mas_optimiser_version": mas_optimiser.__version__,
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().isoformat(timespec="seconds"),
    }
    (out / "run.json").write_text(json.dumps(settings | {"totals": totals}, indent=2))

    print(
        f"\n{totals['successes']}/{totals['runs']} tasks passed "
        f"({totals['errors']} errors, counted as failures); verifiers "
        f"{totals['verifiers_passed']}/{totals['verifiers_total']}; "
        f"{totals['input_tokens']:,} input and {totals['output_tokens']:,} output "
        f"tokens. Results in {out}"
    )


if __name__ == "__main__":
    main()
