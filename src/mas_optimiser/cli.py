"""The ``mas-optimiser`` command: one entry point for everything the package does.

``mas-optimiser --help`` lists the subcommands and ``mas-optimiser <command>
--help`` lists that command's options. Each subcommand lives in its own
module, which defines its options (``add_arguments``) and its work (``run``):

    mas-optimiser run smoke --domain itsm --limit 5
    mas-optimiser rescore evaluations/<experiment>/<evaluation>
    mas-optimiser rebuild evaluations/<experiment>/<evaluation> <task_id> 1
    mas-optimiser split --experiment baseline-haiku --name haiku-20-40-40
    mas-optimiser schema
"""

import argparse

from mas_optimiser.benchmarks.enterpriseops import (
    rebuild,
    rescore,
    runner,
    seeds,
    splits,
)

COMMANDS = {
    "run": runner,
    "rescore": rescore,
    "rebuild": rebuild,
    "split": splits,
    "schema": seeds,
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
