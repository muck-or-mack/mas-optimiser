"""Improve a harness by a loop of proposals, each judged by the acceptance gate.

One experiment starts from a harness folder and a split of the tasks. In each
round a proposer, a model working offline, reads how the current harness did
on the training tasks and changes a copy of the folder; the copy is run on
the validation tasks; and the acceptance gate (``mas_optimiser.stats``)
decides whether it replaces the current harness. The proposer is told only
whether a proposal was accepted. At the end every harness that was accepted
is run once on the test tasks.

The loop is built on the released code of RRSI (Xia et al., "Regularized
Recursive Self-Improvement of Agent Harnesses", arXiv:2609.24972;
github.com/google-research/rrsi, Apache License 2.0), kept unchanged under
``vendor/rrsi``. Used as released: the proposer (a file editor confined to
the harness folder), the review of each proposal for leaked task content,
the analysis of the traces, the record of proposals, and the isolation of
every candidate in its own git worktree. Replaced here, because RRSI reads
its traces and judges its candidates on one and the same set of tasks:

- the round (``loop``), which reads the training tasks and judges on the
  validation tasks;
- the rule that keeps a change (``loop``), which is the paired test of
  ``mas_optimiser.stats.gate`` at a level that shares the experiment's error
  budget over its decisions (``mas_optimiser.stats.budget``);
- the model call of the search roles (``models``), which reads the project's
  model files, so that any provider the benchmark's client knows can propose.

RRSI's modules import each other by the name ``rrsi``, so importing this
package puts ``vendor/rrsi`` at the front of ``sys.path``.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``evolve``.
"""

import sys
from pathlib import Path

RRSI_ROOT = Path(__file__).resolve().parents[3] / "vendor" / "rrsi"

if not (RRSI_ROOT / "rrsi").is_dir():
    raise ImportError(
        f"RRSI not found at {RRSI_ROOT}; "
        "run `git submodule update --init` from the repository root"
    )
sys.path.insert(0, str(RRSI_ROOT))
