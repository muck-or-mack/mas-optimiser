"""Load and validate the project's own check files, one per task, under ``data/checks/``.

The benchmark grades a task mostly with count queries over the final database
state (about 95% of its checks; the rest compare a returned value), and those
checks test only what their authors thought to test. A check file lists
everything the task's request asks of the final state, each requirement as a
count query the cell audit can test, with the benchmark check it came from or
``new`` where none existed, and records by name what became of every
benchmark check: kept as it was, repaired with the evidence that decided it,
excluded as undecidable, or weak because it already held before the agent
acted. The benchmark's checks themselves are not copied in; the runner scores
them from the dataset for the benchmark's own verdict.

``validate`` checks one file against the format described in
``data/checks/README.md``; ``load_checks`` reads and validates a folder of them.
"""

import json
import re
from pathlib import Path

from mas_optimiser.benchmarks.enterpriseops import REPO_ROOT

CHECKS_DIR = REPO_ROOT / "data" / "checks"
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
COUNT_QUERY = re.compile(r"\s*SELECT\s+COUNT\s*\(", re.IGNORECASE)
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


def source_names(source: object) -> list[str] | None:
    """The benchmark check names in a requirement's ``source``, or ``None`` if it is malformed.

    A source is ``"new"`` (no benchmark check, so no names), one check's exact
    name, or a list of distinct names when one requirement carries several
    checks. Names are kept exactly, whitespace included, because benchmark
    check names may contain commas and stray spaces.
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


def validate(document: object) -> list[str]:
    """Name every way a check file departs from the format; an empty list means none.

    Beyond each field's type, the file must hang together: every requirement
    finds its rows by exactly one of an anchor or a selector, every check a
    requirement names as its source exists in ``original_checks`` and is not
    excluded, every check kept or repaired is carried by at least one
    requirement, and a ``remove`` requirement counts zero rows. Malformed
    parts are reported, never raised, so one bad file is named rather than
    stopping the load.

    Args:
        document: One task's check file, parsed.

    Returns:
        Problems as ``"<where>: <what>"`` strings, in file order.
    """
    if not isinstance(document, dict):
        return ["document: not a mapping"]
    problems = []
    originals = document.get("original_checks")
    if not isinstance(originals, dict):
        originals = None
    carried = set()
    # A requirement whose source cannot be read may carry any check, so no
    # check is then reported as uncarried on its account.
    sources_unreadable = False
    for field in ("task_id", "policy_sha1"):
        if not isinstance(document.get(field), str):
            problems.append(f"{field}: missing or not a string")
    requirements = document.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        problems.append("requirements: missing or empty")
        requirements = []
    for i, requirement in enumerate(requirements):
        where = f"requirements[{i}]"
        if not isinstance(requirement, dict):
            problems.append(f"{where}: not a mapping")
            sources_unreadable = True
            continue
        for field, kind in REQUIRED.items():
            if not isinstance(requirement.get(field), kind) or isinstance(
                requirement.get(field), bool
            ):
                problems.append(f"{where}.{field}: missing or wrong type")
        if requirement.get("kind") not in KINDS:
            problems.append(f"{where}.kind: not one of {KINDS}")
        if requirement.get("comparison", "equals") not in COMPARISONS:
            problems.append(f"{where}.comparison: not one of {COMPARISONS}")
        source = requirement.get("source")
        names = source_names(source)
        if names is None:
            problems.append(
                f"{where}.source: must be 'new', one check name, or a list of "
                "distinct check names without 'new'"
            )
            names = [
                name
                for name in (source if isinstance(source, list) else [])
                if isinstance(name, str) and name and name != "new"
            ]
            sources_unreadable = sources_unreadable or not names
        if originals is not None:
            for name in dict.fromkeys(names):
                record = originals.get(name)
                if name not in originals:
                    problems.append(f"{where}.source: names no original check {name!r}")
                elif (
                    isinstance(record, dict) and record.get("disposition") == "excluded"
                ):
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
        anchor, selector = requirement.get("anchor"), requirement.get("selector")
        has_anchor = isinstance(anchor, dict) and bool(anchor)
        has_selector = isinstance(selector, str) and bool(selector.strip())
        if has_anchor == has_selector:
            problems.append(
                f"{where}: needs exactly one of an anchor (the row's key) or a selector"
            )
        # One statement only: anything after a semicolon outside a string
        # literal would be a second statement.
        sql = str(requirement.get("sql", ""))
        body = STRING_LITERAL.sub("''", sql).rstrip().removesuffix(";")
        if not COUNT_QUERY.match(sql) or ";" in body:
            problems.append(f"{where}.sql: must be a single SELECT COUNT(...) query")
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
            continue
        if disposition in ("repaired", "excluded") and not record.get("evidence"):
            problems.append(f"{where}.evidence: required when {disposition}")
        if (
            disposition in ("kept", "repaired")
            and name not in carried
            and not sources_unreadable
        ):
            problems.append(f"{where}: {disposition} but no requirement names it")
    return problems


def load_checks(directory: Path = CHECKS_DIR) -> dict[str, dict]:
    """Read every check file in ``directory``, keyed by task id.

    Raises:
        FileNotFoundError: If ``directory`` does not exist, so a wrong path
            is not mistaken for a folder with no check files.
        ValueError: If any file is not valid JSON, fails ``validate``, or has
            a ``task_id`` that differs from its name; the message names the file.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"no check files folder at {directory}")
    checks = {}
    for path in sorted(directory.glob("task_*.json")):
        try:
            document = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}: not valid JSON ({error})") from error
        problems = validate(document)
        if isinstance(document, dict) and document.get("task_id") != path.stem:
            problems.append(
                f"task_id: {document.get('task_id')!r} is not {path.stem!r}"
            )
        if problems:
            raise ValueError(f"{path}: " + "; ".join(problems))
        checks[path.stem] = document
    return checks
