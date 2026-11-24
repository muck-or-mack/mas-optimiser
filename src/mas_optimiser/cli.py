"""The ``mas-optimiser`` command: one entry point for everything the package does.

``mas-optimiser --help`` lists the subcommands and ``mas-optimiser <command>
--help`` lists that command's options. Each subcommand lives in its own
module, which defines its options (``add_arguments``) and its work (``run``):

    mas-optimiser run smoke --domain itsm --limit 5
    mas-optimiser rescore evaluations/<experiment>/<evaluation>
    mas-optimiser rebuild evaluations/<experiment>/<evaluation> <task_id> 1
    mas-optimiser split --name by-domain
    mas-optimiser schema
    mas-optimiser compare --experiment baseline-haiku --a 20261027 --b 20261103
    mas-optimiser calibrate --experiment baseline-haiku --a 20261027 --b 20261103
    mas-optimiser list baseline-haiku
    mas-optimiser evolve evolve-smoke
    mas-optimiser report evolutions/evolve-smoke/haiku-20-40-40
    mas-optimiser server qwen3.6-27b-fp8

Sources: ``docs/how-it-works.md``, section 6, the rows for ``cli``.
"""

import argparse

from mas_optimiser import server
from mas_optimiser.benchmarks.enterpriseops import (
    listing,
    rebuild,
    rescore,
    runner,
    seeds,
    splits,
)
from mas_optimiser.evolve import loop, report
from mas_optimiser.stats import calibrate, compare

COMMANDS = {
    "run": runner,
    "rescore": rescore,
    "rebuild": rebuild,
    "split": splits,
    "schema": seeds,
    "compare": compare,
    "calibrate": calibrate,
    "list": listing,
    "evolve": loop,
    "report": report,
    "server": server,
}


def _build_parser() -> argparse.ArgumentParser:
    """The top-level parser with one subparser per command.

    Returns:
        The parser; each subparser's ``run`` default is its module's ``run``.
    """
    parser = argparse.ArgumentParser(
        prog="mas-optimiser", description=__doc__.split("\n\n")[0]
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="<command>"
    )
    for name, module in COMMANDS.items():
        sub = subparsers.add_parser(
            name, help=module.DESCRIPTION, description=module.DESCRIPTION
        )
        module.add_arguments(sub)
        sub.set_defaults(run=module.run)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Parse ``argv`` (the command line by default) and run the chosen command.

    Args:
        argv: The arguments to parse, or ``None`` for the process's own.
    """
    args = _build_parser().parse_args(argv)
    args.run(args)
