from mas_optimiser.benchmarks.enterpriseops import verifiers


def test_clashing_names_are_made_unique_and_mapped_back():
    renamed, original = verifiers.unique_names(
        [{"name": "Add CI"}, {"name": "Add CI"}, {"name": None}, {"name": "Add CI"}]
    )
    assert [v["name"] for v in renamed] == [
        "Add CI",
        "Add CI (2)",
        "verifier_3",
        "Add CI (3)",
    ]
    assert original == {
        "Add CI": "Add CI",
        "Add CI (2)": "Add CI",
        "verifier_3": "verifier_3",
        "Add CI (3)": "Add CI",
    }


def test_recheck_compares_a_numeric_string_as_a_number():
    result = {
        "passed": False,
        "expected": "1",
        "actual": 1,
        "comparison_type": "equals",
    }
    verifiers.recheck(result)
    assert result["passed"] is True and result["upstream_passed"] is False

    unchanged = {
        "passed": False,
        "expected": "1",
        "actual": 0,
        "comparison_type": "equals",
    }
    verifiers.recheck(unchanged)
    assert unchanged["passed"] is False

    text = {"passed": True, "expected": "in_progress", "actual": "in_progress"}
    verifiers.recheck(text)
    assert text["passed"] is True and text["upstream_passed"] is True


def test_upstream_verdict_keeps_only_the_last_of_a_name():
    renamed, original = verifiers.unique_names(
        [{"name": "a"}, {"name": "a"}, {"name": "b"}]
    )
    results = {
        "a": {"passed": True, "upstream_passed": True},
        "a (2)": {"passed": False, "upstream_passed": False},
        "b": {"passed": True, "upstream_passed": True},
    }
    assert verifiers.upstream_verdict(results, original) == (1, 2, False)
    results["a (2)"]["upstream_passed"] = True
    assert verifiers.upstream_verdict(results, original) == (2, 2, True)
    # A run that raised before the verifiers ran still counts them all.
    assert verifiers.upstream_verdict({}, original) == (0, 2, False)


def test_verifier_records_use_the_original_names_in_order():
    _, original = verifiers.unique_names([{"name": "a"}, {"name": "a"}])
    results = {
        "a": {"passed": True, "upstream_passed": True, "expected": 1, "actual": 1},
        "a (2)": {
            "passed": False,
            "upstream_passed": False,
            "comparison_type": "equals",
            "expected": 1,
            "actual": 0,
        },
    }
    rows = verifiers.verifier_records("t", 2, results, original)
    assert [(r["position"], r["name"], r["passed"]) for r in rows] == [
        (1, "a", True),
        (2, "a", False),
    ]
    assert rows[1] == {
        "task_id": "t",
        "repeat": 2,
        "position": 2,
        "name": "a",
        "passed": False,
        "upstream_passed": False,
        "comparison": "equals",
        "expected": 1,
        "actual": 0,
    }
