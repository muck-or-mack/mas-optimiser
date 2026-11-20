"""The harness folder: the starting agent and how the runner loads one."""

import asyncio
import runpy
import shutil
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from mas_optimiser.benchmarks.enterpriseops import runner

from orchestrators.react import ReactOrchestrator


def run_agent(agent_class):
    """One scripted task: a tool call, its answer, then a final message."""
    replies = [
        AIMessage(
            content="Looking the user up.",
            tool_calls=[{"name": "get_user", "args": {"id": 7}, "id": "call-1"}],
        ),
        AIMessage(content="Done."),
    ]
    sent = []

    async def invoke_with_tools(messages, tools):
        sent.append(([m.content for m in messages], tools))
        return replies[len(sent) - 1]

    async def call_tool(name, args):
        return {"success": True, "result": {"name": "Ada"}}

    agent = agent_class(
        SimpleNamespace(invoke_with_tools=invoke_with_tools),
        {"itsm": SimpleNamespace(call_tool=call_tool)},
        {"get_user": "itsm"},
        [{"name": "get_user"}],
        SimpleNamespace(system_prompt="The policy.", user_prompt="Find user 7."),
    )
    result = asyncio.run(agent.execute())
    result["messages"] = [m.content for m in result["messages"]]
    return result, sent


def test_the_default_harness_is_the_benchmarks_own_agent():
    agent = runpy.run_path(str(runner.HARNESSES / "default" / "agent.py"))["Agent"]
    assert (runner.HARNESSES / "default" / "prompt.md").read_text() == ""
    # The same exchange through both: the same calls made and the same record.
    assert run_agent(agent) == run_agent(ReactOrchestrator)


def test_text_in_prompt_md_is_added_to_the_system_message(tmp_path):
    folder = tmp_path / "with-text"
    shutil.copytree(runner.HARNESSES / "default", folder)
    (folder / "prompt.md").write_text("Check every argument.\n")
    agent = runpy.run_path(str(folder / "agent.py"))["Agent"]
    result, sent = run_agent(agent)
    system = "The policy.\n\nCheck every argument."
    assert sent[0][0][0] == system
    assert result["conversation_flow"][0] == {
        "type": "system_message",
        "content": system,
    }
    # Nothing else of the record changes.
    plain, _ = run_agent(ReactOrchestrator)
    assert result["conversation_flow"][1:] == plain["conversation_flow"][1:]
    assert result["tool_results"] == plain["tool_results"]


@pytest.mark.parametrize("named", [True, False])
def test_run_loads_the_agent_of_the_harness_it_is_given(tmp_path, monkeypatch, named):
    seen = {}
    monkeypatch.setattr(
        runner, "load_model", lambda path: (SimpleNamespace(llm_provider="vllm"), {})
    )
    monkeypatch.setattr(runner.tracing, "configured", lambda: False)
    monkeypatch.setattr(runner, "evaluate", lambda *args, **kwargs: seen.update(kwargs))
    folder = tmp_path / "copy"
    shutil.copytree(runner.HARNESSES / "default", folder)
    unset = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    chosen = {"experiment": "smoke", "out": tmp_path / "e"}
    if not named:
        # A copy of the folder anywhere is given by its path.
        chosen["harness"] = str(folder)
    runner.run(SimpleNamespace(**(unset | chosen)))
    assert seen["agent"].__name__ == "Agent"
    assert issubclass(seen["agent"], runner.AgentOrchestrator)


def test_a_harness_without_an_agent_file_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    unset = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    chosen = {"experiment": "smoke", "harness": str(tmp_path / "empty")}
    with pytest.raises(SystemExit, match="agent.py.*default"):
        runner.run(SimpleNamespace(**(unset | chosen)))
