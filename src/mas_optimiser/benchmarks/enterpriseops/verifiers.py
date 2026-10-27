"""Count every distinct verifier of a task, and keep the benchmark's own count beside it.

The benchmark's executor runs each task's verifiers (SQL checks on the final
database state) and keeps their verdicts in a dict keyed by verifier name, so
the task passes only if every verdict does. Three details of the data make
that count wrong for the purpose here. In 59 of the 308 oracle tasks two or
more verifiers share a name: in 32 of them the verifier list is simply
repeated, so the copies are one check counted several times, and in 27 the
shared name hides different checks, of which the executor's dict keeps only
the last. And 33 count checks in seven tasks give their expected value as the
string ``'1'`` where the SQL runner returns the integer ``1``; the executor
compares the two with ``==``, so those checks can never pass.

``unique_names`` drops exact copies and renames the clashes that remain
before a run, so the executor keeps every distinct check once, and
``recheck`` compares count checks numerically after it, while the executor's
own verdict stays beside the corrected one as ``upstream_passed``.
``upstream_verdict`` then recovers what the benchmark would have reported for
the task, so both conventions can be shown side by side, and
``verifier_records`` flattens one run's verdicts into the rows of
``verifiers.csv``.
"""

import contextlib
from typing import NamedTuple

from mas_optimiser.benchmarks.enterpriseops import diagnosis

NUMBER_TYPES = (int, float)
COLUMNS = (
    "task_id",
    "attempt",
    "position",
    "name",
    "passed",
    "upstream_passed",
    "comparison",
    "expected",
    "actual",
    "diagnosis",
)


class Names(NamedTuple):
    """How a task's verifiers were kept.

    Attributes:
        original: Each kept verifier's unique name to the name the task gave
            it (``verifier_<n>`` for an unnamed one, as the executor names
            them).
        upstream: Each task-given name to the unique name of the copy the
            executor's own dict would have ended up holding, the last one.
    """

    original: dict[str, str]
    upstream: dict[str, str]


def unique_names(verifiers: list[dict]) -> tuple[list[dict], Names]:
    """Keep each distinct verifier once, under a name no other verifier has.

    An exact copy (same name, query, expected value and comparison) is one
    check, not two, and is dropped. A verifier that only shares its name gets
    the name suffixed with a count, so that the executor keeps it.

    Args:
        verifiers: The task config's verifier dicts, in order.

    Returns:
        Copies of the kept dicts with unique names, and the ``Names`` maps.
    """
    renamed = []
    original = {}
    upstream = {}
    seen = {}
    for i, verifier in enumerate(verifiers, start=1):
        name = verifier.get("name") or f"verifier_{i}"
        check = verifier.get("validation_config", {})
        key = (
            name,
            " ".join(str(check.get("query", "")).split()),
            repr(check.get("expected_value")),
            check.get("comparison_type"),
        )
        if key in seen:
            upstream[name] = seen[key]
            continue
        unique = name
        n = 1
        while unique in original:
            n += 1
            unique = f"{name} ({n})"
        renamed.append(verifier | {"name": unique})
        original[unique] = name
        upstream[name] = unique
        seen[key] = unique
    return renamed, Names(original, upstream)


def recheck(result: dict) -> None:
    """Record the executor's verdict and correct it for a numeric string.

    Sets ``result["upstream_passed"]`` to the verdict as the executor gave it,
    then, for an equality check whose expected value is a number written as a
    string and whose actual value is a number, replaces ``result["passed"]``
    with the numeric comparison.
    """
    result["upstream_passed"] = result["passed"]
    expected, actual = result.get("expected"), result.get("actual")
    if (
        result.get("comparison_type", "equals") == "equals"
        and isinstance(expected, str)
        and isinstance(actual, NUMBER_TYPES)
        and not isinstance(actual, bool)
    ):
        with contextlib.suppress(ValueError):
            result["passed"] = float(actual) == float(expected)


def upstream_verdict(results: dict[str, dict], names: Names) -> tuple[int, int, bool]:
    """What the benchmark's executor would have reported for one run.

    The executor keys verdicts by name, so of verifiers sharing a name only the
    last one counts, and it compares values with ``==``.

    Args:
        results: The run's ``verification_results``, keyed by unique name in
            the order the verifiers ran.
        names: The maps from ``unique_names``.

    Returns:
        Verifiers passed, verifiers counted (the distinct names, whether or
        not the run got as far as running them), and whether the task passed.
    """
    verdicts = {
        name: results[unique]["upstream_passed"]
        for name, unique in names.upstream.items()
        if unique in results
    }
    total = len(names.upstream)
    return (
        sum(verdicts.values()),
        total,
        len(verdicts) == total and all(verdicts.values()),
    )


def verifier_records(
    task_id: str, attempt: int, results: dict[str, dict], names: Names
) -> list[dict]:
    """Flatten one run's verdicts into ``verifiers.csv`` rows (``COLUMNS``), one per verifier."""
    return [
        dict(
            zip(
                COLUMNS,
                (
                    task_id,
                    attempt,
                    position,
                    names.original[unique],
                    result["passed"],
                    result["upstream_passed"],
                    result.get("comparison_type", ""),
                    result.get("expected"),
                    result.get("actual"),
                    diagnosis.describe(result.get("diagnosis")),
                ),
                strict=True,
            )
        )
        for position, (unique, result) in enumerate(results.items(), start=1)
    ]
