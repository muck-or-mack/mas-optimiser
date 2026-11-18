"""Compare two sets of evaluations task by task: the ``mas-optimiser compare`` command.

    mas-optimiser compare --experiment baseline-haiku --a 2026-10-27-0528 --b 2026-11-03-0803
        [--split <name or file>] [--subset validation] [--outcome checks]
        [--alpha 0.05] [--out <folder>]

Each side is one evaluation per domain; ``--a`` is the current harness, ``--b``
the candidate. A side is named by evaluation folders, repeated once per
domain, by a run folder, or by a run name, a day (``20261027`` or
``2026-10-27``) or the start of a name within ``--experiment``, which the
experiment's ``index.csv`` resolves through each evaluation's start time.

The command writes its results as data into ``--out`` (by default
``comparisons/<timestamp>``): ``tests.csv``, a row per outcome and set of
tasks with every number of the test; ``rules.csv``, a row per decision rule;
``sides.csv``, a row per side with its settings, cost and time; and
``differences.csv``, a row per task. ``comparison.md`` is a view of those
files for reading, with the figures of ``figures/`` embedded. Scores are in
percent and differences in percentage points.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``stats``.
"""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from mas_optimiser.benchmarks.enterpriseops.runner import EVALUATIONS
from mas_optimiser.markdown import amount, table
from mas_optimiser.stats import budget, figures, gate
from mas_optimiser.stats.outcomes import OUTCOMES, Comparison, paired

DESCRIPTION = "compare two sets of evaluations task by task: the gate's test, its companions and the figures"
# How each outcome is named in the data files and the report.
LABELS = {"checks": "checks passed", "success": "attempts fully passed"}
# The random relabellings of the permutation test and the resamples of the
# bootstrap, and the seed each starts from, so that a report can be reproduced.
RESAMPLES = 9999
SEED = 0
TEST_COLUMNS = [
    "outcome",
    "set",
    "tasks",
    "current_percent",
    "candidate_percent",
    "gain_percentage_points",
    "false_acceptances_allowed_percent",
    "standard_error_percentage_points",
    "t",
    "bar",
    "p",
    "verdict",
    "lower_bound_percentage_points",
    "minimum_detectable_effect_percentage_points",
    "tasks_up",
    "tasks_down",
    "tasks_unchanged",
    "sign_test_p",
    "permutation_p",
    "bootstrap_standard_error_percentage_points",
    "bernstein_bound_percentage_points",
]
RULE_COLUMNS = [
    "outcome",
    "rule",
    "false_acceptances_allowed_percent",
    "bar",
    "t",
    "verdict",
    "minimum_detectable_effect_percentage_points",
]
# The figures, each with the caption the report gives it.
CAPTIONS = {
    "gains": "The gain on all tasks and in each domain. Each bar spans the gain "
    "plus and minus the bar on T times the standard error, so a row is accepted "
    "when its bar lies wholly to the right of the no-gain line. Only the row "
    "for all tasks decides.",
    "differences": "How many tasks rose, fell and stayed the same, in bins ten "
    "percentage points wide. The grey bar is the tasks that did not change; "
    "the line is the mean, which is the gain.",
    "decisions": "The T observed (the vertical line) against the bar of each "
    "decision rule (the marks). A rule accepts when its bar lies to the left "
    "of the line.",
    "detectable": "The chance that a real gain of each size would be accepted "
    "under each decision rule: theory, from this comparison's standard error. "
    "Each curve crosses 80% at that rule's minimum detectable effect, marked; the vertical "
    "line is the gain observed.",
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


def _row(set_name: str, cmp: Comparison, alpha: float) -> dict:
    """One row of ``tests.csv``: every number of the test on one set of tasks.

    Args:
        set_name: The set's name, ``all`` or a domain.
        cmp: The comparison, or the part of it for the domain.
        alpha: The Type-I error level of the decision.

    Returns:
        The row, keyed by ``TEST_COLUMNS``, scores in percent and differences
        in percentage points; only the first seven when the test cannot be
        computed (fewer than two tasks, or every difference equal).
    """
    d = cmp.d
    row = {
        "outcome": LABELS[cmp.outcome],
        "set": set_name,
        "tasks": len(cmp.tasks),
        "current_percent": 100 * cmp.a.mean(),
        "candidate_percent": 100 * cmp.b.mean(),
        "gain_percentage_points": 100 * d.mean(),
        "false_acceptances_allowed_percent": 100 * alpha,
    }
    try:
        test = gate.mean_test(d, alpha)
    except ValueError:
        return row
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
    return row | {
        "standard_error_percentage_points": 100 * test.se,
        "t": test.t,
        "bar": test.x0,
        "p": test.p,
        "verdict": "accept" if test.accept else "reject",
        "lower_bound_percentage_points": 100 * test.lower,
        "minimum_detectable_effect_percentage_points": 100
        * gate.detectable(test.se, test.n, alpha),
        "tasks_up": sign.n_plus,
        "tasks_down": sign.n_minus,
        "tasks_unchanged": len(d) - sign.n_plus - sign.n_minus,
        "sign_test_p": sign.p,
        "permutation_p": permutation.pvalue,
        "bootstrap_standard_error_percentage_points": 100 * spread.standard_error,
        "bernstein_bound_percentage_points": 100 * gate.bernstein_bound(d, alpha),
    }


def _report(
    basis: str,
    sides: pd.DataFrame,
    tests: pd.DataFrame,
    rules: pd.DataFrame,
    outcome: str,
    drawn: list[str],
) -> str:
    """The comparison as a page for reading, a view of the data files.

    Every number shown is read from the frames, which are what the command
    writes to ``sides.csv``, ``tests.csv`` and ``rules.csv``.

    Args:
        basis: One sentence saying which tasks were compared.
        sides: The two sides, as ``sides.csv`` holds them.
        tests: The tests, as ``tests.csv`` holds them.
        rules: The decision rules, as ``rules.csv`` holds them.
        outcome: The outcome that decides, as ``LABELS`` names it.
        drawn: The names of the figures drawn; each is embedded under the
            table it illustrates.

    Returns:
        The page as Markdown.
    """

    def shown(value: object, form: str) -> str:
        return "n/a" if pd.isna(value) else format(value, form)

    current, candidate = sides.to_dict("records")
    whole = tests[tests["set"] == "all"]
    gate_rows = tests[tests["outcome"] == outcome]
    decides = gate_rows.iloc[0]
    level = f"{decides['false_acceptances_allowed_percent']:g}%"
    quantities = [
        ("gain", "gain_percentage_points", "+.2f"),
        ("standard error", "standard_error_percentage_points", ".2f"),
        ("T", "t", "+.2f"),
        ("bar on T", "bar", ".2f"),
        ("p", "p", ".3f"),
        ("lower bound", "lower_bound_percentage_points", "+.2f"),
        (
            "minimum detectable effect",
            "minimum_detectable_effect_percentage_points",
            ".1f",
        ),
        ("sign test p", "sign_test_p", ".3f"),
        ("permutation p", "permutation_p", ".3f"),
        (
            "bootstrap standard error",
            "bootstrap_standard_error_percentage_points",
            ".2f",
        ),
    ]
    meaning = {
        "gain": "mean over tasks of candidate minus current",
        "standard error": "how far the gain would move if both runs were repeated",
        "T": "gain divided by standard error",
        "bar on T": "T must exceed this to be accepted",
        "p": "chance that a change that does nothing gives a T this large",
        "lower bound": "the true gain is above this with "
        f"{100 - decides['false_acceptances_allowed_percent']:g}% confidence",
        "minimum detectable effect": "smallest true gain the test catches 4 times in 5",
        "tasks up / down / same": "tasks whose score rose, fell or stayed the same",
        "sign test p": "cross-check using only the direction of each change",
        "permutation p": "cross-check that does not assume the test's distribution",
        "bootstrap standard error": "cross-check of the standard error by resampling tasks",
    }
    full = [
        [name]
        + [shown(row[column], form) for _, row in whole.iterrows()]
        + [meaning[name]]
        for name, column, form in quantities
    ]
    full.insert(
        7,
        ["tasks up / down / same"]
        + [
            " / ".join(
                shown(row[c], ".0f")
                for c in ("tasks_up", "tasks_down", "tasks_unchanged")
            )
            for _, row in whole.iterrows()
        ]
        + [meaning["tasks up / down / same"]],
    )

    def figure(name: str) -> list[str]:
        if name not in drawn:
            return []
        return [f"![{name}](figures/{name}.png)", "", f"*{CAPTIONS[name]}*", ""]

    lines = [
        f"# Comparison: {candidate['run']} against {current['run']}",
        "",
        "## What is compared",
        "",
        table(
            ["side", "run", "mode", "model", "harness", "attempts per task"],
            [
                [
                    side["side"],
                    side["run"],
                    side["mode"],
                    side["model"],
                    side["harness"],
                    str(side["attempts_per_task"]),
                ]
                for side in (current, candidate)
            ],
        ),
        "",
        basis,
        "",
        "## Result",
        "",
        table(
            [
                "outcome",
                "current",
                "candidate",
                "gain",
                f"verdict at {level}",
                "minimum detectable effect",
            ],
            [
                [
                    row["outcome"],
                    f"{row['current_percent']:.1f}%",
                    f"{row['candidate_percent']:.1f}%",
                    f"{row['gain_percentage_points']:+.1f} percentage points",
                    "n/a"
                    if pd.isna(row["verdict"])
                    else f"{row['verdict']} (p {row['p']:.3f})",
                    f"{shown(row['minimum_detectable_effect_percentage_points'], '.1f')} percentage points",
                ]
                for _, row in whole.iterrows()
            ],
        ),
        "",
        f'The decision uses "{outcome}". Checks passed is each task\'s percentage of '
        "checks passed, averaged over its attempts and then over tasks, so every "
        "task counts equally. Attempts fully passed is the percentage of attempts "
        "in which every check passed.",
        "",
        "## Cost and time",
        "",
        table(
            [
                "side",
                "attempts",
                "cost per attempt",
                "total cost",
                "seconds per attempt",
                "errors",
            ],
            [
                [
                    side["side"],
                    str(side["attempts"]),
                    f"${side['cost_per_attempt_usd']:.4f}",
                    f"${amount(side['total_cost_usd'])}",
                    f"{side['seconds_per_attempt']:.1f}",
                    str(side["errors"]),
                ]
                for side in (current, candidate)
            ],
        ),
        "",
        f"## By domain ({outcome})",
        "",
        table(
            [
                "tasks",
                "current",
                "candidate",
                "gain (percentage points)",
                "minimum detectable effect (percentage points)",
                f"verdict at {level}",
            ],
            [
                [
                    f"{row['set']}, {row['tasks']}",
                    f"{row['current_percent']:.1f}%",
                    f"{row['candidate_percent']:.1f}%",
                    f"{row['gain_percentage_points']:+.1f}",
                    shown(row["minimum_detectable_effect_percentage_points"], ".1f"),
                    "n/a" if pd.isna(row["verdict"]) else row["verdict"],
                ]
                for _, row in gate_rows.iterrows()
            ],
        ),
        "",
        "The domain rows are for information. Only the first row decides.",
        "",
        *figure("gains"),
        f"## The test in full (all {decides['tasks']} tasks)",
        "",
        table(["quantity", *whole["outcome"], "what it is"], full),
        "",
        "Gain, standard error, lower bound, minimum detectable effect and bootstrap standard "
        "error are in percentage points.",
        "",
        *figure("differences"),
        f"## Decision rules ({outcome}; T is {shown(decides['t'], '+.2f')})",
        "",
        table(
            [
                "rule",
                "false acceptances allowed",
                "bar on T",
                "verdict",
                "minimum detectable effect (percentage points)",
            ],
            [
                [
                    row["rule"],
                    f"{row['false_acceptances_allowed_percent']:.3g}%",
                    shown(row["bar"], ".2f"),
                    "n/a" if pd.isna(row["verdict"]) else row["verdict"],
                    shown(row["minimum_detectable_effect_percentage_points"], ".1f"),
                ]
                for _, row in rules.iterrows()
            ],
        ),
        "",
        *figure("decisions"),
        *figure("detectable"),
        "## Data",
        "",
        "- `tests.csv`: every number above, for every outcome and set of tasks",
        "- `rules.csv`: the decision rules",
        "- `sides.csv`: the two sides, with their cost and time",
        "- `differences.csv`: one row per task",
        "",
    ]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> None:
    """Compare the two sides and write the data files, the figures and the report.

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
    domains = sorted(set(primary.domains))

    tests = pd.DataFrame(
        [
            _row(name, part, args.alpha)
            for cmp in comparisons.values()
            for name, part in [("all", cmp)]
            + [(domain, cmp.domain(domain)) for domain in domains]
        ],
        columns=TEST_COLUMNS,
    )

    levels = budget.levels(
        args.alpha,
        args.round_number,
        args.promotions,
        args.candidates,
        args.decisions,
        args.sigma,
    )
    rule_rows = []
    for name, level in levels.items():
        row = {
            "outcome": LABELS[args.outcome],
            "rule": name,
            "false_acceptances_allowed_percent": 100 * level,
        }
        try:
            test = gate.mean_test(primary.d, level)
            row |= {
                "bar": test.x0,
                "t": test.t,
                "verdict": "accept" if test.accept else "reject",
                "minimum_detectable_effect_percentage_points": 100
                * gate.detectable(test.se, test.n, level),
            }
        except ValueError:
            pass
        rule_rows.append(row)
    rules = pd.DataFrame(rule_rows, columns=RULE_COLUMNS).sort_values(
        "false_acceptances_allowed_percent", ascending=False
    )

    side_rows = []
    for side, folders in (("current", a), ("candidate", b)):
        settings = pd.DataFrame(
            json.loads((p / "evaluation.json").read_text()) for p in folders
        )
        settings["model"] = settings["model"].map(
            lambda model: model["llm_model"] if isinstance(model, dict) else model
        )
        attempts = pd.concat(pd.read_csv(p / "tasks.csv") for p in folders)
        attempts = attempts[attempts["task_id"].isin(primary.tasks)]
        # A setting the side's evaluations share is written once.
        row = {"side": side} | {
            field: ", ".join(settings[field].astype(str).unique())
            for field in ("run", "mode", "model", "harness", "attempts")
        }
        row["attempts_per_task"] = row.pop("attempts")
        side_rows.append(
            row
            | {
                "attempts": len(attempts),
                "cost_per_attempt_usd": attempts["cost_usd"].mean(),
                "total_cost_usd": attempts["cost_usd"].sum(),
                "seconds_per_attempt": attempts["seconds"].mean(),
                "errors": int(attempts["error"].notna().sum()),
            }
        )
    sides = pd.DataFrame(side_rows)

    differences = pd.DataFrame({"task_id": primary.tasks, "domain": primary.domains})
    for outcome, cmp in comparisons.items():
        name = LABELS[outcome].replace(" ", "_")
        differences[f"{name}_current_percent"] = 100 * cmp.a
        differences[f"{name}_candidate_percent"] = 100 * cmp.b
        differences[f"{name}_difference_percentage_points"] = 100 * cmp.d

    tests.to_csv(out / "tests.csv", index=False, float_format="%.6g")
    rules.to_csv(out / "rules.csv", index=False, float_format="%.6g")
    sides.to_csv(out / "sides.csv", index=False, float_format="%.6g")
    differences.to_csv(out / "differences.csv", index=False, float_format="%.2f")

    drawn = figures.draw(out)

    counts = ", ".join(f"{d} {primary.domains.count(d)}" for d in domains)
    basis = (
        f"The same {len(primary.tasks)} {args.subset} tasks of split {args.split} "
        f"on both sides ({counts})."
        if args.split
        else f"Every task both sides scored: {len(primary.tasks)} tasks ({counts})."
    )
    (out / "comparison.md").write_text(
        _report(basis, sides, tests, rules, LABELS[args.outcome], drawn)
    )

    decides = tests[tests["outcome"] == LABELS[args.outcome]].iloc[0]
    print(out)
    print(
        f"all {decides['tasks']} tasks, {decides['outcome']}: "
        f"{decides['current_percent']:.1f}% to {decides['candidate_percent']:.1f}%, "
        f"gain {decides['gain_percentage_points']:+.2f} percentage points"
        + (
            ""
            if pd.isna(decides["verdict"])
            else f", T {decides['t']:+.2f}, p {decides['p']:.3f}, {decides['verdict']}"
        )
    )
