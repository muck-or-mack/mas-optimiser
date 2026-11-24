"""The model call of the loop's search roles, from the project's model files.

RRSI's analyst, digester, proposer and critic each call one function,
``generate``, which in the released code talks to one provider's hosted
models. The function here has the same arguments and returns the same thing,
and takes its model from a model file (``configs/models/<name>.yaml``)
through the benchmark's own client, so a search role can run on any model
the tasks can run on, a self-hosted one included, and the proposer's model
is set apart from the agent's by naming another file.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``evolve``.
"""

import json
import threading
import time
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

import mas_optimiser.evolve  # noqa: F401  (puts vendor/rrsi on sys.path)
from mas_optimiser.benchmarks.enterpriseops import runner

from benchmark.llm_client import LLMClient, get_text_content
from rrsi import llm as released

# Where each call's tokens and seconds are appended, one JSON object a line,
# so that the search side of an experiment can be costed; ``None`` keeps no log.
usage_log: Path | None = None
# Seconds one try at a call may wait for its answer; the loop sets it from
# the experiment's ``model_timeout``.
timeout: float = 300
# Seconds one call of a role may take in all, its tries and the waits between
# them included; the loop sets it from the experiment's ``attempt_timeout``.
limit: float = 3600
# Clients by model file, built on first use and kept for the process.
_clients: dict = {}


def _ask(client: BaseChatModel, messages: list, outcome: list) -> None:
    """Put the client's answer to the messages, or the error it raised, in ``outcome``.

    Args:
        client: The model client.
        messages: The messages to send.
        outcome: The list the answer or the error is appended to.
    """
    try:
        outcome.append(client.invoke(messages))
    except Exception as error:  # noqa: BLE001 - handed back, to be tried again
        outcome.append(error)


def generate(
    prompt: str,
    system: str | None = None,
    max_retries: int = 6,
    json_only: bool = False,
    model: str | None = None,
    max_tokens: int | None = None,
    cache_prefix: str | None = None,
) -> str:
    """One answer from a model, as text; a stand-in for ``rrsi.llm.generate``.

    Args:
        prompt: The user message.
        system: The system message, if any.
        max_retries: Tries in all before giving up; a try that raises or
            answers with no text is repeated after a growing pause.
        json_only: Ask for a single JSON object and return only that part of
            the answer, as the released function does.
        model: The model file's name or path, as an experiment gives it.
        max_tokens: Ignored: the limit on the answer is the model file's.
        cache_prefix: Text to go before the prompt. The released function
            marks it for the provider's prompt cache; here it is sent as
            plain text, so that every provider is treated alike.

    Returns:
        The answer's text, or its JSON part when ``json_only`` is set.

    Raises:
        RuntimeError: If no try gave an answer, or the call ran past
            ``limit`` seconds.
    """
    if model not in _clients:
        config, _ = runner.load_model(runner.config_path("models", model))
        llm = LLMClient(
            config.llm_provider,
            config.llm_model,
            config.llm_api_key,
            api_endpoint=config.llm_api_endpoint,
            api_version=config.llm_api_version,
            region=config.llm_region,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            effort=config.effort,
            reasoning=config.reasoning,
        ).llm
        # As in ``runner.TaskExecutor.initialize``, for the client these
        # calls go through: the benchmark builds the model with no limit.
        if config.llm_provider in runner.TIMEOUT_PROVIDERS:
            if hasattr(llm, "root_client"):
                llm.root_client.timeout = timeout
            else:
                llm.default_request_timeout = timeout
        if config.max_retries is not None:
            # The model file's tries of a failed call, set as the runner
            # sets them, on the client these calls go through.
            if hasattr(llm, "root_client"):
                llm.root_client.max_retries = config.max_retries
            else:
                llm.max_retries = config.max_retries
        _clients[model] = llm
    messages = [HumanMessage(f"{cache_prefix}\n\n{prompt}" if cache_prefix else prompt)]
    if system or json_only:
        # ``_JSON_SUFFIX`` is the released function's own instruction.
        ending = released._JSON_SUFFIX if json_only else ""
        messages.insert(0, SystemMessage((system or "") + ending))
    failure = "no try was made"
    late = f"no answer within its limit of {limit:g} seconds"
    deadline = time.monotonic() + limit
    for attempt in range(max_retries):
        started = time.monotonic()
        if started >= deadline:
            raise RuntimeError(late)
        # The provider's library repeats a failed call by itself, with no
        # limit on the time that takes in all, so the call runs in a thread
        # that can be left behind: a daemon, which does not hold the command
        # open once the experiment has stopped.
        outcome: list = []
        asking = threading.Thread(
            target=_ask, args=(_clients[model], messages, outcome), daemon=True
        )
        asking.start()
        asking.join(deadline - started)
        if asking.is_alive():
            raise RuntimeError(late)
        answer = outcome[0]
        if isinstance(answer, Exception):
            failure = repr(answer)
            time.sleep(min(2**attempt, 30))
            continue
        if usage_log is not None:
            usage = answer.usage_metadata or {}
            with usage_log.open("a") as f:
                record = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "model": model,
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "seconds": round(time.monotonic() - started, 1),
                }
                f.write(json.dumps(record) + "\n")
        text = get_text_content(answer.content)
        if text:
            return released.extract_json(text) if json_only else text
        failure = "an answer with no text"
    raise RuntimeError(f"no answer after {max_retries} tries: {failure}")
