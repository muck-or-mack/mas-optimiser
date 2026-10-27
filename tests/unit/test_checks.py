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
    doc["original_checks"] = {"Add CI": {"disposition": "repaired"}}
    assert checks.validate(doc) == [
        "original_checks['Add CI'].evidence: required when repaired"
    ]
    doc["original_checks"] = {"Add CI": {"disposition": "fixed"}}
    assert any("disposition" in p for p in checks.validate(doc))


def test_load_checks_reads_valid_files_and_rejects_a_mismatched_name(tmp_path):
    (tmp_path / "task_x.json").write_text(json.dumps(document()))
    assert list(checks.load_checks(tmp_path)) == ["task_x"]
    (tmp_path / "task_y.json").write_text(json.dumps(document()))
    with pytest.raises(ValueError, match="task_id"):
        checks.load_checks(tmp_path)
