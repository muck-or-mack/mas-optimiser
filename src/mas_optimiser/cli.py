"""The ``mas-optimiser`` command: one entry point for everything the package does.

``mas-optimiser --help`` lists the subcommands and ``mas-optimiser <command>
--help`` lists that command's options. Each subcommand lives in its own
module, which defines the options once (``add_arguments``) and the work
(``run``), and still runs on its own with ``python -m``:

    mas-optimiser run --model configs/models/haiku-5-5.yaml --domain itsm --limit 5
    mas-optimiser replay runs/<stamp>/tasks/<task_id>.json
"""

import argparse

from mas_optimiser.benchmarks.enterpriseops import replay, runner

COMMANDS = {
    "run": runner,
    "replay": replay,
}


def build_parser() -> argparse.ArgumentParser:
    """The top-level parser with one subparser per command."""
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
    """Parse ``argv`` (the command line by default) and run the chosen command."""
    args = build_parser().parse_args(argv)
    args.run(args)
