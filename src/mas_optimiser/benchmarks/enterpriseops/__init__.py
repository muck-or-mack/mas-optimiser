"""Adapter for the EnterpriseOps-Gym benchmark.

The benchmark is not an installable package: its modules (`benchmark`,
`orchestrators`, `utils`) sit at the top of its repository. Importing this
package puts the pinned submodule at the front of `sys.path`, so those names
resolve to the submodule and not to anything else installed under them.
"""

import sys
from pathlib import Path

GYM_ROOT = Path(__file__).resolve().parents[4] / "vendor" / "enterpriseops-gym"
# Data, configs and runs live at fixed places in the repository, so commands
# behave the same from any working directory.
REPO_ROOT = GYM_ROOT.parents[1]

if not (GYM_ROOT / "benchmark").is_dir():
    raise ImportError(
        f"EnterpriseOps-Gym not found at {GYM_ROOT}; "
        "run `git submodule update --init` from the repository root."
    )
sys.path.insert(0, str(GYM_ROOT))
