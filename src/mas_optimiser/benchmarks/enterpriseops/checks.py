"""Load and validate the project's own check files, one per task, under ``data/checks/``.

The benchmark grades a task with count queries over the final database state,
and those queries test only what their authors thought to test. A check file
lists everything the task's request asks of the final state, each requirement
as a predicate the cell audit can test, with the benchmark check it came from
or ``new`` where none existed, and records what became of every benchmark
check: kept as it was, repaired with the evidence that decided it, excluded
as undecidable, or weak because it already held before the agent acted. The
benchmark's own verdict therefore stays recoverable from the same file.

The runner keeps scoring the benchmark's checks until the files are complete;
``validate`` keeps their shape, which ``data/checks/README.md`` describes.
"""

import json
from pathlib import Path

CHECKS_DIR = Path("data") / "checks"
KINDS = ("create", "change", "stay")
COMPARISONS = ("equals", "greater_than", "less_than")
DISPOSITIONS = ("kept", "repaired", "excluded", "weak")
REQUIRED = {
    "text": str,
    "kind": str,
    "table": str,
    "sql": str,
    "expected": (int, float),
    "comparison": str,
    "source": str,
}


def validate(document: dict) -> list[str]:
    """Name every way a check file departs from the shape; an empty list means none.

    Args:
        document: One task's check file, parsed.

    Returns:
        Problems as ``"<where>: <what>"`` strings, in file order.
    """
    problems = []
    for field in ("task_id", "policy_sha1"):
        if not isinstance(document.get(field), str):
            problems.append(f"{field}: missing or not a string")
    requirements = document.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        problems.append("requirements: missing or empty")
        requirements = []
    for i, requirement in enumerate(requirements):
        where = f"requirements[{i}]"
        for field, kind in REQUIRED.items():
            if not isinstance(requirement.get(field), kind) or isinstance(
                requirement.get(field), bool
            ):
                problems.append(f"{where}.{field}: missing or wrong type")
        if requirement.get("kind") not in KINDS:
            problems.append(f"{where}.kind: not one of {KINDS}")
        if requirement.get("comparison", "equals") not in COMPARISONS:
            problems.append(f"{where}.comparison: not one of {COMPARISONS}")
        if not isinstance(requirement.get("cells"), dict):
            problems.append(f"{where}.cells: missing or not a mapping")
        if "anchor" not in requirement and "selector" not in requirement:
            problems.append(f"{where}: needs an anchor (row key) or a selector")
        if (
            not str(requirement.get("sql", ""))
            .lstrip()
            .upper()
            .startswith("SELECT COUNT")
        ):
            problems.append(f"{where}.sql: must be a SELECT COUNT query")
        assumptions = requirement.get("assumptions", [])
        if not isinstance(assumptions, list) or not all(
            isinstance(a, str) for a in assumptions
        ):
            problems.append(f"{where}.assumptions: must be a list of strings")
    originals = document.get("original_checks")
    if not isinstance(originals, dict):
        problems.append("original_checks: missing or not a mapping")
        originals = {}
    for name, record in originals.items():
        where = f"original_checks[{name!r}]"
        disposition = record.get("disposition") if isinstance(record, dict) else None
        if disposition not in DISPOSITIONS:
            problems.append(f"{where}.disposition: not one of {DISPOSITIONS}")
        elif disposition in ("repaired", "excluded") and not record.get("evidence"):
            problems.append(f"{where}.evidence: required when {disposition}")
    return problems


def load_checks(directory: Path = CHECKS_DIR) -> dict[str, dict]:
    """Read every check file in ``directory``, keyed by task id.

    Raises:
        ValueError: If any file fails ``validate`` or its name and ``task_id`` differ.
    """
    checks = {}
    for path in sorted(directory.glob("task_*.json")):
        document = json.loads(path.read_text())
        problems = validate(document)
        if document.get("task_id") != path.stem:
            problems.append(
                f"task_id: {document.get('task_id')!r} is not {path.stem!r}"
            )
        if problems:
            raise ValueError(f"{path}: " + "; ".join(problems))
        checks[path.stem] = document
    return checks
