import json

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
        ({"sql": "SELECT * FROM incident"}, "sql: must be a SELECT COUNT"),
        ({"expected": "1"}, "expected: missing or wrong type"),
        ({"cells": ["channel"]}, "cells: missing or not a mapping"),
        ({"assumptions": "none"}, "assumptions: must be a list"),
    ],
)
def test_a_bad_requirement_is_named(overrides, problem):
    assert any(problem in p for p in checks.validate(document(**overrides)))


def test_a_requirement_needs_an_anchor_or_a_selector():
    doc = document()
    del doc["requirements"][0]["anchor"]
    assert any("anchor" in p for p in checks.validate(doc))


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


def test_malformed_original_records_do_not_crash_the_source_checks():
    doc = document()
    doc["original_checks"] = {"Report incident": None}
    assert any("disposition" in p for p in checks.validate(doc))
    doc["original_checks"] = "not a mapping"
    assert "original_checks: missing or not a mapping" in checks.validate(doc)


def test_load_checks_reads_valid_files_and_rejects_a_mismatched_name(tmp_path):
    (tmp_path / "task_x.json").write_text(json.dumps(document()))
    assert list(checks.load_checks(tmp_path)) == ["task_x"]
    (tmp_path / "task_y.json").write_text(json.dumps(document()))
    with pytest.raises(ValueError, match="task_id"):
        checks.load_checks(tmp_path)


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
