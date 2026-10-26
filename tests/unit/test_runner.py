import json

import pytest

from mas_optimiser.benchmarks.enterpriseops import GYM_ROOT, runner, verifiers

# A task the submodule's data/revised folder corrects (its dataset copy repeats
# the same verifiers four times).
REVISED_ID = "task_20251212_172511_458_e6427839_47076c15"
SEED = "Domain Wise DBs and Task-DB Mappings/itsm/dbs/db_1765301900121_3mwjj54xy.sql"


def dataset_row(task_id):
    return {
        "task_id": task_id,
        "domain": "itsm",
        "system_prompt": "policy",
        "user_prompt": "request",
        "gym_servers_config": json.dumps(
            [{"mcp_server_name": "gym-itsm-mcp", "seed_database_file": SEED}]
        ),
        "verifiers": json.dumps([{"verifier_type": "database_state"}] * 8),
    }


@pytest.fixture
def fake_dataset(monkeypatch):
    rows = [dataset_row("task_a"), dataset_row(REVISED_ID)]
    monkeypatch.setattr(runner, "load_dataset", lambda *args, **kwargs: rows)


def test_load_tasks_decodes_rows_and_makes_seed_paths_absolute(fake_dataset):
    tasks = runner.load_tasks("itsm", "plus_5_tools")
    assert list(tasks) == ["task_a", REVISED_ID]
    task = tasks["task_a"]
    assert "task_id" not in task and "domain" not in task
    assert len(task["verifiers"]) == 8
    seed = task["gym_servers_config"][0]["seed_database_file"]
    assert seed == str(GYM_ROOT / SEED)


def test_load_tasks_uses_revised_copies_in_oracle_mode_only(fake_dataset):
    assert len(runner.load_tasks("itsm", "oracle")[REVISED_ID]["verifiers"]) == 2
    assert len(runner.load_tasks("itsm", "plus_5_tools")[REVISED_ID]["verifiers"]) == 8


def test_load_model_takes_the_key_from_the_environment(tmp_path, monkeypatch):
    path = tmp_path / "model.yaml"
    path.write_text("llm_provider: anthropic\nllm_model: m\napi_key_env: TEST_KEY\n")
    monkeypatch.setenv("TEST_KEY", "secret")
    assert runner.load_model(path).llm_api_key == "secret"
    monkeypatch.delenv("TEST_KEY")
    with pytest.raises(RuntimeError, match="TEST_KEY"):
        runner.load_model(path)


def test_task_record_counts_an_errored_run_as_a_failed_task():
    # Two verifiers share a name: three count here, two in the benchmark's count.
    _, names = verifiers.unique_names([{"name": "a"}, {"name": "a"}, {"name": "b"}])
    record = runner.task_record("t", 2, {"run_number": 1, "error": "down"}, names)
    assert not record["success"] and record["repeat"] == 2
    assert (record["verifiers_passed"], record["verifiers_total"]) == (0, 3)
    assert not record["upstream_success"]
    assert (
        record["upstream_verifiers_passed"],
        record["upstream_verifiers_total"],
    ) == (
        0,
        2,
    )
    assert record["error"] == "down"


def test_task_record_counts_every_verifier_and_sums_the_agents_token_use():
    _, names = verifiers.unique_names([{"name": "a"}, {"name": "a"}, {"name": "b"}])
    usage = {"input_tokens": 1000, "output_tokens": 50}
    run = {
        "run_number": 1,
        "overall_success": False,
        "verification_results": {
            "a": {"passed": False, "upstream_passed": False},
            "a (2)": {"passed": True, "upstream_passed": True},
            "b": {"passed": True, "upstream_passed": True},
        },
        "execution_time_ms": 1500,
        "conversation_flow": [
            {"type": "system_message"},
            {"type": "ai_message", "usage_metadata": usage},
            {"type": "tool_result"},
            {"type": "ai_message", "usage_metadata": usage},
        ],
    }
    record = runner.task_record("t", 1, run, names)
    # The benchmark keeps only the last "a", which passed, so it calls this a pass.
    assert not record["success"] and record["upstream_success"]
    assert (record["verifiers_passed"], record["verifiers_total"]) == (2, 3)
    assert (
        record["upstream_verifiers_passed"],
        record["upstream_verifiers_total"],
    ) == (
        2,
        2,
    )
    assert record["model_calls"] == 2
    assert (record["input_tokens"], record["output_tokens"]) == (2000, 100)
    assert record["seconds"] == 1.5
