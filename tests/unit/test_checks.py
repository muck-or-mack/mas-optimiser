import asyncio

from mas_optimiser.benchmarks.enterpriseops import checks


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
    renamed, names = checks.unique_names(
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
    renamed, names = checks.unique_names(
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
    renamed, _ = checks.unique_names([check("a", "q1", 1), check("a", "q1", 0)])
    assert len(renamed) == 2


def test_recheck_compares_a_numeric_string_as_a_number():
    result = {
        "passed": False,
        "expected": "1",
        "actual": 1,
        "comparison_type": "equals",
    }
    checks.recheck(result)
    assert result["passed"] is True and result["upstream_passed"] is False

    unchanged = {
        "passed": False,
        "expected": "1",
        "actual": 0,
        "comparison_type": "equals",
    }
    checks.recheck(unchanged)
    assert unchanged["passed"] is False

    text = {"passed": True, "expected": "in_progress", "actual": "in_progress"}
    checks.recheck(text)
    assert text["passed"] is True and text["upstream_passed"] is True


def test_upstream_verdict_keeps_only_the_last_of_a_name():
    _, names = checks.unique_names(
        [check("a", "q1"), check("a", "q2"), check("b", "q3")]
    )
    results = {
        "a": {"passed": True, "upstream_passed": True},
        "a (2)": {"passed": False, "upstream_passed": False},
        "b": {"passed": True, "upstream_passed": True},
    }
    assert checks.upstream_verdict(results, names) == (1, 2, False)
    results["a (2)"]["upstream_passed"] = True
    assert checks.upstream_verdict(results, names) == (2, 2, True)
    # A run that raised before the checks ran still counts them all.
    assert checks.upstream_verdict({}, names) == (0, 2, False)


def test_check_records_use_the_original_names_in_order():
    _, names = checks.unique_names([check("a", "q1"), check("a", "q2")])
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
    results["a (2)"]["diagnosis"] = {"kind": "no_rows", "table": "t"}
    rows = checks.check_records("t", 2, results, names)
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
        "diagnosis": "no row inserted or updated in t",
        "kind": "no_rows",
        "table": "t",
        "step": "",
        "patch": "",
    }
    assert rows[0]["diagnosis"] == ""


def test_patches_drop_excluded_checks_and_swap_repaired_queries(monkeypatch):
    monkeypatch.setattr(
        checks,
        "load_patches",
        lambda: {
            "t": {
                "a": {
                    "disposition": "excluded",
                    "evidence": "names another seed's record",
                },
                "b (2)": {
                    "disposition": "repaired",
                    "evidence": "wrong id",
                    "query": "q2 fixed",
                    "expected_value": 1,
                    "comparison_type": "equals",
                },
            }
        },
    )
    kept, names = checks.unique_names(
        [check("a", "qa"), check("b", "q1"), check("b", "q2")]
    )
    to_run, names = checks.apply_patches("t", kept, names)
    assert [c["name"] for c in to_run] == ["b", "b (2)"]
    assert to_run[1]["validation_config"]["query"] == "q2 fixed"
    assert names.patched["a"]["disposition"] == "excluded"
    assert names.patched["b (2)"]["query"] == "q2"
    assert names.original == {"a": "a", "b": "b", "b (2)": "b"}

    async def run_sql(query):
        return {"qa": 0, "q2": 0, "q2 fixed": 1}[query]

    results = {
        "b": {"passed": True, "upstream_passed": True},
        "b (2)": {"passed": True, "upstream_passed": True, "query": "q2 fixed"},
    }
    asyncio.run(checks.original_verdicts(results, names, lambda name: run_sql))
    # The repaired check passes here and fails for the benchmark; the excluded
    # one is listed with no verdict of ours.
    assert results["b (2)"]["patch"] == "repaired"
    assert results["b (2)"]["upstream_passed"] is False
    assert results["a"]["passed"] is None and results["a"]["upstream_passed"] is False
    rows = checks.check_records("t", 1, results, names)
    assert [(r["name"], r["passed"], r["patch"]) for r in rows] == [
        ("b", True, ""),
        ("b", True, "repaired"),
        ("a", None, "excluded"),
    ]
