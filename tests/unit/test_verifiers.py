from mas_optimiser.benchmarks.enterpriseops import verifiers


def check(name, query, expected=1):
    return {
        "name": name,
        "validation_config": {
            "query": query,
            "expected_value": expected,
            "comparison_type": "equals",
        },
    }


def test_clashing_names_are_made_unique_and_mapped_back():
    renamed, names = verifiers.unique_names(
        [
            check("Add CI", "q1"),
            check("Add CI", "q2"),
            check(None, "q3"),
            check("Add CI", "q4"),
        ]
    )
    assert [v["name"] for v in renamed] == [
        "Add CI",
        "Add CI (2)",
        "verifier_3",
        "Add CI (3)",
    ]
    assert names.original == {
        "Add CI": "Add CI",
        "Add CI (2)": "Add CI",
        "verifier_3": "verifier_3",
        "Add CI (3)": "Add CI",
    }
    # The executor's dict would end up holding the last "Add CI".
    assert names.upstream == {"Add CI": "Add CI (3)", "verifier_3": "verifier_3"}


def test_exact_copies_are_one_check():
    # A dataset task whose verifier list is repeated: [a, b, a, b, a'] where a'
    # shares a's name but asks something else.
    renamed, names = verifiers.unique_names(
        [
            check("a", "q1"),
            check("b", "q2"),
            check("a", "q1 "),
            check("b", "q2"),
            check("a", "q9"),
        ]
    )
    assert [v["name"] for v in renamed] == ["a", "b", "a (2)"]
    assert names.original == {"a": "a", "b": "b", "a (2)": "a"}
    assert names.upstream == {"a": "a (2)", "b": "b"}
    # The same query with a different expected value is a different check.
    renamed, _ = verifiers.unique_names([check("a", "q1", 1), check("a", "q1", 0)])
    assert len(renamed) == 2


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
    _, names = verifiers.unique_names(
        [check("a", "q1"), check("a", "q2"), check("b", "q3")]
    )
    results = {
        "a": {"passed": True, "upstream_passed": True},
        "a (2)": {"passed": False, "upstream_passed": False},
        "b": {"passed": True, "upstream_passed": True},
    }
    assert verifiers.upstream_verdict(results, names) == (1, 2, False)
    results["a (2)"]["upstream_passed"] = True
    assert verifiers.upstream_verdict(results, names) == (2, 2, True)
    # A run that raised before the verifiers ran still counts them all.
    assert verifiers.upstream_verdict({}, names) == (0, 2, False)


def test_verifier_records_use_the_original_names_in_order():
    _, names = verifiers.unique_names([check("a", "q1"), check("a", "q2")])
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
    results["a (2)"]["diagnosis"] = {"kind": "no_match", "condition": "id = 5"}
    rows = verifiers.verifier_records("t", 2, results, names)
    assert [(r["position"], r["name"], r["passed"]) for r in rows] == [
        (1, "a", True),
        (2, "a", False),
    ]
    assert rows[1] == {
        "task_id": "t",
        "attempt": 2,
        "position": 2,
        "name": "a",
        "passed": False,
        "upstream_passed": False,
        "comparison": "equals",
        "expected": 1,
        "actual": 0,
        "diagnosis": "nothing matches id = 5",
    }
    assert rows[0]["diagnosis"] == ""
