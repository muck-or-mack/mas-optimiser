import json

import pytest

from mas_optimiser.benchmarks.enterpriseops import GYM_ROOT, runner

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


def test_score_counts_an_errored_run_as_a_failed_task():
    # Two verifiers share a name, so the executor would report two, not three.
    config = {"verifiers": [{"name": "a"}, {"name": "a"}, {"name": "b"}]}
    result = {"runs": [{"run_number": 1, "error": "server down"}]}
    [record] = runner.score("t", config, result)
    assert not record["success"]
    assert (record["verifiers_passed"], record["verifiers_total"]) == (0, 2)
    assert record["error"] == "server down"


def test_score_sums_the_agents_token_use():
    usage = {"input_tokens": 1000, "output_tokens": 50}
    run = {
        "run_number": 1,
        "overall_success": True,
        "verification_summary": {"passed": 2, "total": 2},
        "execution_time_ms": 1500,
        "conversation_flow": [
            {"type": "system_message"},
            {"type": "ai_message", "usage_metadata": usage},
            {"type": "tool_result"},
            {"type": "ai_message", "usage_metadata": usage},
        ],
    }
    [record] = runner.score(
        "t", {"verifiers": [{"name": "a"}, {"name": "b"}]}, {"runs": [run]}
    )
    assert record["success"]
    assert record["model_calls"] == 2
    assert (record["input_tokens"], record["output_tokens"]) == (2000, 100)
    assert record["seconds"] == 1.5
