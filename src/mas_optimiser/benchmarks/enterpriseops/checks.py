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
KINDS = ("create", "change", "remove", "stay")
COMPARISONS = ("equals", "greater_than", "less_than")
DISPOSITIONS = ("kept", "repaired", "excluded", "weak")
REQUIRED = {
    "text": str,
    "kind": str,
    "table": str,
    "sql": str,
    "expected": (int, float),
    "comparison": str,
}


def source_names(source: object) -> list[str] | None:
    """The benchmark checks a requirement's ``source`` names; ``None`` if it is malformed.

    A source is ``"new"`` (no benchmark check, so no names), one check's exact
    name, or a list of exact names when one requirement carries several checks.
    Names are matched exactly, whitespace included, because benchmark check
    names may contain commas and stray spaces.
    """
    if source == "new":
        return []
    if isinstance(source, str) and source:
        return [source]
    if (
        isinstance(source, list)
        and source
        and all(isinstance(name, str) and name and name != "new" for name in source)
        and len(set(source)) == len(source)
    ):
        return list(source)
    return None


def validate(document: dict) -> list[str]:
    """Name every way a check file departs from the shape; an empty list means none.

    Beyond each field's type, the file must hang together: every check a
    requirement names as its source exists in ``original_checks`` and is not
    excluded, every check kept or repaired is carried by at least one
    requirement, and a ``remove`` requirement counts zero rows.

    Args:
        document: One task's check file, parsed.

    Returns:
        Problems as ``"<where>: <what>"`` strings, in file order.
    """
    problems = []
    originals = document.get("original_checks")
    if not isinstance(originals, dict):
        originals = None
    carried = set()
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
        names = source_names(requirement.get("source"))
        if names is None:
            problems.append(
                f"{where}.source: must be 'new', a check name or a list of check names"
            )
        elif originals is not None:
            for name in names:
                if name not in originals:
                    problems.append(f"{where}.source: names no original check {name!r}")
                elif (originals[name] or {}).get("disposition") == "excluded":
                    problems.append(f"{where}.source: names excluded check {name!r}")
                else:
                    carried.add(name)
        if requirement.get("kind") == "remove" and (
            requirement.get("expected") != 0
            or requirement.get("comparison", "equals") != "equals"
        ):
            problems.append(f"{where}: a remove requirement must count zero rows")
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
    if originals is None:
        problems.append("original_checks: missing or not a mapping")
        originals = {}
    for name, record in originals.items():
        where = f"original_checks[{name!r}]"
        disposition = record.get("disposition") if isinstance(record, dict) else None
        if disposition not in DISPOSITIONS:
            problems.append(f"{where}.disposition: not one of {DISPOSITIONS}")
        elif disposition in ("repaired", "excluded") and not record.get("evidence"):
            problems.append(f"{where}.evidence: required when {disposition}")
        elif disposition in ("kept", "repaired") and name not in carried:
            problems.append(f"{where}: {disposition} but no requirement names it")
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
