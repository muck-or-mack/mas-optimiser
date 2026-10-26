"""Send benchmark runs to Langfuse, one trace per task.

Langfuse records every model call the agent makes (the messages sent, the
reply, its tool calls, token usage and cost) and shows them as a tree under
one trace per task, so a run can be followed step by step in the browser. The
runner opens a trace for each task, attaches Langfuse's LangChain callback to
the model the benchmark builds, and after the run attaches the verifier
verdicts to the trace as scores, so pass or fail sits beside the transcript.

Tracing is on when the three ``LANGFUSE_*`` variables in ``.env.example`` are
set and off otherwise; nothing here touches the agent's behaviour. The model
object is a LangChain chat model, which carries ``callbacks``, ``tags`` and
``metadata`` fields that every call it makes inherits.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager

from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler

ENV_VARS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")


def configured() -> bool:
    """Whether the environment holds a Langfuse key pair and host."""
    return all(os.environ.get(name) for name in ENV_VARS)


@contextmanager
def task_trace(
    name: str, *, session_id: str, tags: list[str], metadata: dict
) -> Iterator:
    """Open one Langfuse trace for a task run.

    Args:
        name: Trace name, the task ID.
        session_id: Groups the traces of one run folder in the Langfuse UI.
        tags: Filterable labels such as the domain, mode, arm and model.
        metadata: Free-form fields stored on the trace.

    Yields:
        The root span. Model calls made inside the block nest under it, and
        ``span.score_trace`` attaches a verdict to the trace.
    """
    client = get_client()
    with (
        propagate_attributes(
            session_id=session_id, tags=tags, metadata=metadata, trace_name=name
        ),
        client.start_as_current_observation(
            name=name, as_type="span", input=metadata
        ) as span,
    ):
        yield span


def attach(model) -> None:
    """Route a LangChain chat model's calls into the current trace."""
    model.callbacks = [CallbackHandler()]


def shutdown() -> None:
    """Send any queued events; call once at the end of a run."""
    get_client().shutdown()
