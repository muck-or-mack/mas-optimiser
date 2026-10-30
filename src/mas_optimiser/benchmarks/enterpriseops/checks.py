"""Count every distinct check of a task, and keep the benchmark's own count beside it.

The benchmark's executor runs each task's checks (its "verifiers": SQL queries
on the final database state) and keeps their verdicts in a dict keyed by name, so
the task passes only if every verdict does. Three details of the data make
that count wrong for the purpose here. In 59 of the 308 oracle tasks two or
more checks share a name: in 32 of them the list is simply
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
``check_records`` flattens one run's verdicts into the rows of
``checks.csv``.

A few of the benchmark's checks are wrong in themselves: no correct attempt
can satisfy them, or they name the wrong record. ``data/check_patches.json``
lists those, each with the evidence, as excluded or repaired (the query
corrected). ``apply_patches`` takes a task's list through it before the run,
and ``original_verdicts`` runs each patched check's original query afterwards,
so the benchmark's own verdict on every check stays beside the one used here.
"""

import contextlib
import functools
import json
from typing import NamedTuple

from mas_optimiser.benchmarks.enterpriseops import REPO_ROOT, diagnosis

NUMBER_TYPES = (int, float)
PATCHES_FILE = REPO_ROOT / "data" / "check_patches.json"
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
    "kind",
    "table",
    "step",
    "patch",
)


class Names(NamedTuple):
    """How a task's checks were kept.

    Attributes:
        original: Each kept check's unique name to the name the task gave
            it (``verifier_<n>`` for an unnamed one, as the executor names
            them).
        upstream: Each task-given name to the unique name of the copy the
            executor's own dict would have ended up holding, the last one.
        patched: Each patched check's unique name to its disposition and
            the original query, expected value and comparison.
    """

    original: dict[str, str]
    upstream: dict[str, str]
    patched: dict[str, dict] = {}


def unique_names(verifiers: list[dict]) -> tuple[list[dict], Names]:
    """Keep each distinct check once, under a name no other check has.

    An exact copy (same name, query, expected value and comparison) is one
    check, not two, and is dropped. A check that only shares its name gets
    the name suffixed with a count, so that the executor keeps it.

    Args:
        verifiers: The task config's check dicts (its ``verifiers``), in order.

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


@functools.cache
def load_patches() -> dict:
    """The patch list: for each task, its excluded and repaired checks by unique name."""
    return json.loads(PATCHES_FILE.read_text()) if PATCHES_FILE.exists() else {}


def apply_patches(
    task_id: str, kept: list[dict], names: Names
) -> tuple[list[dict], Names]:
    """Drop a task's excluded checks and swap in its repaired queries.

    Args:
        task_id: The task's dataset ID.
        kept: Its checks after ``unique_names``.
        names: The maps from ``unique_names``.

    Returns:
        The checks to run, and ``names`` with ``patched`` filled from the
        originals, so that ``original_verdicts`` can run them afterwards.
    """
    patches = load_patches().get(task_id, {})
    if not patches:
        return kept, names
    patched, to_run = {}, []
    for check in kept:
        patch = patches.get(check["name"])
        if patch is None:
            to_run.append(check)
            continue
        config = check.get("validation_config", {})
        patched[check["name"]] = {
            "disposition": patch["disposition"],
            "evidence": patch.get("evidence", ""),
            "query": config.get("query", ""),
            "expected_value": config.get("expected_value"),
            "comparison_type": config.get("comparison_type", "equals"),
        }
        if patch["disposition"] == "repaired":
            repaired = {
                "query": patch["query"],
                "expected_value": patch["expected_value"],
                "comparison_type": patch["comparison_type"],
            }
            to_run.append(check | {"validation_config": config | repaired})
    return to_run, Names(names.original, names.upstream, patched)


def compare(actual: object, expected: object, comparison: str) -> bool:
    """The executor's own comparison of a check's value with its expected value."""
    try:
        if comparison == "equals":
            return actual == expected
        if comparison == "greater_than":
            return actual > expected  # type: ignore[operator]
        if comparison == "less_than":
            return actual < expected  # type: ignore[operator]
        if comparison == "contains":
            return str(expected) in str(actual)
    except TypeError:
        return False
    return False


async def original_verdicts(results: dict[str, dict], names: Names, runner_for) -> None:
    """Run each patched check's original query and keep the benchmark's verdict beside ours.

    A repaired check's result gets ``upstream_passed`` from its original
    query and ``patch`` "repaired". An excluded check, which did not run,
    gets a result of its own with ``passed`` ``None``, so it is listed but
    counts for neither side's total here and for the benchmark's own verdict
    only.

    Args:
        results: The run's ``verification_results``, edited in place.
        names: The maps from ``apply_patches``.
        runner_for: Gives a query function for a check's name.
    """
    for unique, patch in names.patched.items():
        actual = await runner_for(unique)(patch["query"])
        verdict = compare(actual, patch["expected_value"], patch["comparison_type"])
        if patch["disposition"] == "excluded":
            results[unique] = {
                "passed": None,
                "upstream_passed": verdict,
                "expected": patch["expected_value"],
                "actual": actual,
                "comparison_type": patch["comparison_type"],
                "query": patch["query"],
                "patch": "excluded",
            }
        elif unique in results:
            results[unique]["upstream_passed"] = verdict
            results[unique]["patch"] = "repaired"
            results[unique]["original_query"] = patch["query"]


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

    The executor keys verdicts by name, so of checks sharing a name only the
    last one counts, and it compares values with ``==``.

    Args:
        results: The run's ``verification_results``, keyed by unique name in
            the order the checks ran.
        names: The maps from ``unique_names``.

    Returns:
        Checks passed, checks counted (the distinct names, whether or
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


def check_records(
    task_id: str, attempt: int, results: dict[str, dict], names: Names
) -> list[dict]:
    """Flatten one run's verdicts into ``checks.csv`` rows (``COLUMNS``), one per check.

    A failed verdict carries the ``kind`` and ``table`` of its diagnosis and
    the ``step`` its trace gave it (see ``report``); a passed one leaves them
    empty.
    """
    rows = []
    for position, (unique, result) in enumerate(results.items(), start=1):
        found = result.get("diagnosis") or {}
        rows.append(
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
                        diagnosis.describe(found),
                        found.get("kind", ""),
                        found.get("table", ""),
                        (result.get("trace") or {}).get("step", ""),
                        result.get("patch", ""),
                    ),
                    strict=True,
                )
            )
        )
    return rows
