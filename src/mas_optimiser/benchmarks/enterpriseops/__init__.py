"""Adapter for the EnterpriseOps-Gym benchmark: run its tasks, measure the result, explain each failure.

The modules form one chain. ``runner`` runs an experiment's tasks through
the benchmark's executor and, for each attempt, hands the record on:
``checks`` gives the verdicts (every distinct check once, the patch list
applied, the benchmark's own verdict kept beside ours), ``states`` reads
what the attempt changed against the seed, ``diagnosis`` says which
condition of each failed check did not hold on the rows the attempt
touched, and ``report`` traces each wrong cell to the agent's turn and
writes the page per attempt, the tables per evaluation and the summary.
``rescore`` replays that chain from the saved records without running the
agent. ``seeds`` and ``splits`` prepare the data: local copies of the seed
databases, and the one-off draw of the training, validation and test sets.
``docs/how-it-works.md`` follows one attempt through all of it.

The benchmark is not an installable package: its modules (``benchmark``,
``orchestrators``, ``utils``) sit at the top of its repository. Importing
this package puts the pinned submodule at the front of ``sys.path``, so
those names resolve to the submodule and not to anything else installed
under them.
"""

import sys
from pathlib import Path

GYM_ROOT = Path(__file__).resolve().parents[4] / "vendor" / "enterpriseops-gym"
# Data, configs and evaluations live at fixed places in the repository, so commands
# behave the same from any working directory.
REPO_ROOT = GYM_ROOT.parents[1]

if not (GYM_ROOT / "benchmark").is_dir():
    raise ImportError(
        f"EnterpriseOps-Gym not found at {GYM_ROOT}; "
        "run `git submodule update --init` from the repository root."
    )
sys.path.insert(0, str(GYM_ROOT))
