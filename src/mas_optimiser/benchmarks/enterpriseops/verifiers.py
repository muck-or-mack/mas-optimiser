"""Count every verifier of a task, and keep the benchmark's own count beside it.

The benchmark's executor runs each task's verifiers (SQL checks on the final
database state) and keeps their verdicts in a dict keyed by verifier name, so
the task passes only if every verdict does. Two details make that count wrong
for the purpose here. Verifiers that share a name overwrite each other in the
dict, so 27 of the 308 oracle tasks lose checks. And 33 count checks in seven
tasks give their expected value as the string ``'1'`` where the SQL runner
returns the integer ``1``; the executor compares the two with ``==``, so those
checks can never pass.

``unique_names`` renames clashing verifiers before a run so the executor keeps
all of them, and ``recheck`` compares count checks numerically after it, while
the executor's own verdict stays beside the corrected one as
``upstream_passed``. ``upstream_verdict`` then recovers what the benchmark
would have reported for the task, so both conventions can be shown side by
side, and ``verifier_records`` flattens one run's verdicts into the rows of
``verifiers.csv``.
"""

import contextlib

from mas_optimiser.benchmarks.enterpriseops import diagnosis

NUMBER_TYPES = (int, float)
COLUMNS = (
    "task_id",
    "repeat",
    "position",
    "name",
    "passed",
    "upstream_passed",
    "comparison",
    "expected",
    "actual",
    "diagnosis",
)


def unique_names(verifiers: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Give every verifier a distinct name, keeping the first of each as it is.

    Args:
        verifiers: The task config's verifier dicts, in order.

    Returns:
        Copies of the dicts with unique names, and a map from each unique name
        back to the name the task gave (``verifier_<n>`` for an unnamed one,
        as the executor names them).
    """
    renamed = []
    original = {}
    for i, verifier in enumerate(verifiers, start=1):
        name = verifier.get("name") or f"verifier_{i}"
        unique = name
        n = 1
        while unique in original:
            n += 1
            unique = f"{name} ({n})"
        renamed.append(verifier | {"name": unique})
        original[unique] = name
    return renamed, original


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


def upstream_verdict(
    results: dict[str, dict], original: dict[str, str]
) -> tuple[int, int, bool]:
    """What the benchmark's executor would have reported for one run.

    The executor keys verdicts by name, so of verifiers sharing a name only the
    last one counts, and it compares values with ``==``.

    Args:
        results: The run's ``verification_results``, keyed by unique name in
            the order the verifiers ran.
        original: The map from ``unique_names``.

    Returns:
        Verifiers passed, verifiers counted (the distinct names, whether or
        not the run got as far as running them), and whether the task passed.
    """
    last = {}
    for unique, result in results.items():
        last[original[unique]] = result["upstream_passed"]
    total = len(set(original.values()))
    return sum(last.values()), total, len(last) == total and all(last.values())


def verifier_records(
    task_id: str, repeat: int, results: dict[str, dict], original: dict[str, str]
) -> list[dict]:
    """Flatten one run's verdicts into ``verifiers.csv`` rows (``COLUMNS``), one per verifier."""
    return [
        dict(
            zip(
                COLUMNS,
                (
                    task_id,
                    repeat,
                    position,
                    original[unique],
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
