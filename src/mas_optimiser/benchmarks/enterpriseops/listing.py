"""List an experiment's evaluations: the ``mas-optimiser list`` command.

    mas-optimiser list <experiment> [--domain itsm]

Prints the experiment's ``index.csv`` as a table, one row per evaluation in
start order: when it ran, its domain, mode, model and label, how many
attempts passed and how many checks, and what it cost. The experiment is a
name under ``evaluations/`` or a folder.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``runner``.
"""

import argparse
import csv
from pathlib import Path

from mas_optimiser.benchmarks.enterpriseops.runner import EVALUATIONS

DESCRIPTION = "list an experiment's evaluations from its index, in start order"
COLUMNS = [
    "evaluation",
    "started",
    "domain",
    "mode",
    "model",
    "label",
    "attempts passed",
    "checks passed",
    "cost (USD)",
]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Define the command's options.

    Args:
        parser: The command's subparser.
    """
    parser.add_argument(
        "experiment", help="the experiment's name under evaluations/, or its folder"
    )
    parser.add_argument("--domain", default=None, help="show one domain only")


def table(experiment: Path, domain: str | None = None) -> str:
    """The experiment's evaluations as a markdown table.

    Args:
        experiment: The experiment's folder, holding ``index.csv``.
        domain: A domain to keep, or ``None`` for all.

    Returns:
        The table, one row per evaluation in start order.
    """
    with open(experiment / "index.csv", newline="") as f:
        rows = [r for r in csv.DictReader(f) if domain is None or r["domain"] == domain]
    rows.sort(key=lambda r: r["started"])
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        cells = [
            r["evaluation"],
            r["started"].replace("T", " ")[:16],
            r["domain"],
            r["mode"],
            r["model"],
            r["label"],
            f"{r['successes']} of {r['total_attempts']}",
            f"{r['checks_passed']} of {r['checks_total']}",
            f"{float(r['cost_usd']):.2f}" if r["cost_usd"] else "",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> None:
    """Print the experiment's evaluations.

    Args:
        args: The parsed options, as ``add_arguments`` defines them.

    Raises:
        SystemExit: If the experiment has no ``index.csv``.
    """
    folder = Path(args.experiment)
    if not folder.is_dir():
        folder = EVALUATIONS / args.experiment
    if not (folder / "index.csv").exists():
        raise SystemExit(f"error: no index.csv in {folder}")
    print(table(folder, args.domain))
