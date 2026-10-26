"""Print a saved task record as a readable transcript.

The runner saves the executor's full record of each task under
``runs/<stamp>/tasks/<task_id>.json``: the policy and request the agent was
given, every model message with its tool calls, every tool result and every
verifier's verdict. As one JSON document it is hard to read. This command
prints the record in order, as a transcript, with the verifier table at the
end, so a run can be read without a tracing server.

Usage::

    uv run mas-optimiser replay runs/<stamp>/tasks/<task_id>.json [--full-policy] [--max-chars N]

``python -m mas_optimiser.benchmarks.enterpriseops.replay`` takes the same arguments.
"""

import argparse
import json
from pathlib import Path

POLICY_PREVIEW_LINES = 12
DESCRIPTION = __doc__.split("\n\n")[0]


def format_record(
    record: dict, *, full_policy: bool = False, max_chars: int = 400
) -> str:
    """Render one task record as a transcript.

    Args:
        record: The executor's result for one task, as saved by the runner.
        full_policy: Print the whole policy rather than its first lines.
        max_chars: Longest tool result or model text printed before ``...``.

    Returns:
        The transcript, one run after another.
    """
    task_id = record.get("benchmark_config", {}).get("task_id", "?")
    lines = []
    for run in record["runs"]:
        if run.get("error") and not run.get("conversation_flow"):
            lines.append(
                f"== task {task_id}, run {run.get('run_number', '?')}: "
                f"raised before it started: {run['error']}"
            )
            continue
        summary = run.get("verification_summary", {})
        verdict = "PASS" if run.get("overall_success") else "FAIL"
        lines.append(
            f"== task {task_id}, run {run['run_number']}, "
            f"{run.get('execution_time_ms', 0) / 1000:.1f} s, {verdict} "
            f"({summary.get('passed', 0)}/{summary.get('total', 0)} verifiers)"
        )
        turn = 0
        for step in run["conversation_flow"]:
            kind = step["type"]
            if kind == "system_message":
                policy = step["content"]
                shown = (
                    policy
                    if full_policy
                    else "\n".join(policy.splitlines()[:POLICY_PREVIEW_LINES])
                )
                note = (
                    ""
                    if full_policy
                    else (
                        f"; first {POLICY_PREVIEW_LINES} lines, --full-policy for all"
                    )
                )
                lines.append(f"-- policy ({len(policy):,} chars{note})")
                lines.append(shown)
            elif kind == "user_message":
                lines.append("-- request")
                lines.append(step["content"])
            elif kind == "ai_message":
                turn += 1
                usage = step.get("usage_metadata") or {}
                tokens = (
                    f" ({usage.get('input_tokens', 0):,} in / "
                    f"{usage.get('output_tokens', 0):,} out tokens)"
                    if usage
                    else ""
                )
                calls = step.get("tool_calls") or []
                lines.append(f"-- model turn {turn}{tokens}: {len(calls)} tool call(s)")
                if step.get("content"):
                    lines.append(_clip(step["content"], max_chars))
                for call in calls:
                    lines.append(f"   {call['name']} {json.dumps(call['args'])}")
            elif kind == "tool_result":
                result = step.get("result") or {}
                content = result.get("result", {})
                # MCP servers wrap the payload as content blocks of text.
                if isinstance(content, dict) and content.get("content"):
                    content = content["content"][0].get("text", "")
                lines.append(
                    f"-- tool result: {step['tool_name']} "
                    f"(success={result.get('success')}) {_clip(str(content), max_chars)}"
                )
        lines.append("-- verifiers")
        for name, v in run.get("verification_results", {}).items():
            flag = "PASS" if v.get("passed") else "FAIL"
            lines.append(
                f"   {flag}  {name}: expected {v.get('expected')!r}, "
                f"actual {v.get('actual')!r} ({v.get('comparison_type')})"
            )
            if v.get("query"):
                lines.append(f"         {v['query']}")
        lines.append("")
    return "\n".join(lines)


def _clip(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= max_chars else text[:max_chars] + " ..."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the replay options to ``parser``; shared with the ``mas-optimiser`` command."""
    parser.add_argument("record", type=Path, help="a tasks/<task_id>.json file")
    parser.add_argument(
        "--full-policy", action="store_true", help="print the whole policy"
    )
    parser.add_argument(
        "--max-chars", type=int, default=400, help="clip tool results and model text"
    )


def run(args: argparse.Namespace) -> None:
    """Print the record named by the parsed arguments as a transcript."""
    record = json.loads(args.record.read_text())
    # The executor's record does not carry the task id; the runner names the
    # file after it.
    record.setdefault("benchmark_config", {}).setdefault("task_id", args.record.stem)
    print(format_record(record, full_policy=args.full_policy, max_chars=args.max_chars))


def main() -> None:
    """Command-line entry point; see the module docstring for usage."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    add_arguments(parser)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
