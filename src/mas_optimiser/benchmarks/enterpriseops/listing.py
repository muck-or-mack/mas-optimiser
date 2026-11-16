"""List an experiment's runs: the ``mas-optimiser list`` command.

    mas-optimiser list <experiment> [--domain itsm]

Prints the experiment's ``index.csv`` as a table, one block per run: a row
for the run as a whole, with its domains' totals summed, then a row per
domain. Rows from before runs had names are grouped by the day they
started. The experiment is a name under ``evaluations/`` or a folder.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``runner``.
"""

import argparse
import csv
from pathlib import Path

from mas_optimiser import markdown
from mas_optimiser.benchmarks.enterpriseops.runner import EVALUATIONS

DESCRIPTION = "list an experiment's runs and their domains from its index"
COLUMNS = [
    "run",
    "domain",
    "mode",
    "started",
    "model",
    "label",
    "attempts passed",
    "errors",
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


def _cells(run: str, domain: str, rows: list[dict]) -> list[str]:
    """One table row from one or several index rows, their counts summed.

    Args:
        run: The run's name.
        domain: The domain, or ``all`` for the run as a whole.
        rows: The index rows summed into this row.

    Returns:
        The cells, in ``COLUMNS`` order.
    """
    first = min(rows, key=lambda r: r["started"])
    total = {
        key: sum(float(r[key] or 0) for r in rows)
        for key in (
            "successes",
            "total_attempts",
            "errors",
            "checks_passed",
            "checks_total",
            "cost_usd",
        )
    }
    return [
        run,
        domain,
        first["mode"],
        first["started"].replace("T", " ")[:16],
        first["model"],
        first["label"],
        f"{total['successes']:.0f} of {total['total_attempts']:.0f}",
        f"{total['errors']:.0f}",
        f"{total['checks_passed']:.0f} of {total['checks_total']:.0f}",
        f"{total['cost_usd']:.2f}",
    ]


def table(experiment: Path, domain: str | None = None) -> str:
    """The experiment's runs as a markdown table with aligned columns.

    Args:
        experiment: The experiment's folder, holding ``index.csv``.
        domain: A domain to keep, or ``None`` for all.

    Returns:
        The table: per run, a summed row then a row per domain, in start order.
    """
    with open(experiment / "index.csv", newline="") as f:
        rows = [r for r in csv.DictReader(f) if domain is None or r["domain"] == domain]
    runs: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: r["started"]):
        runs.setdefault(r.get("run") or r["started"][:10], []).append(r)
    body = []
    for run, members in runs.items():
        if len(members) > 1:
            body.append(_cells(run, "all", members))
        for r in members:
            body.append(_cells(run, r["domain"], [r]))
    return markdown.table(COLUMNS, body)


def run(args: argparse.Namespace) -> None:
    """Print the experiment's runs.

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
