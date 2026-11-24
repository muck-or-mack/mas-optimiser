"""Unit tests of ``runner``: task loading, pricing, the rows of an attempt and the evaluation folder."""

import asyncio
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


def test_load_tasks_all_tools_is_the_oracle_dataset_without_the_tool_filter(
    monkeypatch,
):
    configs = []

    def fake_load_dataset(name, config, **kwargs):
        configs.append(config)
        return [
            dataset_row("task_a") | {"selected_tools": ["a", "b"]},
            dataset_row(REVISED_ID),
        ]

    monkeypatch.setattr(runner, "load_dataset", fake_load_dataset)
    tasks = runner.load_tasks("itsm", "all_tools")
    assert configs == ["oracle"]
    assert tasks["task_a"]["selected_tools"] == []
    revised = tasks[REVISED_ID]
    assert len(revised["verifiers"]) == 2 and revised["selected_tools"] == []


def test_save_tools_lists_each_server_once(tmp_path, monkeypatch):
    listed = []

    class FakeClient:
        def __init__(self, base_url, auth_config, mcp_endpoint):
            self.base_url = base_url

        async def connect(self):
            return True

        async def list_tools(self):
            listed.append(self.base_url)
            return [{"name": "get_user", "inputSchema": {}}]

    monkeypatch.setattr(runner, "MCPClient", FakeClient)
    server = {"mcp_server_name": "gym-itsm-mcp", "mcp_server_url": "http://itsm"}
    tasks = {
        "a": {"gym_servers_config": [server]},
        "b": {"gym_servers_config": [server]},
    }
    asyncio.run(runner.save_tools(tmp_path, tasks))
    assert listed == ["http://itsm"]
    tools = json.loads((tmp_path / "tools.json").read_text())
    assert tools == {"http://itsm": [{"name": "get_user", "inputSchema": {}}]}


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


def test_a_model_file_may_set_the_tries_of_a_failed_call(tmp_path):
    path = tmp_path / "model.yaml"
    prices = "pricing:\n" + "".join(f"  {k}: 1\n" for k in PRICING)
    path.write_text("llm_provider: vllm\nllm_model: m\nmax_retries: 7\n" + prices)
    assert runner.load_model(path)[0].max_retries == 7
    # Left out, the provider's library keeps its own number.
    path.write_text("llm_provider: vllm\nllm_model: m\n" + prices)
    assert runner.load_model(path)[0].max_retries is None
    for wrong in ("-1", "2.5", "true", "many"):
        path.write_text(
            f"llm_provider: vllm\nllm_model: m\nmax_retries: {wrong}\n" + prices
        )
        with pytest.raises(SystemExit, match="whole number"):
            runner.load_model(path)
    # A provider whose client is not known here.
    path.write_text("llm_provider: google\nllm_model: m\nmax_retries: 7\n" + prices)
    with pytest.raises(SystemExit, match="built for the providers"):
        runner.load_model(path)


def test_a_model_file_takes_what_belongs_to_an_account_from_the_environment(
    tmp_path, monkeypatch
):
    path = tmp_path / "model.yaml"
    path.write_text(
        "llm_provider: azureopenai\napi_key_env: A_KEY\n"
        "llm_api_endpoint_env: AN_ADDRESS\nllm_model_env: A_DEPLOYMENT\n"
        "pricing:\n" + "".join(f"  {k}: 1\n" for k in PRICING)
    )
    values = {"A_KEY": "secret", "AN_ADDRESS": "https://x/", "A_DEPLOYMENT": "d"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    config, _ = runner.load_model(path)
    assert (config.llm_api_key, config.llm_api_endpoint, config.llm_model) == (
        "secret",
        "https://x/",
        "d",
    )
    # A variable that is not set, or set to nothing, stops the run by name.
    monkeypatch.setenv("AN_ADDRESS", "")
    with pytest.raises(RuntimeError, match="AN_ADDRESS is not set"):
        runner.load_model(path)


def test_the_azure_file_names_four_variables_and_records_no_address(
    tmp_path, monkeypatch
):
    names = ("API_KEY", "ENDPOINT", "DEPLOYMENT", "API_VERSION")
    for name in names:
        monkeypatch.setenv(f"AZURE_OPENAI_{name}", name.lower())
    config, pricing = runner.load_model(runner.config_path("models", "gpt-5.1-azure"))
    assert (config.llm_provider, config.llm_model) == ("azureopenai", "deployment")
    assert (config.llm_api_endpoint, config.llm_api_version) == (
        "endpoint",
        "api_version",
    )
    assert pricing["cache_read"] < pricing["input"] < pricing["output"]
    # More tries than the library's own, for a deployment that limits its rate.
    assert config.max_retries > 2
    # A run on it hands the evaluation the variable's name for the address.
    seen = {}
    monkeypatch.setattr(runner.tracing, "configured", lambda: False)
    monkeypatch.setattr(runner, "evaluate", lambda *args, **kwargs: seen.update(kwargs))
    unset = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    chosen = {"experiment": "smoke", "out": tmp_path / "e", "model": "gpt-5.1-azure"}
    runner.run(SimpleNamespace(**(unset | chosen)))
    assert seen["kept_out"] == {"llm_api_endpoint": "$AZURE_OPENAI_ENDPOINT"}
    assert seen["timeout"] == 300


def test_a_self_hosted_model_file_needs_no_key():
    path = runner.config_path("models", "qwen3.6-27b")
    config, pricing = runner.load_model(path)
    assert (config.llm_provider, config.llm_api_key) == ("vllm", "")
    assert config.llm_api_endpoint == "http://localhost:8027/v1"
    # No price per token: the cost in dollars is zero whatever is used.
    usage = [{"input_tokens": 50_000, "output_tokens": 2_000}]
    assert runner.cost(usage, pricing) == 0


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
        # A write as LangChain reports it when the provider gives its
        # lifetime: under the lifetime key, with the general key at zero.
        {
            "input_tokens": 30_000,
            "output_tokens": 0,
            "input_token_details": {
                "cache_read": 0,
                "cache_creation": 0,
                "ephemeral_5m_input_tokens": 30_000,
                "ephemeral_1h_input_tokens": 0,
            },
        },
    ]
    plain = 50_000 * 0.10
    cached = 20_000 * 0.10 + 20_000 * 0.01 + 10_000 * 0.125 + 10_000 * 0.50
    long_prompt = 5 * 200_000 * 0.10
    written = 30_000 * 0.125
    assert runner.cost(usage, PRICING) == pytest.approx(
        (plain + cached + long_prompt + written) / 1e6
    )
    assert runner.cost([], PRICING) == 0.0


def test_task_outcome_counts_an_errored_run_as_a_failed_task():
    # Two checks share a name: three count here, two in the benchmark's count.
    _, names = checks.unique_names(
        [{"name": "a", "validation_config": {"query": q}} for q in ("q1", "q2")]
        + [{"name": "b", "validation_config": {"query": "q3"}}]
    )
    outcome = runner.task_outcome(
        "t", 2, {"run_number": 1, "error": "down"}, names, PRICING, []
    )
    assert not outcome["success"] and outcome["attempt"] == 2
    assert outcome["cost_usd"] == 0
    assert (outcome["checks_passed"], outcome["checks_total"]) == (0, 3)
    assert not outcome["benchmark_success"]
    assert (
        outcome["benchmark_checks_passed"],
        outcome["benchmark_checks_total"],
    ) == (
        0,
        2,
    )
    assert outcome["error"] == "down"


def test_task_outcome_counts_every_check_and_sums_the_agents_token_use():
    _, names = checks.unique_names(
        [{"name": "a", "validation_config": {"query": q}} for q in ("q1", "q2")]
        + [{"name": "b", "validation_config": {"query": "q3"}}]
    )
    usage = {"input_tokens": 1000, "output_tokens": 50}
    # The second call read 600 tokens from the cache and wrote 300 to it.
    cached = usage | {
        "input_token_details": {
            "cache_read": 600,
            "cache_creation": 0,
            "ephemeral_5m_input_tokens": 300,
        }
    }
    execution = {
        "run_number": 1,
        "overall_success": False,
        "verification_results": {
            "a": {"passed": False, "benchmark_passed": False},
            "a (2)": {"passed": True, "benchmark_passed": True},
            "b": {"passed": True, "benchmark_passed": True},
        },
        "execution_time_ms": 1500,
        "conversation_flow": [
            {"type": "system_message"},
            {"type": "ai_message", "usage_metadata": usage},
            {"type": "tool_result"},
            {"type": "ai_message", "usage_metadata": cached},
        ],
    }
    outcome = runner.task_outcome("t", 1, execution, names, PRICING, [])
    # The benchmark keeps only the last "a", which passed, so it calls this a pass.
    assert not outcome["success"] and outcome["benchmark_success"]
    # 1,100 tokens at the input rate, 600 read, 300 written, 100 out; the
    # outcome keeps six decimals.
    assert outcome["cost_usd"] == pytest.approx(
        (1100 * 0.10 + 600 * 0.01 + 300 * 0.125 + 100 * 0.50) / 1e6, abs=5e-7
    )
    assert (outcome["cache_read_tokens"], outcome["cache_write_tokens"]) == (600, 300)
    assert (outcome["checks_passed"], outcome["checks_total"]) == (2, 3)
    assert (
        outcome["benchmark_checks_passed"],
        outcome["benchmark_checks_total"],
    ) == (
        2,
        2,
    )
    assert outcome["model_calls"] == 2
    assert (outcome["input_tokens"], outcome["output_tokens"]) == (2000, 100)
    assert outcome["seconds"] == 1.5


def test_task_outcome_does_not_read_an_excluded_check_as_a_failure():
    _, names = checks.unique_names(
        [{"name": n, "validation_config": {"query": n}} for n in ("a", "b")]
    )
    execution = {
        "run_number": 1,
        "overall_success": False,
        "verification_results": {
            "a": {"passed": True, "benchmark_passed": True},
            "b": {"passed": None, "benchmark_passed": False, "patch": "excluded"},
        },
        "conversation_flow": [],
    }
    outcome = runner.task_outcome("t", 1, execution, names, PRICING, [])
    assert outcome["success"] is True
    assert not outcome["benchmark_success"]


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
        outcomes = [
            runner.task_outcome(
                t, 1, {"run_number": 1, "error": "down"}, names, pricing, []
            )
            for t in tasks
        ]
        return outcomes, [], [], []

    monkeypatch.setattr(runner, "run_tasks", fake_run_tasks)
    no_overrides = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    experiment = runner.load_experiment(
        "smoke",
        no_overrides
        | {"out": tmp_path / "exp" / "e1", "limit": None, "task": "task_b"},
    )
    model = SimpleNamespace(llm_model="m", llm_api_key="secret", provider="p")
    runner.evaluate(
        experiment,
        "itsm",
        model,
        PRICING,
        run_dir=tmp_path / "exp" / "2026-11-06-0900",
        trace=False,
        thinking=None,
        caching=True,
        timeout=300,
        agent=None,
        version="v0",
        server={"vllm": "1", "args": ["--max-model-len=9"]},
        kept_out={"llm_api_endpoint": "$SOME_ENDPOINT"},
    )
    folder = tmp_path / "exp" / "e1"
    settings = json.loads((folder / "evaluation.json").read_text())
    assert settings["tasks"] == ["task_b"]
    # How a self-hosted model was served is kept with its results.
    assert settings["server"] == {"vllm": "1", "args": ["--max-model-len=9"]}
    assert settings["prompt_caching"] is True
    assert settings["model_timeout"] == 300
    # The limits an attempt is held to are kept with its results.
    assert (settings["attempt_timeout"], settings["attempt_calls"]) == (3600, 100)
    assert settings["harness"] == "default"
    assert (
        settings["totals"]["total_attempts"] == 1 and settings["totals"]["errors"] == 1
    )
    assert "llm_api_key" not in settings["model"]
    # An address that came from the environment is recorded by its variable.
    assert settings["model"]["llm_api_endpoint"] == "$SOME_ENDPOINT"
    assert (folder / "tasks.csv").exists()
    # The summary is headed by the evaluation's name, not by a file's.
    assert (folder / "summary.md").read_text().startswith("# e1\n")
    index = (tmp_path / "exp" / "index.csv").read_text().splitlines()
    assert index[0].startswith("evaluation,run,started,domain,mode,model,harness,")
    assert index[1].startswith("e1,") and ",itsm,all_tools,m,default," in index[1]
    # tasks 1, attempts 1, total 1, passed 0, every-attempt 0, errors 1, then
    # the summed columns: checks 0 of 1, the benchmark's own 0 and 0 of 1,
    # four token counts, cost and seconds.
    assert index[1].endswith(",v0,1,1,1,0,0,1,0,1,0,0,1,0,0,0,0,0.0,0.0")


def test_run_tasks_returns_every_attempt_outcome_and_prints_its_progress(
    tmp_path, monkeypatch, capsys
):
    outcome = {
        "error": None,
        "success": True,
        "checks_passed": 2,
        "checks_total": 3,
        "model_calls": 4,
        "input_tokens": 1200,
        "output_tokens": 50,
        "cost_usd": 0.0012,
        "seconds": 9.6,
    }

    async def fake_run_task(task_id, config, attempt, *args, **kwargs):
        row = dict(outcome, task_id=task_id, attempt=attempt)
        if task_id == "task_b":
            row["error"], row["success"] = "down", False
        return row, [{"task_id": task_id}], [], []

    async def no_tools(out, tasks):
        return None

    monkeypatch.setattr(runner, "run_task", fake_run_task)
    monkeypatch.setattr(runner, "save_tools", no_tools)
    outcomes, rows, cells, tables = asyncio.run(
        runner.run_tasks(
            {"task_a": {}, "task_b": {}},
            None,
            PRICING,
            tmp_path,
            domain="itsm",
            attempts=2,
            concurrency=2,
            trace=False,
            thinking=None,
            caching=False,
            timeout=None,
            agent=None,
            tags=[],
        )
    )
    # The outcome rows come back as rows, in task then attempt order, whatever
    # the progress line said about them.
    assert [(o["task_id"], o["attempt"]) for o in outcomes] == [
        ("task_a", 1),
        ("task_a", 2),
        ("task_b", 1),
        ("task_b", 2),
    ]
    assert len(rows) == 4 and cells == [] and tables == []
    lines = capsys.readouterr().out.splitlines()
    assert sorted(line.split()[0] for line in lines) == [
        f"[{n}/4]" for n in (1, 2, 3, 4)
    ]
    assert any(
        line.endswith(
            "task_a attempt 1 pass: checks 2/3, 4 calls, 1,200 in / 50 out tokens, "
            "$0.0012, 10 s"
        )
        for line in lines
    )
    assert any("task_b attempt 2 error: checks 2/3" in line for line in lines)


def test_task_outcome_counts_tables_by_result_and_tells_an_empty_lookup_from_an_error():
    def step(tool, text):
        return [
            {"type": "ai_message", "tool_calls": [{"name": tool, "args": {}}]},
            {"type": "tool_result", "result": {"success": False, "result": text}},
        ]

    execution = {
        "conversation_flow": step("get_user", "User 9 not found")
        + step("create_incident", "caller_id is required")
    }
    tables = [
        {"checks": 1, "result": "correct"},
        {"checks": 2, "result": "missed"},
        {"checks": 0, "result": "not asked for"},
    ]
    _, names = checks.unique_names([])
    outcome = runner.task_outcome("t", 1, execution, names, PRICING, tables)
    counted = ("tables_checked", "tables_correct", "tables_wrong", "tables_missed")
    assert [outcome[key] for key in counted] == [2, 1, 0, 1]
    assert (outcome["tool_errors"], outcome["lookups_empty"]) == (1, 1)


def test_tool_arguments_come_from_the_saved_tool_list(tmp_path):
    assert runner.tool_arguments(tmp_path) == {}
    listed = [
        {"name": "create_incident", "inputSchema": {"properties": {"a": {}, "b": {}}}},
        {"name": "ping"},
    ]
    (tmp_path / "tools.json").write_text(runner.json.dumps({"http://x": listed}))
    assert runner.tool_arguments(tmp_path) == {
        "create_incident": ["a", "b"],
        "ping": [],
    }


def test_write_tables_counts_each_failure_once_per_attempt(tmp_path):
    def check(task, attempt, table, failure):
        return {
            "task_id": task,
            "attempt": attempt,
            "passed": False,
            "table": table,
            "failure": failure,
        }

    def cell(task, column, how, ok=False):
        return {
            "task_id": task,
            "attempt": 1,
            "table": "incident",
            "column": column,
            "ok": ok,
            "how": how,
        }

    check_rows = [
        check("t1", 1, "incident_task", "table not written"),
        check("t1", 2, "incident_task", "table not written"),
        check("t2", 1, "incident_task", "table not written"),
        check("t1", 1, "notification", "call failed"),
        # A check with wrong values is counted by its cells.
        check("t1", 1, "incident", "wrong values"),
    ]
    cell_rows = [
        # Two touched rows of one attempt with the same wrong column.
        cell("t1", "priority", "left out"),
        cell("t1", "priority", "left out"),
        cell("t2", "priority", "left out"),
        cell("t1", "channel", "passed"),
        cell("t1", "status", "passed", ok=True),
        # A failing group of conditions names no single column.
        cell("t1", "", ""),
    ]
    outcome = {"task_id": "t1", "success": False, "checks_passed": 0, "checks_total": 1}
    runner.write_tables(tmp_path, {}, [outcome], check_rows, cell_rows, [])
    assert (tmp_path / "failures.csv").read_text().splitlines() == [
        "table,column,failure,how,attempts,tasks",
        "incident_task,,table not written,,3,2",
        "incident,priority,wrong value,left out,2,2",
        "incident,channel,wrong value,passed,1,1",
        "notification,,call failed,,1,1",
    ]
    assert (tmp_path / "tables.csv").read_text().splitlines() == [
        ",".join(runner.report.TABLE_COLUMNS)
    ]


def test_write_tables_keeps_the_files_whole_for_a_held_out_set(tmp_path):
    outcome = {"task_id": "t1", "success": False, "checks_passed": 0, "checks_total": 1}
    check_rows = [
        {"task_id": "t1", "attempt": 1, "passed": False, "table": "notification"}
        | {"failure": "call failed"}
    ]
    for split, listed in (
        (None, True),
        ("drawn:training", True),
        ("drawn:test", False),
    ):
        runner.write_tables(tmp_path, {"split": split}, [outcome], check_rows, [], [])
        assert (tmp_path / "failures.csv").read_text().splitlines() == [
            "table,column,failure,how,attempts,tasks",
            "notification,,call failed,,1,1",
        ]
        summary = (tmp_path / "summary.md").read_text()
        assert ("## What went wrong" in summary) is listed


def test_the_cache_mark_goes_on_the_last_tool_and_changes_no_definition():
    from benchmark.llm_client import LLMClient

    model = LLMClient("anthropic", "claude-haiku-5-5", "not-a-key").llm
    # Two tools in the function-call shape the benchmark's client converts to.
    tools = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Does {name}.",
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            },
        }
        for name in ("find_user", "create_incident")
    ]
    marked = runner.mark_tool_list(tools)
    assert marked[0] is tools[0] and "cache_control" not in tools[1]
    # What LangChain sends for each list: the mark survives its conversion,
    # and apart from the mark the model is given the same two definitions.
    plain = model.bind_tools(tools).kwargs["tools"]
    sent = model.bind_tools(marked).kwargs["tools"]
    assert sent[-1].pop("cache_control") == runner.CACHE_MARK
    assert sent == plain
    assert runner.mark_tool_list([]) == []


@pytest.mark.parametrize("caching", [True, False])
def test_the_executor_switches_prompt_caching_on_only_when_asked(monkeypatch, caching):
    from benchmark.llm_client import LLMClient

    async def build_client(self):
        self.llm_client = LLMClient("anthropic", "claude-haiku-5-5", "not-a-key")

    # The benchmark's own initialize connects to the servers; only its model
    # client matters here.
    monkeypatch.setattr(runner.BenchmarkExecutor, "initialize", build_client)
    executor = runner.TaskExecutor.__new__(runner.TaskExecutor)
    executor.trace, executor.thinking, executor.caching = False, None, caching
    executor.timeout = executor.calls = None
    executor.llm_config = SimpleNamespace(max_retries=None)
    asyncio.run(executor.initialize())
    tools = [{"name": "find_user", "description": "Finds a user."}]
    last = executor.llm_client._convert_mcp_tools_to_langchain(tools)[-1]
    if caching:
        assert executor.llm_client.llm.model_kwargs == {
            "cache_control": runner.CACHE_MARK
        }
        assert last["cache_control"] == runner.CACHE_MARK
    else:
        assert executor.llm_client.llm.model_kwargs == {}
        assert "cache_control" not in last and last["type"] == "function"


@pytest.mark.parametrize(
    ("provider", "asked", "used"),
    [("anthropic", None, True), ("anthropic", False, False), ("openai", None, False)],
)
def test_prompt_caching_is_used_for_anthropic_models_only(
    tmp_path, monkeypatch, capsys, provider, asked, used
):
    seen = {}
    monkeypatch.setattr(
        runner, "load_model", lambda path: (SimpleNamespace(llm_provider=provider), {})
    )
    monkeypatch.setattr(runner.tracing, "configured", lambda: False)
    monkeypatch.setattr(runner, "evaluate", lambda *args, **kwargs: seen.update(kwargs))
    # ``mas-optimiser run smoke`` with every option unset but these.
    unset = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    chosen = {"experiment": "smoke", "out": tmp_path / "e", "prompt_caching": asked}
    runner.run(SimpleNamespace(**(unset | chosen)))
    assert seen["caching"] is used
    # Asked for and not available: the run says so.
    said = (
        "Prompt caching is built for Anthropic models only" in capsys.readouterr().out
    )
    assert said is (provider != "anthropic")


@pytest.mark.parametrize("provider", runner.TIMEOUT_PROVIDERS)
@pytest.mark.parametrize("timeout", [45, None])
def test_the_executor_puts_the_time_limit_on_the_model_client(
    monkeypatch, provider, timeout
):
    from benchmark.llm_client import LLMClient

    async def build_client(self):
        self.llm_client = LLMClient(
            provider,
            "a-model",
            "not-a-key",
            api_endpoint="http://localhost:9/v1",
            # Azure OpenAI's client cannot be built without one.
            api_version="2025-01-01",
        )

    monkeypatch.setattr(runner.BenchmarkExecutor, "initialize", build_client)
    executor = runner.TaskExecutor.__new__(runner.TaskExecutor)
    executor.trace, executor.thinking, executor.caching = False, None, False
    executor.timeout, executor.calls = timeout, None
    executor.llm_config = SimpleNamespace(max_retries=None)
    asyncio.run(executor.initialize())
    # The client that sends the calls; with nothing set the benchmark's own
    # model waits without limit, whichever provider it is built for.
    model = executor.llm_client.llm
    client = model._async_client if provider == "anthropic" else model.root_async_client
    assert client.timeout == timeout
    if provider != "anthropic":
        # The calls go through the client that carries the limit.
        assert model.async_client._client is client


@pytest.mark.parametrize("provider", runner.TIMEOUT_PROVIDERS)
@pytest.mark.parametrize("retries", [7, None])
def test_the_executor_puts_the_tries_of_a_failed_call_on_the_model_client(
    monkeypatch, provider, retries
):
    from benchmark.llm_client import LLMClient

    async def build_client(self):
        self.llm_client = LLMClient(
            provider,
            "a-model",
            "not-a-key",
            api_endpoint="http://localhost:9/v1",
            api_version="2025-01-01",
        )

    monkeypatch.setattr(runner.BenchmarkExecutor, "initialize", build_client)
    executor = runner.TaskExecutor.__new__(runner.TaskExecutor)
    executor.trace, executor.thinking, executor.caching = False, None, False
    executor.timeout = executor.calls = None
    executor.llm_config = runner.ModelConfig(
        provider, "a-model", "not-a-key", max_retries=retries
    )
    asyncio.run(executor.initialize())
    model = executor.llm_client.llm
    client = model._async_client if provider == "anthropic" else model.root_async_client
    # With nothing set, each provider's library sends a failed call twice more.
    assert client.max_retries == (2 if retries is None else retries)


def test_the_executor_caps_the_model_calls_of_an_attempt(monkeypatch):
    answered = []

    async def build_client(self):
        async def invoke_with_tools(*args, **kwargs):
            answered.append(args)
            return "an answer"

        self.llm_client = SimpleNamespace(
            llm=SimpleNamespace(), invoke_with_tools=invoke_with_tools
        )

    monkeypatch.setattr(runner.BenchmarkExecutor, "initialize", build_client)
    executor = runner.TaskExecutor.__new__(runner.TaskExecutor)
    executor.trace, executor.thinking, executor.caching = False, None, False
    executor.timeout, executor.calls = None, 2
    executor.llm_config = SimpleNamespace(max_retries=None)
    asyncio.run(executor.initialize())

    async def three_calls():
        client = executor.llm_client
        first = await client.invoke_with_tools("a")
        await client.invoke_with_tools("b")
        with pytest.raises(RuntimeError, match="more than 2 calls to the model"):
            await client.invoke_with_tools("c")
        return first

    # Calls within the cap go through as they were made; the one past it
    # never reaches the model.
    assert asyncio.run(three_calls()) == "an answer" and len(answered) == 2


def test_an_attempt_past_its_seconds_is_ended_and_recorded_as_an_error(
    tmp_path, monkeypatch
):
    class Stuck:
        diagnosis_error, changes = None, None
        names = checks.prepare("task_a", [])[1]
        layout = {"keys": {}}

        def __init__(self, *args, **kwargs):
            pass

        async def execute_benchmark(self):
            await asyncio.sleep(60)

    monkeypatch.setattr(runner, "TaskExecutor", Stuck)
    monkeypatch.setattr(runner, "BenchmarkConfig", lambda **fields: fields)
    monkeypatch.setattr(runner.states, "seed_copy", lambda path, domain: ((), {}))
    monkeypatch.setattr(runner, "save_attempt", lambda *args: None)
    config = {
        "gym_servers_config": [{"seed_database_file": "s.sql"}],
        "selected_tools": [],
    }
    outcome, _, _, _ = asyncio.run(
        runner.run_task(
            "task_a",
            config,
            1,
            SimpleNamespace(),
            PRICING,
            tmp_path,
            domain="itsm",
            trace=False,
            thinking=None,
            caching=False,
            timeout=None,
            agent=None,
            tags=[],
            arguments={},
            seconds=0.05,
        )
    )
    assert outcome["error"] == "the attempt ran past its limit of 0.05 seconds"
    assert outcome["success"] is False


@pytest.mark.parametrize(
    ("provider", "asked", "used"),
    [
        ("anthropic", None, 300),
        ("anthropic", 45.0, 45.0),
        ("vllm", None, 300),
        ("google", None, None),
    ],
)
def test_the_time_limit_is_set_for_the_providers_whose_client_takes_it(
    tmp_path, monkeypatch, capsys, provider, asked, used
):
    seen = {}
    monkeypatch.setattr(
        runner, "load_model", lambda path: (SimpleNamespace(llm_provider=provider), {})
    )
    monkeypatch.setattr(runner.tracing, "configured", lambda: False)
    monkeypatch.setattr(runner, "evaluate", lambda *args, **kwargs: seen.update(kwargs))
    unset = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    chosen = {"experiment": "smoke", "out": tmp_path / "e", "model_timeout": asked}
    runner.run(SimpleNamespace(**(unset | chosen)))
    assert seen["timeout"] == used
    said = "time limit on model calls is built for" in capsys.readouterr().out
    assert said is (provider == "google")


def test_a_filter_setting_that_makes_no_sense_is_refused():
    unset = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    for share in (1, 1.5, -0.1, "half", True):
        with pytest.raises(SystemExit, match="filter_share"):
            runner.load_experiment("smoke", unset | {"filter_share": share})
    for threshold in ("high", [1], True):
        with pytest.raises(SystemExit, match="filter_threshold"):
            runner.load_experiment("smoke", unset | {"filter_threshold": threshold})
    # No filter, and a threshold below zero, are both a user's to choose.
    settings = runner.load_experiment(
        "smoke", unset | {"filter_share": 0, "filter_threshold": -1.5}
    )
    assert (settings["filter_share"], settings["filter_threshold"]) == (0, -1.5)


def test_a_pool_setting_that_makes_no_sense_is_refused():
    unset = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    for share in (0, 1.5, -0.1, "half", True):
        with pytest.raises(SystemExit, match="pool_share"):
            runner.load_experiment("smoke", unset | {"pool_share": share})
    with pytest.raises(SystemExit, match="pool_pick"):
        runner.load_experiment("smoke", unset | {"pool_pick": "random"})
    # Every training task, picked from the two ends, is a user's to choose.
    settings = runner.load_experiment(
        "smoke", unset | {"pool_share": 1, "pool_pick": "ends"}
    )
    assert (settings["pool_share"], settings["pool_pick"]) == (1, "ends")


@pytest.mark.parametrize("limit", [0, -5, "soon", True])
def test_a_time_limit_that_is_not_a_positive_number_is_refused(limit):
    unset = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    with pytest.raises(SystemExit, match="model_timeout"):
        runner.load_experiment("smoke", unset | {"model_timeout": limit})
