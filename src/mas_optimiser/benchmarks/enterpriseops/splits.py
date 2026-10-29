"""Split the benchmark's tasks into training, validation and test sets.

The optimiser's proposer reads the traces of the training tasks, the
acceptance gate decides on the validation tasks and returns only its verdict,
and the test tasks are opened once for the reported number. One seeded,
stratified draw fixes the three sets for every arm, so every comparison is
paired on the same tasks.

The strata are domain crossed with the task's baseline outcome: whether all,
some or none of its attempts passed in the experiment the split is drawn
from (``runs/<experiment>/``). For each domain the draw reads the latest full
evaluation in the experiment's ``index.csv`` that has no label and no
command-line override that changes what is measured, and refuses if a domain
with a full evaluation has none that qualifies, or if the evaluations chosen
differ in model, mode, harness or attempts. Within each stratum the tasks named in the forced
list go into training first: tasks already inspected by hand, with dataset
defects, or otherwise unfit for a sealed set. The rest are shuffled by the
seed and filled into training, then validation, then test, in the proportions
given, rounded by largest remainder so that every task lands in exactly one
set. If the forced tasks exceed a stratum's training quota, training grows
and validation and test share what is left in their own ratio.

The draw is written to ``configs/splits/<name>.json`` with everything that
produced it: seed, proportions, the strata rule, the evaluations read, the
code version, the forced list and every task's stratum. A split is drawn
once and never redrawn: the command refuses a name that already has a file,
since redrawing could move tasks someone has read into a sealed set. The
file is the record, and the draw can be checked from it alone, since each
task's stratum and forced flag, the seed and the proportions determine the
assignment. Usage::

    mas-optimiser split --experiment baseline-haiku --name haiku-20-40-40 --seed 0
    mas-optimiser split --experiment baseline-haiku --name trial --proportions 0.6 0.2 0.2

``python -m mas_optimiser.benchmarks.enterpriseops.splits`` takes the same arguments.
"""

import argparse
import csv
import json
import math
import random
import re
from collections import defaultdict
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

import yaml

import mas_optimiser
from mas_optimiser.benchmarks.enterpriseops import REPO_ROOT

SETS = ("training", "validation", "test")
DEFAULT_PROPORTIONS = (0.2, 0.4, 0.4)
SPLITS_DIR = REPO_ROOT / "configs" / "splits"
FORCED_FILE = SPLITS_DIR / "forced-training.yaml"
RUNS = REPO_ROOT / "runs"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
# Command-line overrides that leave what an evaluation measures unchanged;
# any other override (model, mode, attempts, thinking) disqualifies it as a
# baseline, as does any override added later until it is listed here.
HARMLESS_OVERRIDES = frozenset({"domain", "concurrency", "trace", "out"})
INDEX_COLUMNS = (
    "evaluation",
    "started",
    "domain",
    "mode",
    "model",
    "harness",
    "label",
    "overrides",
    "limit",
    "attempts",
)
STRATA_RULE = "domain x outcome (all, some or none of the baseline attempts passed)"
DESCRIPTION = __doc__.split("\n\n")[0]


class Baseline(NamedTuple):
    """What the split is drawn from.

    Attributes:
        tasks: Each task id to its ``(domain, outcome)``, where outcome is
            ``all``, ``some`` or ``none``.
        evaluations: Each domain to the evaluation folder its outcomes came from.
    """

    tasks: dict[str, tuple[str, str]]
    evaluations: dict[str, str]


def started_at(text: str) -> datetime:
    """An evaluation's start time; one written without an offset is read as local time."""
    return datetime.fromisoformat(text).astimezone()


def read_baseline(experiment_dir: Path) -> Baseline:
    """Read each domain's baseline evaluation and classify every task's outcome.

    The baseline of a domain is its latest full evaluation (one run without a
    task limit) with no label and no command-line override that changes what
    is measured (``HARMLESS_OVERRIDES``), so a rerun with another model or a
    labelled trial never defines the strata by accident, while a domain rerun
    alone with ``--domain`` still counts.

    Args:
        experiment_dir: The ``runs/<experiment>`` folder holding ``index.csv``.

    Returns:
        The tasks and the evaluations they were read from.

    Raises:
        FileNotFoundError: If the experiment has no index.
        ValueError: If the index lacks a column the choice needs, a domain
            with a full evaluation has none that qualifies, or the chosen
            evaluations differ in model, mode, harness or attempts.
    """
    index = experiment_dir / "index.csv"
    with index.open(newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in INDEX_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{index} has no {', '.join(missing)} column")
        full = [row for row in reader if not row["limit"]]
    rows = [
        row
        for row in full
        if not row["label"]
        and set(re.findall(r"(?:^|\s)(\w+)=", row["overrides"])) <= HARMLESS_OVERRIDES
    ]
    latest = {}
    for row in sorted(rows, key=lambda row: started_at(row["started"])):
        latest[row["domain"]] = row
    unserved = sorted({row["domain"] for row in full} - set(latest))
    if unserved or not latest:
        raise ValueError(
            f"no full evaluation without a label or a measured override for "
            f"{', '.join(unserved) or 'any domain'} in {index}"
        )
    settings = {
        (row["model"], row["mode"], row["harness"], row["attempts"])
        for row in latest.values()
    }
    if len(settings) > 1:
        raise ValueError(
            "the latest full evaluations differ in model, mode, harness or "
            f"attempts: {sorted(settings)}"
        )
    evaluations = {domain: row["evaluation"] for domain, row in latest.items()}
    tasks = {}
    for domain, evaluation in evaluations.items():
        passed: dict[str, list[bool]] = defaultdict(list)
        with (experiment_dir / evaluation / "tasks.csv").open(newline="") as f:
            for row in csv.DictReader(f):
                passed[row["task_id"]].append(row["success"] == "True")
        for task_id, results in passed.items():
            outcome = "all" if all(results) else "none" if not any(results) else "some"
            tasks[task_id] = (domain, outcome)
    return Baseline(tasks, evaluations)


def quotas(n: int, proportions: tuple[float, ...]) -> list[int]:
    """Share ``n`` items in the given proportions, rounding by largest remainder.

    The shares sum to ``n`` exactly; ties in the remainders go to the
    earlier proportion. Each proportion is read as the decimal it is written
    as (0.1 is exactly one tenth), so floating-point error cannot break a tie.
    """
    exact = [n * Fraction(str(p)) for p in proportions]
    shares = [int(x) for x in exact]
    remainders = sorted(
        range(len(exact)), key=lambda i: (exact[i] - shares[i], -i), reverse=True
    )
    for i in remainders[: n - sum(shares)]:
        shares[i] += 1
    return shares


def draw(
    tasks: dict[str, tuple[str, str]],
    proportions: tuple[float, float, float],
    seed: int,
    forced: set[str],
) -> dict[str, str]:
    """Assign every task to a set, stratum by stratum.

    Args:
        tasks: Each task id to its ``(domain, outcome)`` stratum.
        proportions: The training, validation and test shares, summing to one.
        seed: Seeds one generator for the whole draw; strata are visited in
            sorted order, so the same seed always gives the same assignment.
        forced: Task ids that go into training before anything is drawn.

    Returns:
        Each task id to ``training``, ``validation`` or ``test``.
    """
    rng = random.Random(seed)
    strata: dict[tuple[str, str], list[str]] = defaultdict(list)
    for task_id in sorted(tasks):
        strata[tasks[task_id]].append(task_id)
    assignment = {}
    for stratum in sorted(strata):
        ids = strata[stratum]
        fixed = [task_id for task_id in ids if task_id in forced]
        free = [task_id for task_id in ids if task_id not in forced]
        rng.shuffle(free)
        training_quota, validation_quota, _ = quotas(len(ids), proportions)
        drawn = max(0, training_quota - len(fixed))
        training = fixed + free[:drawn]
        rest = free[drawn:]
        if len(fixed) > training_quota:
            validation, test = (Fraction(str(p)) for p in proportions[1:])
            validation_quota, _ = quotas(
                len(rest),
                (validation / (validation + test), test / (validation + test)),
            )
        for task_id in training:
            assignment[task_id] = "training"
        for task_id in rest[:validation_quota]:
            assignment[task_id] = "validation"
        for task_id in rest[validation_quota:]:
            assignment[task_id] = "test"
    return assignment


def build_split(
    name: str,
    experiment_dir: Path,
    proportions: tuple[float, float, float],
    seed: int,
    forced: dict[str, str],
) -> dict:
    """Draw a split and describe it fully.

    Args:
        name: The split's name, also its file name under ``configs/splits/``.
        experiment_dir: The ``runs/<experiment>`` folder the outcomes come from.
        proportions: The training, validation and test shares.
        seed: The draw's seed.
        forced: Task ids forced into training, each with the reason.

    Returns:
        The split document: provenance, the forced list, counts per set and
        stratum, and every task's domain, outcome, set and whether it was forced.

    Raises:
        ValueError: If the proportions are not three shares between 0 and 1
            summing to one, a forced task is not in the experiment, or
            ``read_baseline`` refuses the experiment.
    """
    if (
        len(proportions) != 3
        or not all(math.isfinite(p) and 0 <= p <= 1 for p in proportions)
        or not math.isclose(sum(proportions), 1, abs_tol=1e-9)
    ):
        raise ValueError(
            "proportions must be three shares between 0 and 1 summing to one: "
            f"{proportions}"
        )
    baseline = read_baseline(experiment_dir)
    unknown = sorted(set(forced) - set(baseline.tasks))
    if unknown:
        raise ValueError(f"forced tasks not in {experiment_dir}: {unknown}")
    assignment = draw(baseline.tasks, proportions, seed, set(forced))
    counts: dict[str, dict[str, int]] = {
        set_name: defaultdict(int) for set_name in SETS
    }
    tasks = {}
    for task_id in sorted(baseline.tasks):
        domain, outcome = baseline.tasks[task_id]
        counts[assignment[task_id]][f"{domain}/{outcome}"] += 1
        tasks[task_id] = {
            "domain": domain,
            "outcome": outcome,
            "set": assignment[task_id],
            "forced": task_id in forced,
        }
    return {
        "name": name,
        "seed": seed,
        "proportions": dict(zip(SETS, proportions, strict=True)),
        "strata": STRATA_RULE,
        "source": {
            "experiment": experiment_dir.name,
            "evaluations": baseline.evaluations,
            "code_version": mas_optimiser.code_version(),
        },
        "forced_training": dict(sorted(forced.items())),
        "counts": {
            set_name: dict(sorted(counts[set_name].items())) for set_name in SETS
        },
        "tasks": tasks,
    }


def load_split(spec: str | Path) -> dict:
    """Read a split by name (``configs/splits/<name>.json``) or by path."""
    path = Path(spec)
    if path.suffix != ".json":
        path = SPLITS_DIR / f"{spec}.json"
    return json.loads(path.read_text())


def summary(split: dict) -> str:
    """One line per set with its size, then the counts per stratum."""
    lines = []
    for set_name in SETS:
        counts = split["counts"][set_name]
        strata = ", ".join(f"{stratum} {n}" for stratum, n in counts.items())
        n = sum(counts.values())
        lines.append(f"{set_name}: {n} task{'s' if n != 1 else ''} ({strata})")
    return "\n".join(lines)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the split options to ``parser``; shared with the ``mas-optimiser`` command."""
    parser.add_argument(
        "--experiment", required=True, help="the runs/<experiment> folder to draw from"
    )
    parser.add_argument("--name", required=True, help="the split's name and file name")
    parser.add_argument("--seed", type=int, default=0, help="seed of the draw")
    parser.add_argument(
        "--proportions",
        type=float,
        nargs=3,
        default=DEFAULT_PROPORTIONS,
        metavar=("TRAINING", "VALIDATION", "TEST"),
        help="shares of each set, summing to one",
    )
    parser.add_argument(
        "--forced",
        type=Path,
        help="YAML of task id to reason, forced into training "
        "(default: configs/splits/forced-training.yaml)",
    )


def run(args: argparse.Namespace) -> None:
    """Draw the split named by the parsed arguments and write it.

    Raises:
        SystemExit: For a malformed name, a name that already has a split, a
            missing or malformed forced list, or an experiment the draw refuses.
    """
    if not NAME.fullmatch(args.name) or args.name.endswith(".json"):
        raise SystemExit(
            f"error: split name {args.name!r} must be letters, digits, '.', '_' "
            "or '-', without .json"
        )
    path = SPLITS_DIR / f"{args.name}.json"
    if path.exists():
        raise SystemExit(
            f"error: {path} exists; a split is never redrawn, so give a new one "
            "another name"
        )
    forced_file = args.forced or FORCED_FILE
    if not forced_file.is_file():
        raise SystemExit(f"error: no forced-training file at {forced_file}")
    try:
        forced = yaml.safe_load(forced_file.read_text()) or {}
    except yaml.YAMLError as error:
        raise SystemExit(f"error: {forced_file} is not valid YAML ({error})") from error
    if not isinstance(forced, dict) or not all(
        isinstance(task_id, str) and isinstance(reason, str)
        for task_id, reason in forced.items()
    ):
        raise SystemExit(
            f"error: {forced_file} must map each task id to the reason it is "
            "forced into training"
        )
    try:
        split = build_split(
            args.name,
            RUNS / args.experiment,
            tuple(args.proportions),
            args.seed,
            forced,
        )
    except (ValueError, FileNotFoundError) as error:
        raise SystemExit(f"error: {error}") from error
    SPLITS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(split, indent=2) + "\n")
    print(f"wrote {path}")
    print(summary(split))


def main() -> None:
    """Command-line entry point; see the module docstring for usage."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    add_arguments(parser)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
