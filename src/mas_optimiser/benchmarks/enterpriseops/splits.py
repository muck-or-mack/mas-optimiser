"""Split the benchmark's tasks into training, validation and test sets.

The optimiser's proposer reads the traces of the training tasks, the
acceptance gate decides on the validation tasks and returns only its verdict,
and the test tasks are opened once for the reported number. One seeded draw
fixes the three sets for every arm, so every comparison is paired on the
same tasks.

The draw shuffles the tasks with the seed and fills training, then
validation, then test in the proportions given, rounded by largest remainder
so that every task lands in exactly one set. Two options shape it.
``--balance`` names what every set should hold the same mix of: ``domain``,
``difficulty`` (whether all, some or none of a task's attempts passed in a
baseline run), both, or neither. Left out, it is ``domain``, and
``difficulty`` as well once ``--experiment`` names a baseline run to read it
from. The tasks are grouped by the values named,
shuffled within each group, and each group is shared out in the proportions;
with nothing named there is one group, a plain shuffle. ``--seen`` names a
file of tasks that have already been read by hand. They go into training
before the shuffle, since a task someone has studied cannot serve as an
unseen one. If they exceed a group's training share, training grows and
validation and test share what is left in their own ratio.

Without a balance on difficulty the task list comes from the dataset and no
run is needed. A balance on difficulty reads a baseline run
(``evaluations/<experiment>/``): for each domain the latest full evaluation
in the experiment's ``index.csv`` that has no label and no command-line
override that changes what is measured. It refuses if a domain with a full
evaluation has none that qualifies, or if the evaluations chosen differ in
model, mode, harness or attempts, and the split covers the tasks that run
scored.

An experiment of the loop on fewer tasks (its ``limit``) takes them from a
split by the same rule, in ``limited``: each set gives its share of the
number and each domain within a set its share, by a shuffle with the split's
seed, so the trial is a small copy of the split.

The draw is written to ``configs/splits/<name>.json``: the seed, the
proportions, the balance, what was read, the tasks placed in training by
hand and the three lists of task ids. A task's difficulty is not written, so
the file holds no result of any single task. A split is drawn once and never
redrawn: the command refuses a name that already has a file, since redrawing
could move tasks someone has read into a sealed set. Usage::

    mas-optimiser split --name by-domain
    mas-optimiser split --name trial --proportions 0.6 0.2 0.2 --seed 7 --balance
    mas-optimiser split --name by-difficulty --experiment baseline-haiku

Sources: ``docs/how-it-works.md``, section 6, the rows for ``splits``.
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
# The sets harnesses are judged on. What goes wrong on their tasks is not
# listed for reading, so that no change to a harness is made in its light.
HELD_OUT = ("validation", "test")
#: The default shares of training, validation and test. With one attempt a
#: task they let the gate find a gain of 6 percentage points at an
#: experiment's first decision and give the final gain a 95% confidence
#: interval of about 5 points either way (``docs/methodology/sizing.md``).
DEFAULT_PROPORTIONS = (0.17, 0.52, 0.31)
# What a draw can balance, in the order a task's values are held.
BALANCE_KEYS = ("domain", "difficulty")
SPLITS_DIR = REPO_ROOT / "configs" / "splits"
EVALUATIONS = REPO_ROOT / "evaluations"
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
DESCRIPTION = __doc__.split("\n\n")[0]


class Baseline(NamedTuple):
    """The run a balance on difficulty reads.

    Attributes:
        tasks: Each task id to its ``(domain, difficulty)``, where difficulty
            is ``all``, ``some`` or ``none``: how many of the task's attempts
            passed.
        evaluations: Each domain to the evaluation folder its attempts came from.
    """

    tasks: dict[str, tuple[str, str]]
    evaluations: dict[str, str]


def read_baseline(experiment_dir: Path) -> Baseline:
    """Read each domain's baseline evaluation and classify every task's difficulty.

    The baseline of a domain is its latest full evaluation (one without a
    task limit) with no label and no command-line override that changes what
    is measured (``HARMLESS_OVERRIDES``), so an evaluation with another model
    or a labelled trial never defines the difficulty by accident, while one
    repeated for a domain alone with ``--domain`` still counts.

    Args:
        experiment_dir: The ``evaluations/<experiment>`` folder holding ``index.csv``;
            opening it raises ``FileNotFoundError`` when there is none.

    Returns:
        The tasks and the evaluations they were read from.

    Raises:
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
    # A start time written without an offset is read as local time.
    latest = {}
    for row in sorted(
        rows, key=lambda row: datetime.fromisoformat(row["started"]).astimezone()
    ):
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
            difficulty = (
                "all" if all(results) else "none" if not any(results) else "some"
            )
            tasks[task_id] = (domain, difficulty)
    return Baseline(tasks, evaluations)


def quotas(n: int, proportions: tuple[float | Fraction, ...]) -> list[int]:
    """Share ``n`` items in the given proportions, rounding by largest remainder.

    The shares sum to ``n`` exactly; ties in the remainders go to the
    earlier proportion. Each proportion is read as the decimal it is written
    as (0.1 is exactly one tenth), or as the fraction it is, so
    floating-point error cannot break a tie.

    Args:
        n: The number of items.
        proportions: The shares, summing to one.

    Returns:
        One share per proportion.
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
    groups: dict[str, tuple[str, ...]],
    proportions: tuple[float, float, float],
    seed: int,
    seen: set[str],
) -> dict[str, str]:
    """Assign every task to a set, group by group.

    Args:
        groups: Each task id to its group, the values the draw balances: for
            example ``("itsm",)`` when only the domain is balanced, and
            ``()`` for every task when nothing is, which makes one group.
        proportions: The training, validation and test shares, summing to one.
        seed: Seeds one generator for the whole draw; groups are visited in
            sorted order, so the same seed always gives the same assignment.
        seen: Task ids that go into training before anything is drawn.

    Returns:
        Each task id to ``training``, ``validation`` or ``test``.
    """
    rng = random.Random(seed)
    members: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for task_id in sorted(groups):
        members[groups[task_id]].append(task_id)
    assignment = {}
    for group in sorted(members):
        ids = members[group]
        fixed = [task_id for task_id in ids if task_id in seen]
        free = [task_id for task_id in ids if task_id not in seen]
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


def limited(
    sets: dict[str, list[str]],
    groups: dict[str, tuple[str, ...]],
    tasks: int,
    seed: int,
) -> dict[str, list[str]]:
    """A split's sets cut down to a number of tasks in all, shared out as the draw shares.

    Each set gives its share of the number, in proportion to its size, and
    within a set each group its share, both rounded by largest remainder.
    Which tasks of a group are taken is settled by a shuffle with the seed.
    A limited split is so a small copy of the whole one, with the same
    proportions between its sets and the same mix of groups in each; the
    first tasks of each list would be whatever the order of the lists put
    first.

    Args:
        sets: Each set's task ids, as a split holds them.
        groups: Each task id to its group, as in ``draw``.
        tasks: How many tasks to keep in all; every task is kept when the
            sets hold no more than that.
        seed: Seeds the one generator of the shuffles. With the split's own
            seed, a limit of a split is always the same tasks.

    Returns:
        Each set's tasks that are kept, sorted.
    """
    rng = random.Random(seed)
    total = sum(len(ids) for ids in sets.values())
    if tasks >= total:
        return {name: sorted(ids) for name, ids in sets.items()}
    shares = quotas(tasks, tuple(Fraction(len(ids), total) for ids in sets.values()))
    kept = {}
    for (name, ids), share in zip(sets.items(), shares, strict=True):
        members: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for task_id in sorted(ids):
            members[groups[task_id]].append(task_id)
        order = sorted(members)
        taken = quotas(
            share, tuple(Fraction(len(members[group]), len(ids)) for group in order)
        )
        kept[name] = []
        for group, number in zip(order, taken, strict=True):
            rng.shuffle(members[group])
            kept[name] += members[group][:number]
        kept[name].sort()
    return kept


def build_split(
    name: str,
    groups: dict[str, tuple[str, ...]],
    proportions: tuple[float, float, float],
    seed: int,
    balance: tuple[str, ...],
    seen: set[str],
    source: dict,
) -> dict:
    """Draw a split and describe it.

    Args:
        name: The split's name, also its file name under ``configs/splits/``.
        groups: Each task id to its group, the task's values of the keys in
            ``balance``.
        proportions: The training, validation and test shares.
        seed: The draw's seed.
        balance: What every set holds the same mix of, from ``BALANCE_KEYS``.
        seen: Task ids already read by hand, placed in training.
        source: What the tasks were read from: the dataset, or the baseline run.

    Returns:
        The split document: the seed, the proportions, the balance, the
        source with the code version, the seen tasks and each set's task ids.
        It holds no task's difficulty.

    Raises:
        ValueError: If the proportions are not three shares between 0 and 1
            summing to one, or a seen task is not among the tasks.
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
    unknown = sorted(seen - set(groups))
    if unknown:
        raise ValueError(f"seen tasks that are not among the tasks: {unknown}")
    assignment = draw(groups, proportions, seed, seen)
    return {
        "name": name,
        "seed": seed,
        "proportions": dict(zip(SETS, proportions, strict=True)),
        "balance": list(balance),
        "source": source | {"code_version": mas_optimiser.code_version()},
        "seen": sorted(seen),
    } | {
        set_name: sorted(
            task_id for task_id, drawn in assignment.items() if drawn == set_name
        )
        for set_name in SETS
    }


def load_split(spec: str | Path) -> dict:
    """Read a split by name (``configs/splits/<name>.json``) or by path.

    Args:
        spec: The split's name, or the path of its file.

    Returns:
        The split document as ``build_split`` wrote it.
    """
    path = Path(spec)
    if path.suffix != ".json":
        path = SPLITS_DIR / f"{spec}.json"
    return json.loads(path.read_text())


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the split options to ``parser``; shared with the ``mas-optimiser`` command.

    Args:
        parser: The ``split`` subparser.
    """
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
        "--balance",
        nargs="*",
        choices=BALANCE_KEYS,
        help="what every set holds the same mix of: domain, difficulty, both, or "
        "nothing after the option for a plain shuffle (default: domain, and "
        "difficulty too when --experiment names a baseline run)",
    )
    parser.add_argument(
        "--experiment",
        help="the evaluations/<experiment> folder whose baseline run gives each "
        "task's difficulty; a balance on difficulty needs it",
    )
    parser.add_argument(
        "--seen",
        type=Path,
        help="YAML of task id to reason: tasks already read by hand, placed in "
        "training before the shuffle",
    )


def run(args: argparse.Namespace) -> None:
    """Draw the split named by the parsed arguments and write it.

    Args:
        args: The parsed arguments.

    Raises:
        SystemExit: For a malformed name, a name that already has a split, an
            experiment given with a balance that leaves difficulty out or
            left out of one that names it, a missing or malformed file of
            seen tasks, or a draw that is
            refused.
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
    # With no balance given the sets are balanced on the domain, and on
    # difficulty as well once a baseline run is named to read it from.
    chosen = args.balance
    if chosen is None:
        chosen = BALANCE_KEYS if args.experiment else ("domain",)
    balance = tuple(key for key in BALANCE_KEYS if key in chosen)
    if ("difficulty" in balance) != bool(args.experiment):
        raise SystemExit(
            "error: --experiment names the baseline run that gives each task's "
            "difficulty; a balance on difficulty needs it, and a balance given "
            "without difficulty has no use for it"
        )
    seen = {}
    if args.seen:
        if not args.seen.is_file():
            raise SystemExit(f"error: no file of seen tasks at {args.seen}")
        try:
            seen = yaml.safe_load(args.seen.read_text()) or {}
        except yaml.YAMLError as error:
            raise SystemExit(
                f"error: {args.seen} is not valid YAML ({error})"
            ) from error
        if not isinstance(seen, dict) or not all(
            isinstance(task_id, str) and isinstance(reason, str)
            for task_id, reason in seen.items()
        ):
            raise SystemExit(
                f"error: {args.seen} must map each task id to the reason it counts "
                "as seen"
            )
    try:
        described: dict[str, tuple[str, str | None]]
        if "difficulty" in balance:
            baseline = read_baseline(EVALUATIONS / args.experiment)
            described = baseline.tasks
            source = {
                "experiment": args.experiment,
                "evaluations": baseline.evaluations,
            }
        else:
            # Imported here because the runner imports this module.
            from mas_optimiser.benchmarks.enterpriseops import runner

            described = {
                task_id: (domain, None)
                for domain in runner.DOMAINS
                for task_id in runner.load_tasks(domain, "oracle")
            }
            source = {"dataset": runner.HF_DATASET, "revision": runner.HF_REVISION}
        groups = {
            task_id: tuple(
                value
                for key, value in zip(BALANCE_KEYS, values, strict=True)
                if key in balance
            )
            for task_id, values in described.items()
        }
        split = build_split(
            args.name,
            groups,
            tuple(args.proportions),
            args.seed,
            balance,
            set(seen),
            source,
        )
    except (ValueError, FileNotFoundError) as error:
        raise SystemExit(f"error: {error}") from error
    SPLITS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(split, indent=2) + "\n")
    print(f"wrote {path}")
    for set_name in SETS:
        n = len(split[set_name])
        print(f"{set_name}: {n} task{'s' if n != 1 else ''}")
