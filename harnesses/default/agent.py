"""The starting harness: the benchmark's own ReAct agent, as a file that may be edited.

This file and ``prompt.md`` beside it are one harness: the code around the
model that turns a task into tool calls. The runner loads the class ``Agent``
from here, so a change to this folder changes how the agent works and
nothing else: the tasks, the servers, the checks and the scoring are outside it.

The file is a copy of ``orchestrators/react.py`` from EnterpriseOps-Gym
(ServiceNow, Apache License 2.0; see ``vendor/enterpriseops-gym/LICENSE``) at
the commit named in ``NOTICE.md``. Changed from the original:

- the class is named ``Agent`` and its base class is imported by its full
  name, since this file is loaded by path;
- the text of ``prompt.md`` is added to the end of the system message when
  that file is not empty.

With ``prompt.md`` empty the agent sends and records exactly what the
original does.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, TYPE_CHECKING

from benchmark.mcp_client import MCPClient
from benchmark.llm_client import LLMClient, get_text_content
from benchmark.models import BenchmarkConfig
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from orchestrators.base import AgentOrchestrator

logger = logging.getLogger(__name__)

# Text added to the end of every task's system message; empty adds nothing.
PROMPT = (Path(__file__).parent / "prompt.md").read_text().strip()


class Agent(AgentOrchestrator):

    async def execute(self) -> Dict[str, Any]:
        """Execute the ReAct (Reason-Act-Observe) loop until the LLM stops
        calling tools or max_iterations is reached."""
        system_prompt = self.config.system_prompt
        if PROMPT:
            system_prompt = f"{system_prompt}\n\n{PROMPT}"
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=self.config.user_prompt),
        ]

        conversation_flow = [
            {"type": "system_message", "content": system_prompt},
            {"type": "user_message", "content": self.config.user_prompt},
        ]
        tools_used = []
        tool_results = []

        for iteration in range(self.max_iterations):
            logger.info(f"\n--- Iteration {iteration + 1} ---")

            # Reason: ask the LLM what to do next
            response = await self.llm_client.invoke_with_tools(
                messages, self.available_tools
            )
            reasoning_details = (getattr(response, "additional_kwargs", None) or {}).get(
                "reasoning_details"
            )
            if reasoning_details:
                logger.debug(f"Preserving reasoning_details for next turn ({len(reasoning_details)} items)")

            messages.append(response)

            usage_metadata = (
                response.usage_metadata if hasattr(response, "usage_metadata") else {}
            )
            response_metadata = (
                response.response_metadata
                if hasattr(response, "response_metadata")
                else {}
            )

            conversation_flow.append(
                {
                    "type": "ai_message",
                    "content": get_text_content(response.content),
                    "usage_metadata": usage_metadata,
                    "response_metadata": response_metadata,
                    "tool_calls": [
                        {"name": tc["name"], "args": tc["args"]}
                        for tc in (response.tool_calls or [])
                    ],
                }
            )

            logger.info(f"LLM Response: {get_text_content(response.content)}")

            # Terminate if the LLM decided no further tool calls are needed
            if not response.tool_calls or len(response.tool_calls) == 0:
                logger.info("No tool calls requested. Task complete.")
                break

            # Act + Observe: execute each tool call and feed results back
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                logger.debug(f"Tool arguments: {tool_args}")

                exec_result = await self._execute_tool_call(tool_name, tool_args)
                tool_result = exec_result["result"]
                target_gym = exec_result["gym_server"]

                logger.info(f"Tool result success: {tool_result.get('success')}")

                if tool_name not in tools_used:
                    tools_used.append(tool_name)

                tool_results.append(
                    {
                        "tool_name": tool_name,
                        "arguments": tool_args,
                        "result": tool_result,
                        "gym_server": target_gym,
                    }
                )

                messages.append(
                    ToolMessage(
                        content=json.dumps(tool_result.get("result", {})),
                        tool_call_id=tool_call.get("id", ""),
                    )
                )

                conversation_flow.append(
                    {
                        "type": "tool_result",
                        "tool_name": tool_name,
                        "result": tool_result,
                        "gym_server": target_gym,
                    }
                )

        return {
            "final_response": get_text_content(messages[-1].content) if messages else "",
            "conversation_flow": conversation_flow,
            "tools_used": tools_used,
            "tool_results": tool_results,
            "messages": messages,
        }
