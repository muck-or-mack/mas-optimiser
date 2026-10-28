import json
import re

import pytest

from mas_optimiser.benchmarks.enterpriseops import checks


def document(**overrides):
    requirement = {
        "text": "log an incident",
        "kind": "create",
        "table": "incident",
        "anchor": {"incident_id": "INC_024"},
        "cells": {"channel": "chat"},
        "sql": "SELECT COUNT(*) FROM incident WHERE incident_id = 'INC_024'",
        "expected": 1,
        "comparison": "equals",
        "source": "Report incident",
        "assumptions": [],
    }
    requirement.update(overrides)
    return {
        "task_id": "task_x",
        "policy_sha1": "4afd943b",
        "requirements": [requirement],
        "original_checks": {"Report incident": {"disposition": "kept"}},
    }


def test_a_well_formed_file_has_no_problems():
    assert checks.validate(document()) == []
    assert checks.validate(document(kind="remove", expected=0)) == []
    assert (
        checks.validate(document(anchor=None, selector="priority = 'moderate'")) == []
    )


@pytest.mark.parametrize(
    "overrides, problem",
    [
        ({"kind": "update"}, "kind: not one of"),
        ({"sql": "SELECT * FROM incident"}, "sql: must be a single SELECT COUNT"),
        ({"sql": "SELECT COUNTRY FROM incident"}, "sql: must be a single"),
        (
            {"sql": "SELECT COUNT(*) FROM incident; DELETE FROM incident"},
            "sql: must be a single",
        ),
        ({"expected": "1"}, "expected: missing or wrong type"),
        ({"expected": True}, "expected: missing or wrong type"),
        ({"comparison": "at_least"}, "comparison: not one of"),
        ({"cells": ["channel"]}, "cells: missing or not a mapping"),
        ({"assumptions": "none"}, "assumptions: must be a list"),
    ],
)
def test_a_bad_requirement_is_named(overrides, problem):
    assert any(problem in p for p in checks.validate(document(**overrides)))


@pytest.mark.parametrize(
    "sql",
    [
        "select count(*) from incident",
        "SELECT\n  COUNT(*) FROM incident WHERE note LIKE '%a; b%'",
        "SELECT COUNT(*) FROM incident;",
    ],
)
def test_a_count_query_in_any_case_or_layout_is_accepted(sql):
    assert checks.validate(document(sql=sql)) == []


@pytest.mark.parametrize(
    "anchor, selector",
    [
        ("absent", "absent"),
        (None, "absent"),
        ({}, "absent"),
        (None, "  "),
        ({"incident_id": "INC_024"}, "priority = 'moderate'"),
    ],
)
def test_a_requirement_needs_exactly_one_of_an_anchor_or_a_selector(anchor, selector):
    doc = document()
    requirement = doc["requirements"][0]
    del requirement["anchor"]
    if anchor != "absent":
        requirement["anchor"] = anchor
    if selector != "absent":
        requirement["selector"] = selector
    assert any("needs exactly one" in p for p in checks.validate(doc))


def test_original_checks_need_a_disposition_and_evidence_when_repaired():
    doc = document()
    doc["original_checks"] = {"Report incident": {"disposition": "repaired"}}
    assert checks.validate(doc) == [
        "original_checks['Report incident'].evidence: required when repaired"
    ]
    doc["original_checks"] = {"Report incident": {"disposition": "fixed"}}
    assert any("disposition" in p for p in checks.validate(doc))


def test_a_requirement_may_carry_several_checks_as_a_list():
    doc = document(source=["Report incident", "Report incident, again "])
    doc["original_checks"]["Report incident, again "] = {"disposition": "kept"}
    assert checks.validate(doc) == []


@pytest.mark.parametrize(
    "source",
    ["", [], ["Report incident", "new"], ["Report incident", "Report incident"], 3],
)
def test_a_malformed_source_is_named(source):
    assert any("source: must be" in p for p in checks.validate(document(source=source)))


def test_a_source_must_name_an_original_check_exactly():
    joined = document(source="Report incident, Add CI")
    assert any("names no original check" in p for p in checks.validate(joined))
    spaced = document(source=" Report incident")
    assert any("names no original check" in p for p in checks.validate(spaced))


def test_a_source_may_not_name_an_excluded_check():
    doc = document(source=["Report incident", "Report incident (2)"])
    doc["original_checks"]["Report incident (2)"] = {
        "disposition": "excluded",
        "evidence": "contradicts the policy default",
    }
    assert any("names excluded check" in p for p in checks.validate(doc))


def test_every_kept_or_repaired_check_must_be_carried():
    doc = document(source="new")
    assert checks.validate(doc) == [
        "original_checks['Report incident']: kept but no requirement names it"
    ]
    doc["original_checks"]["Report incident"] = {"disposition": "weak"}
    assert checks.validate(doc) == []


def test_a_remove_requirement_counts_zero_rows():
    assert checks.validate(document(kind="remove", expected=0.0)) == []
    assert any("count zero rows" in p for p in checks.validate(document(kind="remove")))
    assert any(
        "count zero rows" in p
        for p in checks.validate(
            document(kind="remove", expected=0, comparison="less_than")
        )
    )


@pytest.mark.parametrize("record", [None, "kept", ["kept"], 3])
def test_a_malformed_original_record_is_reported_not_raised(record):
    doc = document()
    doc["original_checks"] = {"Report incident": record}
    assert any("disposition: not one of" in p for p in checks.validate(doc))


def test_malformed_documents_and_requirements_are_reported_not_raised():
    assert checks.validate([]) == ["document: not a mapping"]
    assert checks.validate(None) == ["document: not a mapping"]
    doc = document()
    doc["requirements"] = ["log an incident"]
    assert checks.validate(doc) == ["requirements[0]: not a mapping"]
    doc = document()
    doc["original_checks"] = "not a mapping"
    assert "original_checks: missing or not a mapping" in checks.validate(doc)


def test_an_excluded_check_needs_evidence():
    doc = document(source="new")
    doc["original_checks"] = {"Report incident": {"disposition": "excluded"}}
    assert checks.validate(doc) == [
        "original_checks['Report incident'].evidence: required when excluded"
    ]


def test_a_repaired_check_without_evidence_or_carrier_gets_both_problems():
    doc = document(source="new")
    doc["original_checks"] = {"Report incident": {"disposition": "repaired"}}
    assert checks.validate(doc) == [
        "original_checks['Report incident'].evidence: required when repaired",
        "original_checks['Report incident']: repaired but no requirement names it",
    ]


def test_a_repeated_source_name_gives_one_problem():
    problems = checks.validate(document(source=["Report incident", "Report incident"]))
    assert len(problems) == 1 and "distinct check names" in problems[0]


def test_load_checks_reads_valid_files_and_rejects_a_mismatched_name(tmp_path):
    (tmp_path / "task_x.json").write_text(json.dumps(document()))
    assert list(checks.load_checks(tmp_path)) == ["task_x"]
    (tmp_path / "task_y.json").write_text(json.dumps(document()))
    with pytest.raises(ValueError, match="task_id"):
        checks.load_checks(tmp_path)


@pytest.mark.parametrize(
    "content, problem",
    [
        (
            json.dumps(
                {
                    **document(),
                    "requirements": [
                        {**document()["requirements"][0], "kind": "update"}
                    ],
                }
            ),
            "kind: not one of",
        ),
        ("{not json", "not valid JSON"),
        ("[]", "document: not a mapping"),
    ],
)
def test_load_checks_names_the_file_and_the_problem(tmp_path, content, problem):
    (tmp_path / "task_x.json").write_text(content)
    with pytest.raises(ValueError, match=re.escape(problem)) as raised:
        checks.load_checks(tmp_path)
    assert "task_x.json" in str(raised.value)


def test_the_readme_example_is_a_valid_check_file():
    readme = (checks.CHECKS_DIR / "README.md").read_text()
    example = json.loads(readme.split("```json\n", 1)[1].split("```", 1)[0])
    assert checks.validate(example) == []


def test_load_checks_reads_the_repository_files_from_any_working_directory(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    loaded = checks.load_checks()
    assert len(loaded) == 308
    assert all(task_id.startswith("task_") for task_id in loaded)


def test_load_checks_refuses_a_missing_folder(tmp_path):
    with pytest.raises(FileNotFoundError, match="no check files folder"):
        checks.load_checks(tmp_path / "nowhere")
