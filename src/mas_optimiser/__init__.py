import subprocess
from pathlib import Path

try:
    from mas_optimiser._version import version as __version__
except ImportError:
    __version__ = "0.0.0"


def code_version() -> str:
    """The running code's version as git describes it, e.g. ``v0.5.0-3-gabc1234-dirty``.

    ``__version__`` is fixed when the package is installed, so in an editable
    install it can name older code than the code that runs. Outside a git
    checkout, ``__version__`` is all there is.
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
