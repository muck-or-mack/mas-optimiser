from mas_optimiser.benchmarks.enterpriseops.replay import format_record

RECORD = {
    "benchmark_config": {"task_id": "task_abc"},
    "runs": [
        {
            "run_number": 1,
            "execution_time_ms": 15101,
            "overall_success": False,
            "verification_summary": {"passed": 0, "total": 2},
            "conversation_flow": [
                {
                    "type": "system_message",
                    "content": "\n".join(f"policy line {i}" for i in range(30)),
                },
                {"type": "user_message", "content": "Benjamin's Wi-Fi keeps dropping"},
                {
                    "type": "ai_message",
                    "content": "",
                    "usage_metadata": {"input_tokens": 1000, "output_tokens": 50},
                    "tool_calls": [
                        {"name": "list_users", "args": {"first_name": "Benjamin"}},
                    ],
                },
                {
                    "type": "tool_result",
                    "tool_name": "list_users",
                    "result": {
                        "success": True,
                        "result": {"content": [{"type": "text", "text": "x" * 1000}]},
                    },
                },
                {
                    "type": "ai_message",
                    "content": "I logged incident INC-000024.",
                    "usage_metadata": {"input_tokens": 2000, "output_tokens": 80},
                    "tool_calls": [],
                },
            ],
            "verification_results": {
                "Add CI": {
                    "passed": False,
                    "expected": 1,
                    "actual": 0,
                    "comparison_type": "equals",
                    "query": "SELECT COUNT(*) FROM configuration_item;",
                },
                "Report incident": {"passed": True, "expected": 1, "actual": 1},
            },
        }
    ],
}


def test_transcript_names_calls_results_and_verdicts():
    text = format_record(RECORD)
    assert "== task task_abc, run 1, 15.1 s, FAIL (0/2 verifiers)" in text
    assert 'list_users {"first_name": "Benjamin"}' in text
    assert "tool result: list_users (success=True)" in text
    assert "I logged incident INC-000024." in text
    assert "FAIL  Add CI: expected 1, actual 0 (equals)" in text
    assert "PASS  Report incident" in text
    assert "SELECT COUNT(*) FROM configuration_item;" in text


def test_policy_preview_and_clipping():
    short = format_record(RECORD, max_chars=100)
    assert "policy line 11" in short and "policy line 12" not in short
    assert "x" * 100 + " ..." in short
    assert "x" * 101 not in short
    assert "policy line 29" in format_record(RECORD, full_policy=True)


def test_run_that_never_started():
    record = {"runs": [{"run_number": 1, "error": "ConnectionError('server down')"}]}
    text = format_record(record)
    assert "raised before it started: ConnectionError('server down')" in text
