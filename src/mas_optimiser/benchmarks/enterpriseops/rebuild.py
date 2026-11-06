"""Write the database an attempt ended with, rebuilt from its saved changes, as a SQLite file.

The file ``attempt-<n>.sqlite`` lands beside the attempt's record. Open it
with ``sqlite3`` or an editor's SQLite viewer to run a check's query by hand
or to browse every table; the rebuild is the one ``rescore`` judges on.

Usage::

    mas-optimiser rebuild evaluations/<experiment>/<evaluation> <task_id> <attempt>
"""

import argparse
import json
import sqlite3
from pathlib import Path

from mas_optimiser.benchmarks.enterpriseops import runner, states

DESCRIPTION = __doc__.split("\n\n")[0]


def rebuild_attempt(evaluation: Path, task_id: str, attempt: int) -> Path:
    """Rebuild one attempt's final database and write it beside its record.

    Args:
        evaluation: The evaluation folder.
        task_id: The task's dataset ID.
        attempt: The attempt number, from 1.

    Returns:
        The path of the SQLite file written.

    Raises:
        FileNotFoundError: If the attempt saved no changes (one that raised
            before it finished).
    """
    settings = json.loads((evaluation / "evaluation.json").read_text())
    folder = evaluation / "tasks" / task_id
    saved = folder / f"attempt-{attempt}.changes.json"
    if not saved.exists():
        raise FileNotFoundError(f"{saved}: the attempt saved no changes")
    config = runner.load_tasks(settings["domain"], settings["mode"])[task_id]
    seed, layout = states.seed_copy(
        Path(config["gym_servers_config"][0]["seed_database_file"]),
        settings["domain"],
    )
    final = states.rebuild(
        seed, json.loads(saved.read_text())["changes"], layout["keys"]
    )
    # The rebuild leaves its writes in an open transaction, which the backup
    # would wait on for ever.
    final.commit()
    path = folder / f"attempt-{attempt}.sqlite"
    path.unlink(missing_ok=True)
    out = sqlite3.connect(path)
    final.backup(out)
    out.close()
    return path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the rebuild options to ``parser``; shared with the ``mas-optimiser`` command.

    Args:
        parser: The ``rebuild`` subparser.
    """
    parser.add_argument(
        "evaluation", type=Path, help="evaluation folder under evaluations/"
    )
    parser.add_argument("task_id", help="the task's dataset ID")
    parser.add_argument("attempt", type=int, help="attempt number, from 1")


def run(args: argparse.Namespace) -> None:
    """Rebuild the attempt's database and print where it was written.

    Args:
        args: The parsed arguments.
    """
    print(rebuild_attempt(args.evaluation, args.task_id, args.attempt))
