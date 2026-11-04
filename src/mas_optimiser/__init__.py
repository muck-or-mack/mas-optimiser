"""Planning-layer evolution for multi-agent systems, measured on EnterpriseOps-Gym.

The package has three parts. ``benchmarks.enterpriseops`` runs an agent on
the benchmark's tasks and measures the result (the ``mas-optimiser``
command, ``cli``); ``tracing`` sends each run to Langfuse; the planner and
the optimiser are to come. ``code_version`` stamps every run with the code
that produced it.
"""

import subprocess
from pathlib import Path

try:
    from mas_optimiser._version import version as __version__
except ImportError:
    __version__ = "0.0.0"


def code_version() -> str:
    """The checkout's version as git describes it now, e.g. ``v0.5.0-3-gabc1234-dirty``.

    ``__version__`` is fixed when the package is installed, so in an editable
    install it can name older code than the code that runs. Git describes the
    checkout at the moment of the call, so a run takes it once, when it
    starts.

    Returns:
        The ``git describe`` string, or ``__version__`` outside a git checkout.
    """
    try:
        described = subprocess.run(
            ["git", "describe", "--tags", "--long", "--dirty", "--always"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return __version__
    return described.stdout.strip()
