"""Check the gate's test on two runs of one harness: the ``mas-optimiser calibrate`` command.

    mas-optimiser calibrate --experiment baseline-haiku --a 2026-10-27-0528 --b 2026-11-03-0803
        [--split <name or file>] [--subset validation] [--outcome checks]
        [--alpha 0.05] [--splits 200000] [--seed 0] [--out <folder>]

When two runs are of the same harness, any difference between them is
chance. The command pools each task's attempts from both runs and splits them
at random into two halves, many times; each split is a comparison of the
harness with itself. The gate's statistic T is an estimate divided by its
estimated uncertainty, so over those splits it should have mean 0 and the
width of Student's t, and each decision rule should accept the share of
splits its level allows. The command measures all three.

It writes its results as data into ``--out`` (by default
``calibrations/<timestamp>``): ``pulls.csv``, the T of every split;
``scores.csv``, each task's score in each run; and ``calibration.csv``, each
quantity measured, its uncertainty and the value expected. ``calibration.md``
is a view of those files for reading, with three figures.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``stats``.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from mas_optimiser.benchmarks.enterpriseops.splits import load_split
from mas_optimiser.markdown import table
from mas_optimiser.stats import budget, figures, gate
from mas_optimiser.stats.compare import LABELS, evaluations
from mas_optimiser.stats.outcomes import OUTCOMES

DESCRIPTION = "check the gate's test on two runs of one harness: its statistic over random splits of their attempts"
# The figures, each with the caption the report gives it.
CAPTIONS = {
    "calibration": "Top: the T of every random split (points, with their "
    "counting errors) against Student's t (the line), on a logarithmic scale "
    "so that the tails show; the vertical marks are the bars of the decision "
    "rules and the long line is the T of the two runs as they were run. "
    "Bottom: the pull of each bin, its count minus the expected one in units "
    "of the expected count's error.",
    "scatter": "Each task's score in the first run against its score in the "
    "second; a larger dot is more tasks. On the diagonal a task scored the "
    "same both times, and the spread around it is the noise between two runs "
    "of one harness.",
    "distributions": "What the test assumes: the statistic T when nothing "
    "changed (solid curve) and when the true gain equals the minimum detectable effect "
    "(dashed curve). The bar cuts off the shaded tail of the first, the share "
    "of false acceptances allowed, and leaves the shaded part of the second, "
    "the real gains missed.",
}


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
    for side in ("--a", "--b"):
        parser.add_argument(
            side,
            required=True,
            metavar="RUN",
            help="one of the two runs of the harness: a run folder, or a run or "
            "day within --experiment",
        )
    parser.add_argument(
        "--split",
        default=None,
        help="a split's name or file; without it, every task both runs scored",
    )
    parser.add_argument(
        "--subset",
        default="validation",
        choices=("training", "validation", "test"),
        help="the split's set to use",
    )
    parser.add_argument(
        "--outcome", default="checks", choices=OUTCOMES, help="the outcome to score"
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.05,
        help="the Type-I error level the decision rules start from",
    )
    parser.add_argument(
        "--splits", type=int, default=200_000, help="the number of random splits"
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="the seed of the random splits"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="the folder to write; calibrations/<timestamp> by default",
    )


def pulls(scores: np.ndarray, splits: int = 200_000, seed: int = 0) -> np.ndarray:
    """The gate's statistic for each of many random splits of every task's attempts.

    The splitting is ``scipy.stats.permutation_test`` with
    ``permutation_type="samples"``: each attempt column is one sample, and
    every task's attempts are exchanged between the columns independently.
    Each split compares the mean of one half of the columns with the mean of
    the other, task by task, exactly as the gate compares a candidate with
    the current harness; the statistic is that of
    ``scipy.stats.ttest_1samp`` on the per-task differences. The attempts all
    come from one harness, so every split is a comparison in which nothing
    changed.

    Args:
        scores: One row per task and one column per attempt, an even number
            of columns.
        splits: The number of random splits.
        seed: The seed of the splitting.

    Returns:
        The statistic of each split; ``nan`` for a split whose differences
        are all equal.
    """
    half = scores.shape[1] // 2

    def statistic(*attempts: np.ndarray, axis: int) -> np.ndarray:
        """T of the second half of the attempt columns against the first.

        Args:
            *attempts: One array per attempt column, as SciPy passes them.
            axis: The axis the tasks lie along.

        Returns:
            The statistic of each split.
        """
        d = np.mean(attempts[half:], axis=0) - np.mean(attempts[:half], axis=0)
        return stats.ttest_1samp(d, 0.0, axis=axis).statistic

    return stats.permutation_test(
        tuple(scores.T),
        statistic,
        permutation_type="samples",
        vectorized=True,
        n_resamples=splits,
        batch=1000,
        rng=seed,
    ).null_distribution


def run(args: argparse.Namespace) -> None:
    """Split the two runs' attempts at random and write the data files, the figures and the report.

    Args:
        args: The parsed options, as ``add_arguments`` defines them.

    Raises:
        SystemExit: If the two runs differ in mode, model or harness, if they
            made different numbers of attempts per task, or if no task has
            all its attempts in both.
    """
    out = args.out or Path("calibrations") / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    wanted = None
    if args.split:
        wanted = {
            task
            for task, entry in load_split(args.split)["tasks"].items()
            if entry["set"] == args.subset
        }

    sides, described = [], []
    for spec in (args.a, args.b):
        folders = evaluations(args.experiment, spec)
        settings = pd.DataFrame(
            json.loads((p / "evaluation.json").read_text()) for p in folders
        )
        settings["model"] = settings["model"].map(
            lambda model: model["llm_model"] if isinstance(model, dict) else model
        )
        described.append(
            {
                field: ", ".join(settings[field].astype(str).unique())
                for field in ("run", "mode", "model", "harness")
            }
        )
        attempts = pd.concat(
            pd.read_csv(p / "tasks.csv").assign(domain=domain)
            for p, domain in zip(folders, settings["domain"], strict=True)
        )
        attempts = attempts[attempts["checks_total"] > 0]
        if wanted is not None:
            attempts = attempts[attempts["task_id"].isin(wanted)]
        attempts["score"] = (
            attempts["success"].astype(str).eq("True").astype(float)
            if args.outcome == "success"
            else attempts["checks_passed"] / attempts["checks_total"]
        )
        sides.append(
            attempts.pivot(
                index=["domain", "task_id"], columns="attempt", values="score"
            )
        )

    first, second = described
    differing = [f for f in ("mode", "model", "harness") if first[f] != second[f]]
    if differing:
        raise SystemExit(
            "error: calibrate needs two runs of one harness; these differ in "
            + ", ".join(f"{f} ({first[f]} against {second[f]})" for f in differing)
        )
    if sides[0].shape[1] != sides[1].shape[1]:
        raise SystemExit(
            "error: the two runs made different numbers of attempts per task: "
            f"{sides[0].shape[1]} and {sides[1].shape[1]}"
        )
    both = sides[0].join(sides[1], how="inner", lsuffix=" first", rsuffix=" second")
    both = both.dropna()
    if both.empty:
        raise SystemExit("error: no task has all its attempts in both runs")
    per_side = sides[0].shape[1]
    n = len(both)

    scores = pd.DataFrame(
        {
            "task_id": both.index.get_level_values("task_id"),
            "domain": both.index.get_level_values("domain"),
            "outcome": LABELS[args.outcome],
            "first_run_percent": 100 * both.iloc[:, :per_side].mean(axis=1).to_numpy(),
            "second_run_percent": 100 * both.iloc[:, per_side:].mean(axis=1).to_numpy(),
        }
    )
    t = pulls(both.to_numpy(), args.splits, args.seed)
    t = t[~np.isnan(t)]
    as_run = stats.ttest_1samp(
        scores["second_run_percent"] - scores["first_run_percent"], 0.0
    ).statistic

    rows = [
        {
            "quantity": "mean of T",
            "measured": t.mean(),
            "uncertainty": stats.sem(t),
            "expected": 0.0,
        },
        {
            "quantity": "width of T",
            "measured": t.std(ddof=1),
            # The uncertainty of a standard deviation estimated from many
            # values: sd / sqrt(2N).
            "uncertainty": t.std(ddof=1) / np.sqrt(2 * len(t)),
            "expected": stats.t.std(n - 1),
        },
    ]
    # A share of the splits is a binomial rate, with that rate's uncertainty.
    for rule, level in budget.levels(args.alpha).items():
        bar = gate.bar(n, level)
        rate = (t > bar).mean()
        rows.append(
            {
                "quantity": f"splits accepted at {rule} (%)",
                "measured": 100 * rate,
                "uncertainty": 100 * stats.binom.std(len(t), rate) / len(t),
                "expected": 100 * level,
                "bar": bar,
            }
        )
    rate = (t > 0).mean()
    rows += [
        {
            "quantity": "splits accepted by any gain (%)",
            "measured": 100 * rate,
            "uncertainty": 100 * stats.binom.std(len(t), rate) / len(t),
        },
        {"quantity": "T of the two runs as they were run", "measured": as_run},
    ]
    calibration = pd.DataFrame(
        rows, columns=["quantity", "measured", "uncertainty", "expected", "bar"]
    )

    pd.DataFrame({"t": t}).to_csv(out / "pulls.csv", index=False, float_format="%.5f")
    scores.to_csv(out / "scores.csv", index=False, float_format="%.2f")
    calibration.to_csv(out / "calibration.csv", index=False, float_format="%.6g")
    drawn = figures.draw_calibration(out)

    report_rows = []
    for _, row in calibration.iterrows():
        # A share of the splits is a percentage, to two decimals; a property
        # of T has three.
        percent = row["quantity"].endswith("(%)")
        form = ".2f" if percent else ".3f"
        measured = format(row["measured"], form)
        if not pd.isna(row["uncertainty"]):
            measured += f" ± {row['uncertainty']:{form}}"
        expected = ""
        if not pd.isna(row["expected"]):
            expected = format(row["expected"], ".3g" if percent else form)
        report_rows.append([row["quantity"], measured, expected])

    counts = ", ".join(
        f"{domain} {count}"
        for domain, count in scores["domain"].value_counts().sort_index().items()
    )
    tasks = (
        f"the {n} {args.subset} tasks of split {args.split}"
        if args.split
        else f"the {n} tasks both runs scored"
    )
    lines = [
        f"# Calibration: {first['run']} and {second['run']}",
        "",
        f"Two runs of one harness (mode {first['mode']}, model {first['model']}, "
        f"harness {first['harness']}) on {tasks} ({counts}), {per_side} attempts "
        "each. Any difference between them is chance, so every acceptance below "
        f"would be a false one. Each task's {2 * per_side} attempts were split at "
        f"random into two halves {len(t)} times and the test's statistic T "
        f"computed for each split, on {LABELS[args.outcome]}.",
        "",
        "## Is the test calibrated?",
        "",
        table(["quantity", "measured", "expected"], report_rows),
        "",
        "A calibrated test has a mean of 0, the width of Student's t, and "
        "accepts at each rule the share of no-change splits that the rule "
        "allows. A rule that keeps any gain accepts about half of them.",
        "",
    ]
    sections = {
        "calibration": None,
        "scatter": "## The noise the test has to see through",
        "distributions": "## What the test assumes",
    }
    for name, heading in sections.items():
        if name in drawn:
            lines += ([heading, ""] if heading else []) + [
                f"![{name}](figures/{name}.png)",
                "",
                f"*{CAPTIONS[name]}*",
                "",
            ]
    lines += [
        "## Limits",
        "",
        "- This measures false acceptance, not how often a real gain is found.",
        "- A task's attempts are treated as interchangeable, so any difference "
        "between the days of the two runs counts as noise.",
        "- It covers one decision at a time, not what a loop adds by reusing a "
        "run of the current harness across many candidates.",
        "- The uncertainties are those of the random splitting alone. Every "
        "split draws on the same attempts, so another pair of runs would give "
        "slightly different numbers.",
        "",
        "## Data",
        "",
        "- `calibration.csv`: the table above, with each rule's bar",
        "- `pulls.csv`: the T of every split",
        "- `scores.csv`: each task's score in each run",
        "",
    ]
    (out / "calibration.md").write_text("\n".join(lines))
    print(out)
    print(
        f"{len(t)} splits of {n} tasks: mean of T {t.mean():+.3f}, width "
        f"{t.std(ddof=1):.3f}; accepted at {100 * args.alpha:g}%: "
        f"{100 * (t > gate.bar(n, args.alpha)).mean():.2f}%"
    )
