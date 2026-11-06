"""Unit tests of ``runner``: task loading, pricing, the rows of an attempt and the evaluation folder."""

import json
from types import SimpleNamespace

import pytest

from mas_optimiser.benchmarks.enterpriseops import GYM_ROOT, checks, runner

# A task the submodule's data/revised folder corrects (its dataset copy attempts
# the same checks four times).
REVISED_ID = "task_20251212_172511_458_e6427839_47076c15"
SEED = "Domain Wise DBs and Task-DB Mappings/itsm/dbs/db_1765301900121_3mwjj54xy.sql"
# Haiku 5.5's rates, in dollars per million tokens.
PRICING = {
    "input": 0.10,
    "output": 0.50,
    "cache_read": 0.01,
    "cache_write": 0.125,
    "long_prompt_tokens": 100_000,
    "long_prompt_multiplier": 5,
}


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
    fields = "llm_provider: anthropic\nllm_model: m\napi_key_env: TEST_KEY\n"
    path.write_text(fields + "pricing:\n" + "".join(f"  {k}: 1\n" for k in PRICING))
    monkeypatch.setenv("TEST_KEY", "secret")
    config, pricing = runner.load_model(path)
    assert config.llm_api_key == "secret" and pricing["input"] == 1
    monkeypatch.delenv("TEST_KEY")
    with pytest.raises(RuntimeError, match="TEST_KEY"):
        runner.load_model(path)
    # A missing price table stops the evaluation before it spends anything.
    path.write_text(fields + "pricing:\n  input: 1\n")
    with pytest.raises(SystemExit, match="cache_read"):
        runner.load_model(path)


def test_cost_prices_cached_and_long_prompts_at_their_own_rates():
    usage = [
        {"input_tokens": 50_000, "output_tokens": 0},
        {
            "input_tokens": 50_000,
            "output_tokens": 10_000,
            "input_token_details": {"cache_read": 20_000, "cache_creation": 10_000},
        },
        # Over the long-prompt threshold: every rate is multiplied.
        {"input_tokens": 200_000, "output_tokens": 0},
    ]
    plain = 50_000 * 0.10
    cached = 20_000 * 0.10 + 20_000 * 0.01 + 10_000 * 0.125 + 10_000 * 0.50
    long_prompt = 5 * 200_000 * 0.10
    assert runner.cost(usage, PRICING) == pytest.approx(
        (plain + cached + long_prompt) / 1e6
    )
    assert runner.cost([], PRICING) == 0.0


def test_task_record_counts_an_errored_run_as_a_failed_task():
    # Two checks share a name: three count here, two in the benchmark's count.
    _, names = checks.unique_names(
        [{"name": "a", "validation_config": {"query": q}} for q in ("q1", "q2")]
        + [{"name": "b", "validation_config": {"query": "q3"}}]
    )
    record = runner.task_record(
        "t", 2, {"run_number": 1, "error": "down"}, names, PRICING
    )
    assert not record["success"] and record["attempt"] == 2
    assert record["cost_usd"] == 0
    assert (record["checks_passed"], record["checks_total"]) == (0, 3)
    assert not record["upstream_success"]
    assert (
        record["upstream_checks_passed"],
        record["upstream_checks_total"],
    ) == (
        0,
        2,
    )
    assert record["error"] == "down"


def test_task_record_counts_every_check_and_sums_the_agents_token_use():
    _, names = checks.unique_names(
        [{"name": "a", "validation_config": {"query": q}} for q in ("q1", "q2")]
        + [{"name": "b", "validation_config": {"query": "q3"}}]
    )
    usage = {"input_tokens": 1000, "output_tokens": 50}
    execution = {
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
    record = runner.task_record("t", 1, execution, names, PRICING)
    # The benchmark keeps only the last "a", which passed, so it calls this a pass.
    assert not record["success"] and record["upstream_success"]
    assert record["cost_usd"] == pytest.approx(2000 * 0.10e-6 + 100 * 0.50e-6)
    assert (record["cache_read_tokens"], record["cache_write_tokens"]) == (0, 0)
    assert (record["checks_passed"], record["checks_total"]) == (2, 3)
    assert (
        record["upstream_checks_passed"],
        record["upstream_checks_total"],
    ) == (
        2,
        2,
    )
    assert record["model_calls"] == 2
    assert (record["input_tokens"], record["output_tokens"]) == (2000, 100)
    assert record["seconds"] == 1.5


def test_task_record_does_not_read_an_excluded_check_as_a_failure():
    _, names = checks.unique_names(
        [{"name": n, "validation_config": {"query": n}} for n in ("a", "b")]
    )
    execution = {
        "run_number": 1,
        "overall_success": False,
        "verification_results": {
            "a": {"passed": True, "upstream_passed": True},
            "b": {"passed": None, "upstream_passed": False, "patch": "excluded"},
        },
        "conversation_flow": [],
    }
    record = runner.task_record("t", 1, execution, names, PRICING)
    assert record["success"] is True
    assert not record["upstream_success"]


def test_evaluate_writes_the_tables_the_settings_and_the_index_row(
    tmp_path, monkeypatch
):
    config = {
        "verifiers": [
            {
                "name": "Incident",
                "validation_config": {
                    "query": "SELECT COUNT(*) FROM incident",
                    "expected_value": 1,
                },
            }
        ],
        "gym_servers_config": [
            {"mcp_server_url": "http://localhost:1", "seed_database_file": "s.sql"}
        ],
    }
    monkeypatch.setattr(
        runner, "load_tasks", lambda domain, mode: {"task_a": config, "task_b": config}
    )
    monkeypatch.setattr(runner, "check_servers", lambda urls: None)
    names = checks.prepare("task_a", config["verifiers"])[1]

    async def fake_run_tasks(tasks, model, pricing, out, **kwargs):
        # Every attempt raised before it started: the end of the evaluation
        # still has to write its tables, settings and index row.
        records = [
            runner.task_record(t, 1, {"run_number": 1, "error": "down"}, names, pricing)
            for t in tasks
        ]
        return records, [], [], []

    monkeypatch.setattr(runner, "run_tasks", fake_run_tasks)
    no_overrides = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    experiment = runner.load_experiment(
        "smoke",
        no_overrides
        | {"out": tmp_path / "exp" / "e1", "limit": None, "task": "task_b"},
    )
    model = SimpleNamespace(llm_model="m", llm_api_key="secret", provider="p")
    runner.evaluate(
        experiment, "itsm", model, PRICING, trace=False, thinking=None, version="v0"
    )
    folder = tmp_path / "exp" / "e1"
    settings = json.loads((folder / "evaluation.json").read_text())
    assert settings["tasks"] == ["task_b"]
    assert (
        settings["totals"]["total_attempts"] == 1 and settings["totals"]["errors"] == 1
    )
    assert "llm_api_key" not in settings["model"]
    assert (folder / "summary.md").exists() and (folder / "tasks.csv").exists()
    index = (tmp_path / "exp" / "index.csv").read_text().splitlines()
    assert index[0].startswith("evaluation,started,domain,mode,model,harness,")
    assert index[1].startswith("e1,") and ",itsm,oracle,m,default," in index[1]
    # tasks 1, attempts 1, total 1, passed 0, every-attempt 0, errors 1, then
    # the summed columns: checks 0 of 1, the benchmark's own 0 and 0 of 1,
    # four token counts, cost and seconds.
    assert index[1].endswith(",v0,1,1,1,0,0,1,0,1,0,0,1,0,0,0,0,0.0,0.0")
