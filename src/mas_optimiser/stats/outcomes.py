"""Per-task scores of an evaluation, and the paired differences between two.

Two outcomes score an attempt: ``success`` is 1 when every check passed and
0 otherwise; ``checks`` is the fraction of its checks that passed. A task's
score is the mean of its attempts' outcomes. ``paired`` lines up two
evaluations' tasks and keeps each task's difference ``b - a``; the sides must
have scored the same tasks, so that a task's own difficulty cancels in its
difference.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``stats``.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from mas_optimiser.benchmarks.enterpriseops.splits import load_split

OUTCOMES = ("checks", "success")


def task_scores(evaluation: Path, outcome: str) -> dict[str, float]:
    """Each task's mean score over its attempts in one evaluation.

    Reads the evaluation's ``tasks.csv`` (one row per attempt, written by
    ``runner.task_outcome``). An attempt that ended in an error counts as the
    runner recorded it, a failure. A task whose attempts have no checks
    (``checks_total`` 0, every check excluded by the patch list) is left out.

    Args:
        evaluation: The evaluation's folder.
        outcome: ``"checks"`` for the fraction of checks passed, ``"success"``
            for 1 when every check passed.

    Returns:
        Task id to the mean of its attempts' scores.

    Raises:
        ValueError: If ``outcome`` is not one of ``OUTCOMES``.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, not {outcome!r}")
    attempts = pd.read_csv(Path(evaluation) / "tasks.csv")
    attempts = attempts[attempts["checks_total"] > 0]
    if outcome == "success":
        score = attempts["success"].astype(str).eq("True").astype(float)
    else:
        score = attempts["checks_passed"] / attempts["checks_total"]
    return score.groupby(attempts["task_id"]).mean().to_dict()


@dataclass(frozen=True)
class Comparison:
    """Two harnesses' scores on the same tasks, and the differences between them.

    Attributes:
        tasks: The task ids, in a fixed order (by domain, then id).
        domains: Each task's domain, aligned with ``tasks``.
        a: The current harness's per-task scores, aligned with ``tasks``.
        b: The candidate's per-task scores, aligned with ``tasks``.
        outcome: The outcome the scores are, one of ``OUTCOMES``.
    """

    tasks: tuple[str, ...]
    domains: tuple[str, ...]
    a: np.ndarray
    b: np.ndarray
    outcome: str

    @property
    def d(self) -> np.ndarray:
        """The per-task differences ``b - a``, positive where the candidate did better.

        Returns:
            One difference per task, aligned with ``tasks``.
        """
        return self.b - self.a

    def domain(self, name: str) -> "Comparison":
        """The comparison restricted to one domain's tasks.

        Args:
            name: The domain, as ``evaluation.json`` names it.

        Returns:
            A comparison over the tasks of that domain, in the same order.
        """
        keep = [i for i, domain in enumerate(self.domains) if domain == name]
        return Comparison(
            tasks=tuple(self.tasks[i] for i in keep),
            domains=tuple(self.domains[i] for i in keep),
            a=self.a[keep],
            b=self.b[keep],
            outcome=self.outcome,
        )


def paired(
    a: list[Path],
    b: list[Path],
    outcome: str,
    split: str | Path | None = None,
    subset: str = "validation",
) -> Comparison:
    """Pair two sides' evaluations task by task.

    Each side is a list of evaluations, one per domain; a task's domain is
    read from its evaluation's ``evaluation.json``. With a split, only the
    tasks in its ``subset`` are kept; without one, every task both sides
    scored. The two sides must then cover the same tasks.

    Args:
        a: The current harness's evaluations.
        b: The candidate's evaluations.
        outcome: The outcome to score, one of ``OUTCOMES``.
        split: A split's name or path (``splits.load_split``), or ``None``.
        subset: The split's set to keep: ``"training"``, ``"validation"`` or
            ``"test"``.

    Returns:
        The paired comparison.

    Raises:
        ValueError: If the two sides scored different tasks after filtering.
    """
    sides = []
    for evaluations in (a, b):
        scores: dict[str, float] = {}
        domains: dict[str, str] = {}
        for evaluation in evaluations:
            domain = json.loads((Path(evaluation) / "evaluation.json").read_text())
            for task, score in task_scores(evaluation, outcome).items():
                scores[task] = score
                domains[task] = domain["domain"]
        sides.append((scores, domains))
    (scores_a, domains_a), (scores_b, _) = sides
    if split is not None:
        wanted = set(load_split(split)[subset])
        scores_a = {t: s for t, s in scores_a.items() if t in wanted}
        scores_b = {t: s for t, s in scores_b.items() if t in wanted}
    only = set(scores_a) ^ set(scores_b)
    if only:
        raise ValueError(
            f"{len(only)} task(s) scored on one side only, for example {sorted(only)[:3]}"
        )
    tasks = tuple(sorted(scores_a, key=lambda t: (domains_a[t], t)))
    return Comparison(
        tasks=tasks,
        domains=tuple(domains_a[t] for t in tasks),
        a=np.array([scores_a[t] for t in tasks]),
        b=np.array([scores_b[t] for t in tasks]),
        outcome=outcome,
    )
