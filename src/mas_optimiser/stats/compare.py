"""Compare two sets of evaluations task by task: the ``mas-optimiser compare`` command.

    mas-optimiser compare --experiment baseline-haiku --a 2026-10-27-0528 --b 2026-11-03-0803
        [--split <name or file>] [--subset validation] [--outcome checks]
        [--alpha 0.05] [--out <folder>]

Each side is one evaluation per domain; ``--a`` is the current harness, ``--b``
the candidate. A side is named by evaluation folders, repeated once per
domain, by a run folder, or by a run name, a day (``20261027`` or
``2026-10-27``) or the start of a name within ``--experiment``, which the
experiment's ``index.csv`` resolves through each evaluation's start time. The command writes ``comparison.md`` (the gate's test, its
companions and the decision rules, pooled and per domain, for both outcomes),
``differences.csv`` (one row per task) and ``figures/`` (each figure as PDF
and PNG with its numbers as CSV) into ``--out``, by default
``comparisons/<timestamp>``.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``stats``.
"""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy import stats

from mas_optimiser.benchmarks.enterpriseops.runner import EVALUATIONS
from mas_optimiser.markdown import align_tables
from mas_optimiser.stats import budget, figures, gate
from mas_optimiser.stats.outcomes import OUTCOMES, Comparison, paired

DESCRIPTION = "compare two sets of evaluations task by task: the gate's test, its companions and the figures"
LABELS = {"checks": "Fraction of checks passed", "success": "Task success"}
# The random relabellings of the permutation test and the resamples of the
# bootstrap, and the seed each starts from, so that a report can be reproduced.
RESAMPLES = 9999
SEED = 0
COLUMNS = [
    "set",
    "tasks",
    "current",
    "candidate",
    "gain (points)",
    "SE (points)",
    "T",
    "X0",
    "p",
    "verdict",
    "detectable (points)",
    "sign test (up/down, p)",
    "permutation p",
    "bootstrap SE (points)",
    "lower bound (points)",
    "Bernstein bound (points)",
]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Define the command's options.

    Args:
        parser: The command's subparser.
    """
    parser.add_argument(
        "--experiment",
        default=None,
        help="the experiment whose index.csv resolves --a and --b given as names",
    )
    parser.add_argument(
        "--a",
        action="append",
        required=True,
        metavar="EVALUATION",
        help="the current harness: a run folder, evaluation folders repeated per "
        "domain, or a run, day or name within --experiment",
    )
    parser.add_argument(
        "--b",
        action="append",
        required=True,
        metavar="EVALUATION",
        help="the candidate: a run folder, evaluation folders repeated per "
        "domain, or a run, day or name within --experiment",
    )
    parser.add_argument(
        "--split",
        default=None,
        help="a split's name or file; without it, every task both sides scored",
    )
    parser.add_argument(
        "--subset",
        default="validation",
        choices=("training", "validation", "test"),
        help="the split's set to compare on",
    )
    parser.add_argument(
        "--outcome",
        default="checks",
        choices=OUTCOMES,
        help="the outcome the decision rules and figures use; both are tabled",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.05,
        help="the Type-I error level of the decision",
    )
    parser.add_argument(
        "--sigma",
        type=float,
        default=3.0,
        help="the fixed significance threshold shown beside it, in standard deviations",
    )
    parser.add_argument(
        "--decisions",
        type=int,
        default=20,
        help="the number of decisions the equal split shown beside it plans for",
    )
    parser.add_argument(
        "--round",
        dest="round_number",
        type=int,
        default=1,
        help="the round, for the REUSE level",
    )
    parser.add_argument(
        "--promotions",
        type=int,
        default=0,
        help="promotions before this round, for the REUSE level",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=1,
        help="candidates in this round, for the REUSE level",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="the folder to write; comparisons/<timestamp> by default",
    )


def evaluations(experiment: str | None, spec: str) -> list[Path]:
    """The evaluation folders one side names.

    ``spec`` is a run folder (every domain folder inside it is taken) or one
    evaluation folder, used as they are; otherwise it names evaluations in the
    experiment's ``index.csv``: a run name such as ``2026-11-03-0803``, a day
    (``2026-11-03`` or ``20261103``) matched against each evaluation's start
    time, or the start of an evaluation's name. Every match is taken, one per
    domain.

    Args:
        experiment: The experiment's name under ``evaluations/``, or its
            folder; needed when ``spec`` is not a folder.
        spec: An evaluation folder, or the start of evaluation names.

    Returns:
        The evaluation folders, one per domain.

    Raises:
        SystemExit: If a folder holds no evaluation, if ``spec`` is not a
            folder and no experiment is given, if nothing matches, or if a
            domain matches more than once.
    """
    path = Path(spec)
    if path.is_dir():
        if (path / "evaluation.json").exists():
            return [path]
        inside = sorted(p.parent for p in path.glob("*/evaluation.json"))
        if inside:
            return inside
        raise SystemExit(f"error: {path} holds no evaluation")
    if experiment is None:
        raise SystemExit(
            f"error: {spec} is not an evaluation folder; give --experiment to"
            " look it up by name"
        )
    folder = Path(experiment)
    if not folder.is_dir():
        folder = EVALUATIONS / experiment
    day = spec.replace("-", "")
    by_domain: dict[str, list[str]] = {}
    with open(folder / "index.csv", newline="") as f:
        for row in csv.DictReader(f):
            same_day = row["started"][:10].replace("-", "") == day
            if same_day or row["evaluation"].startswith(spec):
                by_domain.setdefault(row["domain"], []).append(row["evaluation"])
    if not by_domain:
        raise SystemExit(f"error: no evaluation of {folder.name} starts with {spec}")
    clashes = {d: names for d, names in by_domain.items() if len(names) > 1}
    if clashes:
        raise SystemExit(
            f"error: {spec} names more than one evaluation of a domain: {clashes}"
        )
    return [folder / names[0] for names in by_domain.values()]


def _name(evaluation: Path) -> str:
    """An evaluation's name within its experiment, ``<run>/<domain>`` where recorded.

    Args:
        evaluation: The evaluation's folder.

    Returns:
        The ``evaluation`` field of its ``evaluation.json``, or the folder's name.
    """
    record = json.loads((evaluation / "evaluation.json").read_text())
    return record.get("evaluation") or evaluation.name


def _row(name: str, cmp: Comparison, alpha: float) -> list[str]:
    """One table row: the tests of a comparison at one level.

    Args:
        name: The row's label, ``pooled`` or a domain.
        cmp: The comparison, or the part of it for the domain.
        alpha: The Type-I error level of the decision.

    Returns:
        The cells, formatted; ``n/a`` where a test cannot be computed.
    """
    d = cmp.d
    cells = [name, str(len(cmp.tasks)), f"{cmp.a.mean():.3f}", f"{cmp.b.mean():.3f}"]
    try:
        test = gate.mean_test(d, alpha)
    except ValueError:
        return cells + ["n/a"] * (len(COLUMNS) - len(cells))
    sign = gate.sign_test(d, alpha)
    # The two checks that do not assume the test's distribution: each
    # difference given a random sign, and the tasks resampled.
    permutation = stats.permutation_test(
        (d,),
        np.mean,
        permutation_type="samples",
        alternative="greater",
        n_resamples=RESAMPLES,
        rng=np.random.default_rng(SEED),
    )
    spread = stats.bootstrap(
        (d,),
        np.mean,
        n_resamples=RESAMPLES,
        method="percentile",
        rng=np.random.default_rng(SEED),
    )
    cells += [
        f"{test.mean * 100:+.2f}",
        f"{test.se * 100:.2f}",
        f"{test.t:+.2f}",
        f"{test.x0:.3f}",
        f"{test.p:.3f}",
        "accept" if test.accept else "reject",
        f"{gate.detectable(test.se, test.n, alpha) * 100:.1f}",
        f"{sign.n_plus}/{sign.n_minus}, {sign.p:.3f}",
        f"{permutation.pvalue:.3f}",
        f"{spread.standard_error * 100:.2f}",
        f"{test.lower * 100:+.2f}",
        f"{gate.bernstein_bound(d, alpha) * 100:+.2f}",
    ]
    return cells


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    """A markdown table.

    Args:
        header: The column names.
        rows: The rows, already formatted.

    Returns:
        The table's lines.
    """
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def run(args: argparse.Namespace) -> None:
    """Compare the two sides and write the tables and figures.

    Args:
        args: The parsed options, as ``add_arguments`` defines them.
    """
    out = args.out or Path("comparisons") / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    a = [p for spec in args.a for p in evaluations(args.experiment, spec)]
    b = [p for spec in args.b for p in evaluations(args.experiment, spec)]
    comparisons = {
        outcome: paired(a, b, outcome, args.split, args.subset) for outcome in OUTCOMES
    }
    primary = comparisons[args.outcome]
    names = (
        "candidate "
        + ", ".join(_name(p) for p in b)
        + " against current "
        + ", ".join(_name(p) for p in a)
    )
    where = (
        f"the {args.subset} tasks of split {args.split}"
        if args.split
        else "every task both sides scored"
    )
    lines = [
        f"# Comparison: {names}",
        "",
        f"Candidate (second) against current (first) on {where}: {len(primary.tasks)} "
        f"tasks. Scores are per-task means over attempts; a point is a hundredth. "
        f"Decisions at level {args.alpha:g}, one-sided.",
        "",
    ]
    domains = sorted(set(primary.domains))
    for outcome, cmp in comparisons.items():
        title = LABELS[outcome] + (
            " (the gate's outcome)" if outcome == args.outcome else ""
        )
        rows = [_row("pooled", cmp, args.alpha)]
        rows += [_row(domain, cmp.domain(domain), args.alpha) for domain in domains]
        lines += [f"## {title}", ""] + _table(COLUMNS, rows) + [""]

    levels = [
        (f"level as given ({args.alpha:g})", args.alpha),
        (f"fixed {args.sigma:g} sigma", budget.fixed_level(args.sigma)),
        (
            f"equal split over {args.decisions} decisions",
            budget.bonferroni_level(args.alpha, args.decisions),
        ),
        (
            f"REUSE allocation, round {args.round_number}, {args.promotions} promotions, "
            f"{args.candidates} candidates",
            budget.reuse_level(
                args.alpha, args.round_number, args.promotions, args.candidates
            ),
        ),
    ]
    rule_rows = []
    for label, level in levels:
        try:
            test = gate.mean_test(primary.d, level)
            rule_rows.append(
                [
                    label,
                    f"{level:.2g}",
                    f"{test.x0:.2f}",
                    "accept" if test.accept else "reject",
                    f"{gate.detectable(test.se, test.n, level) * 100:.1f}",
                ]
            )
        except ValueError:
            rule_rows.append([label, f"{level:.2g}", "n/a", "n/a", "n/a"])
    lines += [f"## Decision rules on the pooled {LABELS[args.outcome].lower()}", ""]
    lines += _table(
        ["rule", "level", "X0", "verdict", "detectable (points)"], rule_rows
    ) + [""]

    written = []
    try:
        test = gate.mean_test(primary.d, args.alpha)
        written.append(figures.threshold(test, out / "figures"))
        written.append(
            figures.power_curve(test.se, test.n, args.alpha, out / "figures")
        )
    except ValueError:
        pass
    written.append(figures.differences(primary, out / "figures"))
    written.append(figures.domains(primary, out / "figures"))
    lines += ["## Figures", ""] + [f"- `{p.relative_to(out)}`" for p in written] + [""]
    (out / "comparison.md").write_text(align_tables("\n".join(lines)))

    with open(out / "differences.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["task_id", "domain"]
            + [f"{side}_{outcome}" for outcome in OUTCOMES for side in ("a", "b", "d")]
        )
        for i, task in enumerate(primary.tasks):
            row = [task, primary.domains[i]]
            for outcome in OUTCOMES:
                cmp = comparisons[outcome]
                row += [f"{cmp.a[i]:.4f}", f"{cmp.b[i]:.4f}", f"{cmp.d[i]:+.4f}"]
            writer.writerow(row)
    print(out)
    cells = _row("pooled", primary, args.alpha)
    print(
        f"pooled {LABELS[args.outcome].lower()}: gain {cells[4]} points, "
        f"T {cells[6]}, p {cells[8]}, {cells[9]}"
    )
