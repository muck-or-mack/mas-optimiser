"""Whether a model answers, and what its service allows: ``mas-optimiser status``.

Each model named is sent a one-word question, once and with no second try,
and a row is printed for it: whether it answered, the seconds that took and,
where the service states them with its reply, the tokens and the requests it
allows in a minute and what is left of each. Services of the OpenAI style
state them, in a refusal as in an answer; for the others those cells stay
empty. A model that did not answer is shown with what was raised, any
address in it left out.

    mas-optimiser status gpt-5.1-azure haiku-5-5

The command ends with an error if a model named did not answer.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``status``.
"""

import argparse
import re
import time

from dotenv import load_dotenv
from tabulate import tabulate

from mas_optimiser.benchmarks.enterpriseops import runner
from mas_optimiser.evolve import models

DESCRIPTION = (
    "ask each model named a one-word question: whether it answers, and the "
    "allowance its service states"
)
# The reply headers in which an OpenAI-style service states its allowance.
ALLOWANCE = {
    "tokens_a_minute": "x-ratelimit-limit-tokens",
    "tokens_left": "x-ratelimit-remaining-tokens",
    "requests_a_minute": "x-ratelimit-limit-requests",
    "requests_left": "x-ratelimit-remaining-requests",
}
# Seconds the one question may wait for its answer.
LIMIT = 60


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The command's options.

    Args:
        parser: The ``status`` subparser.
    """
    parser.add_argument(
        "model",
        nargs="+",
        help="model name in configs/models/, or a YAML path; several may be given",
    )


def ask(name: str) -> dict:
    """Ask one model a one-word question and say what came back.

    Args:
        name: The model file's name or path.

    Returns:
        The model's row: ``answers``, the ``seconds`` taken, the four
        ``ALLOWANCE`` values as the service gave them (``None`` where it gave
        none) and ``said``, what was raised if the model did not answer, with
        any address in it left out.
    """
    row = {"model": name, "answers": False, "said": ""}
    started = time.monotonic()
    try:
        llm = models.client(name)
        # One try, so that a refusal is seen and not waited out, and the
        # reply's headers kept where the client can hand them on. This
        # command makes no other call with the client.
        if hasattr(llm, "root_client"):
            llm.root_client.max_retries, llm.root_client.timeout = 0, LIMIT
            llm.include_response_headers = True
        else:
            llm.max_retries, llm.default_request_timeout = 0, LIMIT
        answer = llm.invoke("ping")
    except Exception as error:  # noqa: BLE001 - whatever was raised is the finding
        said = re.sub(r"https?://\S+", "<address>", f"{type(error).__name__}: {error}")
        row["said"] = " ".join(said.split())[:200]
        # A refusal for the allowance being used up still states it.
        headers = getattr(getattr(error, "response", None), "headers", None) or {}
    else:
        headers = (answer.response_metadata or {}).get("headers") or {}
        row["answers"] = True
    row |= {key: headers.get(header) for key, header in ALLOWANCE.items()}
    return row | {"seconds": round(time.monotonic() - started, 1)}


def run(args: argparse.Namespace) -> None:
    """Ask every model named and print the rows as one table.

    Args:
        args: The parsed command line.

    Raises:
        SystemExit: If a model named did not answer.
    """
    load_dotenv(runner.REPO_ROOT / ".env")
    rows = [ask(name) for name in args.model]

    def count(value: str | None) -> str:
        """A number a service gave as text, with its thousands marked.

        Args:
            value: The number as the service gave it, or ``None``.

        Returns:
            The number with commas, or nothing for ``None``.
        """
        return "" if value is None else f"{int(value):,}"

    print(
        tabulate(
            [
                [
                    row["model"],
                    "yes" if row["answers"] else "no",
                    row["seconds"],
                    count(row["tokens_a_minute"]),
                    count(row["tokens_left"]),
                    count(row["requests_a_minute"]),
                    count(row["requests_left"]),
                    row["said"],
                ]
                for row in rows
            ],
            headers=[
                "model",
                "answers",
                "seconds",
                "tokens a minute",
                "left",
                "requests a minute",
                "left",
                "what was raised",
            ],
            tablefmt="github",
            disable_numparse=True,
        )
    )
    silent = [row["model"] for row in rows if not row["answers"]]
    if silent:
        raise SystemExit(f"error: no answer from {', '.join(silent)}")
