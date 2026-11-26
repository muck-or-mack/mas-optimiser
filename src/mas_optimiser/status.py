"""A model's connection, or an experiment in progress: ``mas-optimiser status``.

Given a model, it is sent a one-word question, once and with no second try,
and a row is printed for it: whether it answered, the seconds that took and,
where the service states them with its reply, the tokens and the requests it
allows in a minute and what is left of each. Services of the OpenAI style
state them, in a refusal as in an answer; for the others those cells stay
empty. A model that did not answer is shown with what was raised, any
address in it left out.

Given the folder of an experiment of the loop, the files the experiment has
written are read, and nothing is run: the job it is at and how many of that
job's attempts are done, the attempts finished in the last ten minutes and
the last hour, how many of the hour's ended in an error or went unanswered
by a service, the tokens a minute the experiment used over the hour, and how
long ago it last wrote a file. Each model the experiment names is then asked
the one-word question, and the tokens used are set against what its service
allows. Counts and totals only are printed: no task is named.

    mas-optimiser status gpt-5.1-azure haiku-5-5
    mas-optimiser status evolutions/evolve-smoke/haiku-20-40-40

The command ends with an error if a model named on the command line did not
answer; the model of an experiment is reported on and no more.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``status``.
"""

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from tabulate import tabulate

from mas_optimiser.benchmarks.enterpriseops import runner
from mas_optimiser.benchmarks.enterpriseops.domain import EnterpriseOps
from mas_optimiser.evolve import models, report

DESCRIPTION = (
    "the status of a connection or of an experiment: whether each model named "
    "answers and what its service allows; for an experiment's folder, where "
    "it is, its pace and its traffic over the last hour"
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
# Seconds of an experiment's latest traffic that are added up.
RECENT = 3600


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The command's options.

    Args:
        parser: The ``status`` subparser.
    """
    parser.add_argument(
        "target",
        nargs="+",
        help=(
            "a model's name in configs/models/ or a YAML path, or the folder of "
            "an experiment of the loop; several may be given"
        ),
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


def progress(folder: Path) -> dict:
    """Where an experiment of the loop is, and its traffic over the last hour.

    Read from the files the experiment has written: nothing is run and no
    model is asked. The job it is at is the one its newest finished attempt
    belongs to. Tokens are those of the calls that were answered, in attempts
    that have finished and in the search roles' calls, so an attempt still
    going is not yet in them.

    Args:
        folder: The experiment's folder.

    Returns:
        ``experiment``, ``models`` and ``version`` from its settings;
        ``where``, the job it is at in words, or that the test has run;
        ``rounds`` decided ``of`` how many, and the changes ``kept``; that
        job's attempts ``done`` and ``expected`` (``None`` for a job started
        by a version that did not record it); the attempts finished in the
        ``last_10`` minutes and the ``last_60``; of the hour's, those that
        ended in an error (``errors``) and those a service left
        ``unanswered``; the ``tokens`` the hour's attempts and search calls
        used; the ``search_calls`` of the hour; the ``idle_minutes`` since a
        file was last written; and ``rest``, the attempts still to run, the
        least and the most (``None`` for an experiment that did not record
        its sets' sizes, or whose test has run). The least is with every
        candidate still to come dropped at the filter, the most with every
        one kept.
    """
    settings = json.loads((folder / "experiment.json").read_text())
    domain = folder / EnterpriseOps.name
    now = time.time()
    finished = {
        path: path.stat().st_mtime
        for path in domain.glob("jobs/*/*/tasks/*/attempt-*.json")
        if not path.name.endswith(".changes.json")
    }
    recent = [path for path, at in finished.items() if at > now - RECENT]
    tokens = errors = unanswered = 0
    for path in recent:
        try:
            execution = json.loads(path.read_text())["runs"][0]
        except json.JSONDecodeError:
            # Still being written: it is read at the next look.
            continue
        tokens += sum(
            used["input_tokens"] + used["output_tokens"]
            for used in runner.model_usage(execution)
        )
        unanswered += bool(execution.get("service_error"))
        errors += bool(execution.get("error")) and not execution.get("service_error")
    log = folder / "search_usage.jsonl"
    calls = [
        call
        for call in map(
            json.loads, log.read_text().splitlines() if log.is_file() else []
        )
        if call.get("time")
        and time.mktime(time.strptime(call["time"], "%Y-%m-%d %H:%M:%S")) > now - RECENT
    ]
    tokens += sum(call["input_tokens"] + call["output_tokens"] for call in calls)
    newest = max(finished, key=finished.get, default=None)
    job = newest.relative_to(domain / "jobs").parts[0] if newest else None
    planned = domain / "jobs" / str(job) / "job.json"
    planned = json.loads(planned.read_text()) if planned.is_file() else None
    state = domain / "frontier.json"
    state = json.loads(state.read_text()) if state.is_file() else {}
    # The harness's git folders are not what the experiment is doing.
    written = [
        path.stat().st_mtime
        for path in folder.rglob("*")
        if path.is_file()
        and not {"repository", "wt"} & set(path.relative_to(folder).parts)
    ]
    rest = None
    sizes, over = settings.get("tasks"), (domain / "test.csv").is_file()
    if sizes and not over:
        per = {
            stage: settings.get(f"attempts_{stage}") or settings["attempts"]
            for stage in ("training", "filter", "gate", "test")
        }
        training = sizes["training"] * per["training"]
        gate = sizes["validation"] * per["gate"]
        share = settings["filter_share"]
        # With no filter a candidate goes straight to the gate.
        sieve = (
            max(1, round(share * sizes["validation"])) * per["filter"]
            if share
            else gate
        )
        fed = training if settings["feedback"] == "training" else 0
        test = sizes["test"] * per["test"]
        each = settings["candidates"]
        # A round at its least and at its most: the one-task trial, the run
        # on the training tasks where the proposer is told of them, then the
        # filter alone, or every validation task and the kept harness's own
        # run on the training tasks.
        low = each * (1 + fed + sieve)
        high = each * (1 + fed + gate) + (0 if fed else training)
        made = Counter(path.relative_to(domain / "jobs").parts[0] for path in finished)
        decided = max(len(state.get("trajectory", [])) - 1, 0)
        to_come = settings["rounds"] - decided
        left = least = most = 0
        if not state:
            least = most = max(training - made["base_training"], 0) + max(
                gate - made["base_validation"], 0
            )
        if to_come:
            # The round under way has run some of its attempts already.
            under_way = sum(
                count
                for name, count in made.items()
                if re.match(rf"r{decided}[A-Z]", name)
            )
            if planned and job and re.match(rf"r{decided}[A-Z]", job):
                left = planned["tasks"] * planned["attempts"] - made[job]
            least += (left if under_way else low) + (to_come - 1) * low
            most += max(high - under_way, left) + (to_come - 1) * high
        tested = sum(
            count for name, count in made.items() if name.startswith("heldout_")
        )
        kept = state.get("promotions", 0)
        least += max(test * (1 + kept) - tested, 0)
        most += max(test * (1 + kept + to_come) - tested, 0)
        rest = (least, most)
    if over:
        where = "finished: the test has run"
    elif job:
        where = report.described(job, report.kept(folder))[0]
    else:
        where = "no attempt has finished yet"
    label = f" ({settings['label']})" if settings.get("label") else ""
    return {
        "experiment": f"{settings['experiment']} on {settings['split']}{label}",
        "models": list(
            dict.fromkeys(
                name
                for name in (settings["model"], settings.get("proposer_model"))
                if name
            )
        ),
        "version": settings.get("mas_optimiser_version"),
        "where": where,
        "rounds": max(len(state.get("trajectory", [])) - 1, 0),
        "of": settings["rounds"],
        "kept": state.get("promotions", 0),
        "done": sum(
            path.relative_to(domain / "jobs").parts[0] == job for path in finished
        ),
        "expected": planned and planned["tasks"] * planned["attempts"],
        "last_10": sum(at > now - 600 for at in finished.values()),
        "last_60": len(recent),
        "errors": errors,
        "unanswered": unanswered,
        "tokens": tokens,
        "search_calls": len(calls),
        "idle_minutes": round((now - max(written)) / 60),
        "rest": rest,
    }


def run(args: argparse.Namespace) -> None:
    """Print each experiment's status, then one table of the models asked.

    Args:
        args: The parsed command line.

    Raises:
        SystemExit: If a folder named holds no experiment of the loop, or a
            model named on the command line did not answer.
    """
    load_dotenv(runner.REPO_ROOT / ".env")
    folders = [Path(target) for target in args.target if Path(target).is_dir()]
    for folder in folders:
        if not (folder / "experiment.json").is_file():
            raise SystemExit(
                f"error: {folder} holds no experiment of the loop (no experiment.json)"
            )
    states = [progress(folder) for folder in folders]
    names = [target for target in args.target if not Path(target).is_dir()]
    names += [name for state in states for name in state["models"]]
    rows = [ask(name) for name in dict.fromkeys(names)]

    def count(value: str | None) -> str:
        """A number a service gave as text, with its thousands marked.

        Args:
            value: The number as the service gave it, or ``None``.

        Returns:
            The number with commas, or nothing for ``None``.
        """
        return "" if value is None else f"{int(value):,}"

    def clock(minutes: float) -> str:
        """A length of time in hours and minutes.

        Args:
            minutes: The length, in minutes.

        Returns:
            It as ``H h M min``.
        """
        return f"{round(minutes) // 60} h {round(minutes) % 60} min"

    for state in states:
        total = state["expected"]
        # In minutes, at the last hour's pace; not known for a job with no
        # recorded total, or when no attempt finished in the hour.
        left = (
            round(60 * (total - state["done"]) / state["last_60"])
            if total and state["last_60"]
            else None
        )
        allowed = next(
            row["tokens_a_minute"] for row in rows if row["model"] == state["models"][0]
        )
        a_minute = state["tokens"] / (RECENT / 60)
        # Where no range can be given: the experiment is over, or it did
        # not record its sets' sizes, or nothing finished in the hour.
        no_range = (
            "nothing: the experiment has finished"
            if state["where"].startswith("finished")
            else "not known"
        )
        lines = {
            "experiment": state["experiment"],
            "model": ", ".join(state["models"]),
            "code version": state["version"],
            "where it is": state["where"],
            "rounds": (
                f"{state['rounds']} of {state['of']} decided; "
                f"changes kept: {state['kept']}"
            ),
            "this job": (
                f"{state['done']:,} of {total:,} attempts"
                if total
                else f"{state['done']:,} attempts; its total was not recorded "
                "(the job was started by an earlier version)"
            ),
            "attempts finished": (
                f"{state['last_10']} in the last 10 minutes, "
                f"{state['last_60']} in the last hour"
            ),
            "time left for this job": (
                f"{clock(left)} at the last hour's pace"
                if left is not None
                else "not known"
            ),
            # Attempts only: the search roles' own time is not in either.
            "the rest, at the least": (
                f"{clock(60 * state['rest'][0] / state['last_60'])} "
                f"({state['rest'][0]:,} attempts): every candidate still to come "
                "dropped at the filter"
                if state["rest"] and state["last_60"]
                else no_range
            ),
            "the rest, at the most": (
                f"{clock(60 * state['rest'][1] / state['last_60'])} "
                f"({state['rest'][1]:,} attempts): every candidate still to come "
                "kept"
                if state["rest"] and state["last_60"]
                else no_range
            ),
            "last file written": (
                f"{state['idle_minutes']} min ago"
                if state["idle_minutes"]
                else "less than a minute ago"
            ),
            "in the last hour": (
                f"ended in an error: {state['errors']}; "
                f"unanswered by a service: {state['unanswered']}; "
                f"calls by the search roles: {state['search_calls']}"
            ),
            "its traffic": f"{a_minute:,.0f} tokens a minute over the last hour"
            + (
                f", {100 * a_minute / int(allowed):.0f}% of the {int(allowed):,} "
                "its service allows"
                if allowed
                else ""
            ),
        }
        first, *rest = lines.items()
        print(tabulate(rest, headers=first, tablefmt="github"), end="\n\n")
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
    # A model named on the command line is being tested, and its silence is
    # the command's failure; an experiment's model is only reported on.
    silent = [
        row["model"]
        for row in rows
        if not row["answers"] and row["model"] in args.target
    ]
    if silent:
        raise SystemExit(f"error: no answer from {', '.join(silent)}")
