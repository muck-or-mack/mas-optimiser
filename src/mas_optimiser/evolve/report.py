"""What an experiment of the loop found and what it cost: ``mas-optimiser report``.

Reads an experiment's folder (``evolutions/<experiment>/<split>``), whether
the experiment has finished or is still going, and writes a ``report/``
folder inside it:

- ``harnesses.csv``: each harness the experiment kept, the starting one
  first: its scores on the validation and the test tasks, what an attempt
  at a test task used, and its gain on the test tasks over the starting
  harness, task by task, with the gate's test and a 95% confidence interval;
- ``rounds.csv``: each candidate, what it proposed (the proposer's
  hypothesis, the files it edited and the lines it changed), how the
  proposal was made (the proposer's actions), how far it got and the
  numbers of the filter and of the gate;
- ``errors.csv``: each job with attempts that ended in an error, which are
  scored zero, and how many of them ran past a limit;
- ``parts.csv``: the attempts, tokens, dollars and hours of each part of the
  experiment, the agent's and the search roles';
- ``figures/scores``: the current harness's score after each round, as PDF,
  PNG and the CSV it was drawn from;
- ``report.md``: the tables and the figure, for reading.

Nothing is measured here. Every number is read from what the experiment
wrote: the jobs' ``tasks.csv``, ``frontier.json``, each round's
``decisions.json``, ``history.jsonl``, ``test.csv`` and
``search_usage.jsonl``. The gain on the test tasks is the paired comparison
of ``stats.outcomes`` and the test of ``stats.gate``.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``evolve``.
"""

import argparse
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402
from scipy import stats  # noqa: E402
from tabulate import tabulate  # noqa: E402

from mas_optimiser.benchmarks.enterpriseops import runner  # noqa: E402
from mas_optimiser.benchmarks.enterpriseops.domain import EnterpriseOps  # noqa: E402
from mas_optimiser.stats import figures, gate, outcomes  # noqa: E402

DESCRIPTION = (
    "report an experiment of the loop: each harness kept, each round, and "
    "what every part cost"
)
# The parts of an experiment, each with the names of the jobs that make it. A
# kept harness's job on the training tasks is matched with ``_kept`` after its
# name (see ``kept``): with ``feedback: training`` every candidate has one.
PARTS = (
    ("the starting harness on the training tasks", r"base_training"),
    ("the starting harness on the validation tasks", r"base_validation"),
    ("the one-task trial of each candidate", r"r\d+[A-Z]_smoke(_again)?"),
    ("the candidates on the validation tasks", r"r\d+[A-Z](_filter|_more|_rest)?"),
    ("the candidates on the training tasks, for the proposer", r"r\d+[A-Z]_training"),
    ("each harness kept, on the training tasks", r"r\d+[A-Z]_training_kept"),
    ("each harness kept, on the test tasks", r"heldout_t\d+"),
)
# What a job is, by its name: a pattern, the words for it, and where it comes
# in the order of an experiment. The number in a round's job counts from 0 and
# the words count from 1, as the table of rounds does; a kept harness's test
# job is numbered by the round that kept it, the starting harness's being 0.
JOBS = (
    (r"base_(training|validation)", "the starting harness, {} tasks", 0),
    (r"r(\d+)[A-Z]_smoke", "round {}: the one-task trial", 1),
    (r"r(\d+)[A-Z]_smoke_again", "round {}: the one-task trial, given again", 1),
    (r"r(\d+)[A-Z]_filter", "round {}: the filter", 1),
    (r"r(\d+)[A-Z]_more", "round {}: further attempts at the filter's tasks", 1),
    (r"r(\d+)[A-Z]_rest", "round {}: the rest of the validation tasks", 1),
    (r"r(\d+)[A-Z]", "round {}: the validation tasks", 1),
    (r"r(\d+)[A-Z]_training", "round {}: a candidate, training tasks", 1),
    (r"r(\d+)[A-Z]_training_kept", "round {}: the kept harness, training tasks", 1),
    (r"heldout_t0", "the starting harness, test tasks", 2),
    (r"heldout_t(\d+)", "the harness kept in round {}, test tasks", 2),
)
SEARCH = "the search roles: analysis, proposals and reviews"
# Where a candidate that reached no test ended, by the loop's own name for it.
ENDED = {
    "no_proposal": "the proposal, which changed nothing",
    "critic_reject": "the review",
    "smoke_fail": "its one-task trial",
    "filtered_out": "the filter",
    "eval_invalid": "its validation run, which could not be scored",
}


def kept(folder: Path) -> set[str]:
    """The training jobs of the harnesses an experiment kept.

    Args:
        folder: The experiment's folder.

    Returns:
        For each round with an accepted candidate, the name of the training
        job of the one with the highest gain on the validation tasks, which
        is the one the loop keeps.
    """
    names = set()
    for path in (folder / EnterpriseOps.name).glob("r*/decisions.json"):
        accepted = [d for d in json.loads(path.read_text()) if d.get("accept")]
        if accepted:
            best = max(accepted, key=lambda decision: decision["mean"])
            names.add(f"{path.parent.name}{best['variant']}_training")
    return names


def described(job: str, kept_jobs: set[str]) -> tuple[str, int, int]:
    """What a job is, in words, and where it comes in the order of an experiment.

    Args:
        job: The job's name.
        kept_jobs: The training jobs of the harnesses kept, from ``kept``.

    Returns:
        The words for the job, or its name for one of no known kind; then
        its place in the order of ``JOBS`` and its round's number, to sort
        by.
    """
    for pattern, words, order in JOBS:
        match = re.fullmatch(pattern, job + "_kept" * (job in kept_jobs))
        if match:
            named = match.group(1) if match.groups() else ""
            number = int(named) + (order == 1) if named.isdigit() else 0
            return words.format(number if named.isdigit() else named), order, number
    return job, len(JOBS), 0


def attempts(job: Path) -> pd.DataFrame:
    """A job's attempts over its domains, without those a service they need never answered.

    Args:
        job: The job's folder, holding one evaluation folder per domain.

    Returns:
        The rows of the domains' ``tasks.csv``, with the score in percent.
    """
    rows = pd.concat(
        [pd.read_csv(path) for path in sorted(job.glob("*/tasks.csv"))]
        or [pd.DataFrame()]
    )
    if "service_error" in rows:
        rows = rows[rows["service_error"].astype(str) != "True"]
    if len(rows):
        rows = rows.assign(score=100 * rows["checks_passed"] / rows["checks_total"])
    return rows


def parts(folder: Path) -> pd.DataFrame:
    """The attempts, tokens, dollars and hours of each part of an experiment.

    A search role's call is priced from the tokens its line in
    ``search_usage.jsonl`` records, at the rates of the model file it names.

    Args:
        folder: The experiment's folder.

    Returns:
        One row per part that has run, the search roles last, with each
        part's share of the dollars and of the input tokens.
    """
    jobs = folder / EnterpriseOps.name / "jobs"
    kept_jobs = kept(folder)
    rows = []
    for part, pattern in PARTS:
        made = [
            attempts(job)
            for job in sorted(jobs.iterdir())
            if re.fullmatch(pattern, job.name + "_kept" * (job.name in kept_jobs))
        ]
        made = pd.concat(made) if made else pd.DataFrame()
        if len(made):
            rows.append(
                {
                    "part": part,
                    "runs": len(made),
                    "input_tokens": int(made["input_tokens"].sum()),
                    "output_tokens": int(made["output_tokens"].sum()),
                    "dollars": float(made["cost_usd"].sum()),
                    "hours": float(made["seconds"].sum()) / 3600,
                }
            )
    # No file before the first call of a search role.
    usage = folder / "search_usage.jsonl"
    calls = (
        [json.loads(line) for line in usage.read_text().splitlines()]
        if usage.is_file()
        else []
    )
    if calls:
        prices = {
            model: yaml.safe_load(runner.config_path("models", model).read_text())[
                "pricing"
            ]
            for model in {call["model"] for call in calls}
        }
        rows.append(
            {
                "part": SEARCH,
                "runs": len(calls),
                "input_tokens": sum(call["input_tokens"] or 0 for call in calls),
                "output_tokens": sum(call["output_tokens"] or 0 for call in calls),
                "dollars": sum(
                    runner.cost(
                        [
                            {
                                "input_tokens": call["input_tokens"] or 0,
                                "output_tokens": call["output_tokens"] or 0,
                            }
                        ],
                        prices[call["model"]],
                    )
                    for call in calls
                ),
                "hours": sum(call["seconds"] for call in calls) / 3600,
            }
        )
    table = pd.DataFrame(rows)
    for column in ("dollars", "input_tokens"):
        total = table[column].sum()
        table[f"share_of_{column}_percent"] = (
            100 * table[column] / total if total else 0.0
        )
    return table


def rounds(folder: Path) -> pd.DataFrame:
    """Each candidate of each round: what it was, how far it got, and its numbers.

    Args:
        folder: The experiment's folder.

    Returns:
        One row per candidate, in the order they were decided. The gain on
        the training tasks (where the experiment had every candidate run on
        them), the gain on the filter's tasks and the gate's gain, the gain
        its decision needed and the standard error are in percentage points. What the candidate proposed is the kind of each
        of its edits, the proposer's hypothesis (one per edit, numbered,
        when a candidate holds several), the files its change edited, the
        lines it added and removed, and where its diff is kept, from the
        experiment's folder. How the proposal was made is the proposer's own
        record of its drafting: the actions it took, how many of them opened
        a training trace or read a file, how often its saying it was done
        was refused, how often the reviewer sent the proposal back, and the
        edits it declared.
    """
    domain = folder / EnterpriseOps.name
    said = {}
    for line in (domain / "history.jsonl").read_text().splitlines():
        record = json.loads(line)
        # RRSI's record gives a candidate that declared no edit one row all
        # the same, of no kind and for a bundle of none; it is no edit.
        if record.get("edit_id") and record.get("bundle") != 0:
            said.setdefault((record["t"], record["variant"]), []).append(record)
    rows = []
    for path in sorted(
        domain.glob("r*/decisions.json"), key=lambda p: int(p.parent.name[1:])
    ):
        t = int(path.parent.name[1:])
        for decision in json.loads(path.read_text()):
            edits = said.get((t, decision["variant"]), [])
            # One edit is given in the proposer's words as they are; several
            # are numbered, each with its kind.
            hypothesis = " ".join(
                str(edit.get("hypothesis"))
                if len(edits) == 1
                else f"Edit {n} ({edit.get('component')}): {edit.get('hypothesis')}"
                for n, edit in enumerate(edits, start=1)
            )
            gated = "t" in decision
            # The candidate's change as the loop kept it: a unified diff.
            patch = path.parent / decision["variant"] / "diff.patch"
            lines = patch.read_text().splitlines() if patch.is_file() else []
            # The proposer's draftings of this candidate, the first and then
            # one for each time the reviewer sent it back.
            drafts = [
                json.loads(draft.read_text())
                for draft in sorted(
                    patch.parent.glob("proposal*.json"),
                    key=lambda draft: (len(draft.name), draft.name),
                )
            ]
            actions = [
                step.get("action") for draft in drafts for step in draft.get("log", [])
            ]
            finished = sum(draft.get("status") == "done" for draft in drafts)
            # How the candidate did on the training tasks, where the
            # experiment had every candidate run on them.
            fed = patch.parent / "training.json"
            fed = json.loads(fed.read_text()) if fed.is_file() else {}
            rows.append(
                {
                    "round": t + 1,
                    "candidate": decision["variant"],
                    "changed": ", ".join(str(e.get("component")) for e in edits)
                    or None,
                    "ended_at": (
                        ("accepted by the gate" if decision["accept"] else "the gate")
                        if gated
                        else ENDED.get(stopped := decision.get("stopped", ""), stopped)
                    ),
                    "filter_gain_points": (decision.get("filter") or {}).get(
                        "gain_points"
                    ),
                    "training_gain_points": 100 * fed["delta_S_training"]
                    if fed
                    else None,
                    "gain_points": 100 * decision["mean"] if gated else None,
                    "standard_error_points": 100 * decision["se"] if gated else None,
                    # The smallest gain this decision would have kept: the
                    # bar on T times the standard error.
                    "gain_needed_points": 100 * decision["x0"] * decision["se"]
                    if gated
                    else None,
                    "t": decision.get("t"),
                    "bar": decision.get("x0"),
                    "p": decision.get("p"),
                    "false_acceptances_allowed_percent": 100 * decision["level"],
                    "accepted": decision["accept"],
                    "hypothesis": hypothesis or None,
                    "files": ", ".join(
                        line[6:] for line in lines if line.startswith("+++ b/")
                    ),
                    "lines_added": sum(
                        line.startswith("+") and not line.startswith("+++")
                        for line in lines
                    ),
                    "lines_removed": sum(
                        line.startswith("-") and not line.startswith("---")
                        for line in lines
                    ),
                    "diff": str(patch.relative_to(folder)) if lines else None,
                    "actions": len(actions) if drafts else None,
                    "traces_opened": actions.count("read_trace") if drafts else None,
                    "files_read": actions.count("read_file") if drafts else None,
                    "done_refused": actions.count("done") - finished
                    if drafts
                    else None,
                    "sent_back": len(drafts) - 1 if drafts else None,
                    "edits": len(edits) if drafts else None,
                }
            )
    return pd.DataFrame(rows)


def errors(folder: Path) -> pd.DataFrame:
    """The jobs with attempts that ended in an error, which are scored zero.

    An attempt a service it needs never answered is no part of this, as it
    is of no score. The kind of an error is told from the runner's two fixed
    messages (``runner.run_task`` and ``runner.TaskExecutor.initialize``);
    an error's own words may quote a task and are not copied.

    Args:
        folder: The experiment's folder.

    Returns:
        One row per job with such an attempt, in the order of the
        experiment: what the job is, its attempts, those in error, and how
        many of them ran past their seconds, past their calls to the model,
        or ended otherwise.
    """
    rows, kept_jobs = [], kept(folder)
    for job in (folder / EnterpriseOps.name / "jobs").iterdir():
        made = attempts(job)
        if not len(made) or made["error"].isna().all():
            continue
        said = made["error"].dropna().astype(str)
        seconds = int(said.str.startswith("the attempt ran past its limit").sum())
        calls = int(said.str.startswith("the attempt made more than").sum())
        what, place, number = described(job.name, kept_jobs)
        row = {
            "job": job.name,
            "what": what,
            "attempts": len(made),
            "in_error": len(said),
            "past_seconds": seconds,
            "past_calls": calls,
            "other": len(said) - seconds - calls,
        }
        rows.append(((place, number, job.name), row))
    return pd.DataFrame(
        [row for _, row in sorted(rows, key=lambda pair: pair[0])],
        columns=[
            "job",
            "what",
            "attempts",
            "in_error",
            "past_seconds",
            "past_calls",
            "other",
        ],
    )


def harnesses(folder: Path) -> pd.DataFrame:
    """Each harness the experiment kept, with its scores and its gain on the test tasks.

    The gain is the mean over the test tasks of a task's score with the
    harness less its score with the starting harness, with the gate's
    one-sided test at 5% and a two-sided 95% confidence interval from the
    same standard error.

    Args:
        folder: The experiment's folder, after its test step.

    Returns:
        One row per harness, the starting one first; its gain columns are
        empty. Scores, gains and intervals are in percentage points.
    """
    domain = folder / EnterpriseOps.name
    kept = pd.read_csv(domain / "test.csv")
    rows = []
    for row in kept.itertuples():
        job = domain / "jobs" / f"heldout_t{row.round}"
        made = attempts(job)
        entry = {
            "after_round": row.round,
            "commit": row.commit,
            "validation_score": 100 * row.validation,
            "test_score": 100 * row.test,
            "test_attempts_fully_passed_percent": 100
            * made["success"].astype(str).eq("True").mean(),
            "test_input_tokens_an_attempt": made["input_tokens"].mean(),
            "test_dollars_an_attempt": made["cost_usd"].mean(),
            "test_seconds_an_attempt": made["seconds"].mean(),
            "test_gain_points": None,
            "test_gain_interval_low": None,
            "test_gain_interval_high": None,
            "test_gain_p": None,
            "test_tasks": made["task_id"].nunique(),
        }
        if row.round != kept["round"].iloc[0]:
            start = domain / "jobs" / f"heldout_t{kept['round'].iloc[0]}"
            compared = outcomes.paired(
                sorted(p.parent for p in start.glob("*/tasks.csv")),
                sorted(p.parent for p in job.glob("*/tasks.csv")),
                "checks",
            )
            test = gate.mean_test(compared.d)
            around = stats.t.ppf(0.975, test.n - 1) * test.se
            entry |= {
                "test_gain_points": 100 * test.mean,
                "test_gain_interval_low": 100 * (test.mean - around),
                "test_gain_interval_high": 100 * (test.mean + around),
                "test_gain_p": test.p,
            }
        rows.append(entry)
    return pd.DataFrame(rows)


def draw_gain(kept: pd.DataFrame, out: Path) -> None:
    """The gain of each harness kept over the starting one, on the test tasks.

    A row for each harness kept: a point at its gain and a bar over the 95%
    confidence interval of that gain, which is from the paired differences
    over the test tasks. A bar that lies wholly to the right of the no-gain
    line is a gain at that level. A row's label gives the round after which
    the harness was the current one and its test score beside the starting
    harness's. The numbers drawn are written beside the figure.

    Args:
        kept: The harnesses, as ``harnesses.csv`` holds them, the test step
            having run.
        out: The folder to write ``test_gain.pdf``, ``test_gain.png`` and
            ``test_gain.csv`` into.
    """
    start = kept["test_score"].iloc[0]
    rows = kept[kept["test_gain_points"].notna()].iloc[::-1]
    rows[
        ["after_round", "test_score", "test_gain_points", "test_gain_interval_low"]
        + ["test_gain_interval_high", "test_gain_p", "test_tasks"]
    ].to_csv(out / "test_gain.csv", index=False)
    gain = rows["test_gain_points"].to_numpy()
    with plt.style.context(figures.STYLE):
        fig, ax = plt.subplots(figsize=(3.4, 0.5 * len(rows) + 0.8))
        ax.axvline(0, color=figures.REFERENCE, linewidth=0.8)
        ax.text(
            0,
            1.01,
            "no gain",
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="bottom",
            color=figures.MUTED,
            fontsize=7,
        )
        ax.errorbar(
            gain,
            range(len(rows)),
            xerr=[
                gain - rows["test_gain_interval_low"].to_numpy(),
                rows["test_gain_interval_high"].to_numpy() - gain,
            ],
            fmt="o",
            color=figures.OBSERVED,
            capsize=2,
        )
        for y, value in enumerate(gain):
            ax.annotate(
                f"{value:+.1f}",
                (value, y),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                color=figures.INK,
                fontsize=7,
            )
        ax.set_yticks(
            range(len(rows)),
            [
                f"after round {after}\n{start:.1f}% to {score:.1f}%"
                for after, score in zip(
                    rows["after_round"], rows["test_score"], strict=True
                )
            ],
        )
        ax.set_ylim(-0.6, len(rows) - 0.3)
        ax.set_xlabel(
            "gain on the test tasks over the starting harness\n"
            "[percentage points, 95% confidence interval]"
        )
        for kind in ("pdf", "png"):
            fig.savefig(out / f"test_gain.{kind}")
        plt.close(fig)


def draw_gate(decided: pd.DataFrame, out: Path) -> None:
    """Each round's candidate at the gate: the gain it found against the gain it needed.

    For each candidate that reached the gate, a point at its gain on the
    validation tasks with one standard error either side, and a short line at
    the gain its decision needed, the bar times the standard error. A
    candidate is kept when its point is above its line; a kept one is filled
    and in the accent, one turned down is hollow. A candidate dropped at the
    filter had no test at the gate: it is marked with a cross at its gain on
    the filter's tasks, a smaller set. A candidate that ended before either
    has no mark. The numbers drawn are written beside the figure.

    Args:
        decided: The rounds, as ``rounds.csv`` holds them.
        out: The folder to write ``gate.pdf``, ``gate.png`` and ``gate.csv``
            into.
    """
    decided[
        ["round", "ended_at", "accepted", "filter_gain_points", "gain_points"]
        + ["standard_error_points", "gain_needed_points"]
    ].to_csv(out / "gate.csv", index=False)
    gated = decided[decided["gain_points"].notna()]
    dropped = decided[
        decided["gain_points"].isna() & decided["filter_gain_points"].notna()
    ]
    with plt.style.context(figures.STYLE):
        fig, ax = plt.subplots(figsize=(3.4, 2.6))
        ax.axhline(0, color=figures.REFERENCE, linewidth=0.8)
        for accepted, colour, face, label in (
            (True, figures.OBSERVED, figures.OBSERVED, "kept"),
            (False, figures.INK, "white", "turned down at the gate"),
        ):
            rows = gated[gated["accepted"].astype(bool) == accepted]
            if len(rows):
                ax.errorbar(
                    rows["round"],
                    rows["gain_points"],
                    yerr=rows["standard_error_points"],
                    fmt="o",
                    color=colour,
                    markerfacecolor=face,
                    capsize=2,
                    label=label,
                )
        if len(gated):
            ax.scatter(
                gated["round"],
                gated["gain_needed_points"],
                marker="_",
                s=140,
                color=figures.THEORY,
                label="gain needed",
                zorder=3,
            )
        if len(dropped):
            ax.scatter(
                dropped["round"],
                dropped["filter_gain_points"],
                marker="x",
                color=figures.MUTED,
                label="dropped at the filter",
            )
        ax.set_xticks(decided["round"])
        ax.set_xlabel("round")
        ax.set_ylabel(
            "gain on the validation tasks\n[percentage points, ± 1 standard error]"
        )
        # Above the plot, where it covers no point and takes none of the
        # plot's width, and in the order read.
        order = ["kept", "turned down at the gate", "gain needed"]
        order += ["dropped at the filter"]
        handles, labels = ax.get_legend_handles_labels()
        placed = sorted(
            zip(labels, handles, strict=True), key=lambda p: order.index(p[0])
        )
        fig.legend(
            [handle for _, handle in placed],
            [label for label, _ in placed],
            frameon=False,
            loc="outside upper center",
            ncol=2,
        )
        for kind in ("pdf", "png"):
            fig.savefig(out / f"gate.{kind}")
        plt.close(fig)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``report`` options to ``parser``.

    Args:
        parser: The ``report`` subparser.
    """
    parser.add_argument(
        "folder",
        type=Path,
        help="the experiment's folder, evolutions/<experiment>/<split>",
    )


def run(args: argparse.Namespace) -> None:
    """Write the report of the experiment in the folder given.

    Args:
        args: The parsed arguments.

    Raises:
        SystemExit: If the folder holds no experiment of the loop, or one
            whose starting harness has not yet been scored.
    """
    folder = args.folder
    domain = folder / EnterpriseOps.name
    if not (domain / "frontier.json").is_file():
        raise SystemExit(
            f"error: nothing to report in {folder}: no experiment of the loop "
            "is there, or its starting harness has not yet been scored (no "
            f"{EnterpriseOps.name}/frontier.json)"
        )
    out = folder / "report"
    (out / "figures").mkdir(parents=True, exist_ok=True)
    settings = json.loads((folder / "experiment.json").read_text())
    state = json.loads((domain / "frontier.json").read_text())

    def table(frame: pd.DataFrame, columns: dict[str, str], formats: dict) -> str:
        """A table of the page: the columns named, under the headings given.

        Args:
            frame: The rows.
            columns: Each column to show and its heading.
            formats: The format of a column's values, for those of the
                columns that are shown; an empty value stays empty.

        Returns:
            The table as markdown, its columns aligned.
        """
        shown = frame[list(columns)].copy()
        for column in shown.columns.intersection(list(formats)):
            pattern = formats[column]
            shown[column] = [
                "" if pd.isna(value) else pattern.format(value)
                for value in shown[column]
            ]
        return tabulate(
            shown.fillna("").values.tolist(),
            headers=list(columns.values()),
            tablefmt="github",
            disable_numparse=True,
        )

    spent = parts(folder)
    spent.to_csv(out / "parts.csv", index=False)
    total = spent[["runs", "input_tokens", "output_tokens", "dollars", "hours"]].sum()
    kept = harnesses(folder) if (domain / "test.csv").is_file() else None
    # The headline: what was run, how far it got, and what it found.
    headline = [
        ("experiment", f"{settings['experiment']} on split {settings['split']}"),
        ("the agent's model", settings["model"]),
        ("the search roles' model", settings["proposer_model"] or settings["model"]),
        (
            "rounds decided",
            f"{len(state['trajectory']) - 1} of {settings['rounds']}",
        ),
        ("changes kept", state["promotions"]),
    ]
    if kept is None:
        headline += [("test score", "the test step has not run yet")]
    else:
        first, last = kept.iloc[0], kept.iloc[-1]
        headline += [("test score, starting harness", f"{first['test_score']:.1f}%")]
        if len(kept) > 1:
            headline += [
                ("test score, last harness kept", f"{last['test_score']:.1f}%"),
                (
                    "gain on the test tasks",
                    f"{last['test_gain_points']:+.1f} points (95% confidence "
                    f"interval {last['test_gain_interval_low']:+.1f} to "
                    f"{last['test_gain_interval_high']:+.1f}; "
                    f"p {last['test_gain_p']:.4f})",
                ),
            ]
    headline += [
        ("cost", f"${total['dollars']:.2f}"),
        ("code version", settings["mas_optimiser_version"]),
    ]
    text = [
        f"# Report of the experiment {settings['experiment']}, split {settings['split']}",
        "",
        tabulate(
            headline[1:], headers=headline[0], tablefmt="github", disable_numparse=True
        ),
        "",
        "A score is the mean over tasks of the checks an attempt passed, in percent.",
    ]

    if kept is not None:
        kept.to_csv(out / "harnesses.csv", index=False)
        text += [
            "",
            "## The harnesses kept",
            "",
            "Each harness that was the current one, run once on the test "
            "tasks. The gain is over the starting harness on the same test "
            "tasks, with its 95% confidence interval and the p-value of the "
            "one-sided test that it is above zero.",
            "",
            table(
                kept.assign(
                    interval=[
                        "" if pd.isna(low) else f"{low:+.1f} to {high:+.1f}"
                        for low, high in zip(
                            kept["test_gain_interval_low"],
                            kept["test_gain_interval_high"],
                            strict=True,
                        )
                    ]
                ),
                {
                    "after_round": "kept after round",
                    "validation_score": "validation score",
                    "test_score": "test score",
                    "test_gain_points": "gain on test, points",
                    "interval": "95% confidence interval",
                    "test_gain_p": "p",
                    "test_input_tokens_an_attempt": "input tokens an attempt",
                    "test_dollars_an_attempt": "dollars an attempt",
                },
                {
                    "validation_score": "{:.1f}",
                    "test_score": "{:.1f}",
                    "test_gain_points": "{:+.1f}",
                    "test_gain_p": "{:.4f}",
                    "test_input_tokens_an_attempt": "{:,.0f}",
                    "test_dollars_an_attempt": "{:.4f}",
                },
            ),
        ]
        if kept["test_gain_points"].notna().any():
            draw_gain(kept, out / "figures")
            text += [
                "",
                "![The gain of each harness kept, on the test tasks]"
                "(figures/test_gain.png)",
                "",
                "*The gain of each harness kept over the starting harness, on "
                "the test tasks, with its 95% confidence interval from the "
                "paired differences over those tasks. A bar wholly to the "
                "right of the no-gain line is a gain at that level. Numbers in "
                "`figures/test_gain.csv`.*",
            ]
    else:
        text += ["", "The test step has not run yet, so no harness has a test score."]

    # The score of the current harness after each round, from the frontier.
    scores = pd.DataFrame(
        {
            "after_round": [entry["t"] for entry in state["trajectory"]],
            "validation_score": [100 * entry["S"] for entry in state["trajectory"]],
        }
    )
    if (domain / "test.csv").is_file():
        tested = dict(zip(kept["after_round"], kept["test_score"], strict=True))
        scores["test_score"] = scores["after_round"].map(tested).ffill()
    scores.to_csv(out / "figures" / "scores.csv", index=False)
    with plt.style.context(figures.STYLE):
        fig, ax = plt.subplots(figsize=(6.4, 3.6))
        ax.step(
            scores["after_round"],
            scores["validation_score"],
            where="post",
            color=figures.THEORY,
            marker="o",
            label="validation tasks",
        )
        if "test_score" in scores:
            ax.step(
                scores["after_round"],
                scores["test_score"],
                where="post",
                color=figures.OBSERVED,
                marker="s",
                label="test tasks",
            )
        ax.set_xlabel("rounds decided")
        ax.set_ylabel("score of the current harness (% of checks passed)")
        ax.set_xticks(scores["after_round"])
        ax.legend(frameon=False)
        for kind in ("pdf", "png"):
            fig.savefig(out / "figures" / f"scores.{kind}")
        plt.close(fig)
    text += [
        "",
        "![The current harness's score after each round](figures/scores.png)",
        "",
        "*The score of the current harness after each round, on the validation "
        "tasks and, once the test step has run, on the test tasks. The line "
        "steps up where a change was kept. Numbers in `figures/scores.csv`.*",
    ]

    decided = rounds(folder)
    decided.to_csv(out / "rounds.csv", index=False)
    if len(decided):
        text += [
            "",
            "## The rounds",
            "",
            "Each candidate and where it ended: at the review, at its one-task "
            "trial, at the filter, or at the gate. Gains are in percentage "
            "points. A candidate is kept when its gain is above the gain "
            "needed, which is the bar times the standard error; that is the "
            "same as T, the gain divided by its standard error, being above "
            "the bar. The bar rises with each round and each change kept.",
            "",
            table(
                decided,
                {
                    "round": "round",
                    "changed": "changed",
                    "ended_at": "ended at",
                }
                # Shown where the experiment ran its candidates on them.
                | (
                    {"training_gain_points": "gain on the training tasks"}
                    if decided["training_gain_points"].notna().any()
                    else {}
                )
                | {
                    "filter_gain_points": "gain on the filter's tasks",
                    "gain_points": "gain on all validation tasks",
                    "gain_needed_points": "gain needed",
                    "standard_error_points": "standard error",
                    "t": "T",
                    "bar": "bar",
                    "p": "p",
                },
                {
                    "training_gain_points": "{:+.1f}",
                    "filter_gain_points": "{:+.1f}",
                    "gain_points": "{:+.1f}",
                    "gain_needed_points": "{:+.1f}",
                    "standard_error_points": "{:.1f}",
                    "t": "{:+.2f}",
                    "bar": "{:.2f}",
                    "p": "{:.4f}",
                },
            ),
        ]
        draw_gate(decided, out / "figures")
        text += [
            "",
            "![Each round's candidate at the gate](figures/gate.png)",
            "",
            "*Each round's candidate at the gate. A point is the gain a "
            "candidate found on the validation tasks, with one standard error "
            "either side; the short line in its column is the gain its "
            "decision needed. A candidate is kept when its point is above its "
            "line. A cross is a candidate dropped at the filter, at its gain "
            "on the filter's tasks, which are fewer; a round with no mark "
            "ended before either. Numbers in `figures/gate.csv`.*",
        ]

    if len(decided):
        text += [
            "",
            "## What each round proposed",
            "",
            "Each candidate's change and the proposer's hypothesis for it, in "
            "the proposer's own words, written before the candidate was run. "
            "The diff is the change itself, kept in the experiment's folder.",
            "",
            table(
                decided.assign(
                    # A table's cell is one line, and a bar would end it.
                    hypothesis=[
                        " ".join(said.replace("|", "/").split())
                        if isinstance(said, str)
                        else ""
                        for said in decided["hypothesis"]
                    ]
                ),
                {
                    "round": "round",
                    "ended_at": "ended at",
                    "files": "files changed",
                    "lines_added": "lines added",
                    "lines_removed": "lines removed",
                    "diff": "diff",
                    "hypothesis": "hypothesis",
                },
                {"lines_added": "{:.0f}", "lines_removed": "{:.0f}"},
            ),
        ]

    if len(decided) and decided["actions"].notna().any():
        text += [
            "",
            "## How each proposal was made",
            "",
            "The proposer's own record of its drafting. It is given the "
            "harness's files and the analyst's report, and may open a "
            "training trace or read a file before it edits. Its saying it "
            "is done is refused when what it declares does not hold, and "
            "the reviewer may send a proposal back to be changed.",
            "",
            table(
                decided,
                {
                    "round": "round",
                    "actions": "actions taken",
                    "traces_opened": "traces opened",
                    "files_read": "files read",
                    "done_refused": '"done" refused',
                    "sent_back": "sent back by the reviewer",
                    "edits": "edits declared",
                    "changed": "kinds",
                },
                dict.fromkeys(
                    ("actions", "traces_opened", "files_read", "done_refused")
                    + ("sent_back", "edits"),
                    "{:.0f}",
                ),
            ),
        ]

    failed = errors(folder)
    failed.to_csv(out / "errors.csv", index=False)
    text += ["", "## Attempts that ended in an error", ""]
    if len(failed):
        text += [
            "An attempt that ends in an error is scored zero for its task; only "
            "an attempt that left no result at all has its job run again. Each "
            "job with attempts that ended in an error is below: "
            "how many there were, and how many of them ran past their limit "
            "of seconds, past their limit of calls to the model, or ended "
            "otherwise. An error's own words are in the `error` column of the "
            "job's `tasks.csv`. An attempt whose service never answered is "
            "not here: it is scored nowhere.",
            "",
            table(
                failed,
                {
                    "what": "job",
                    "attempts": "attempts",
                    "in_error": "in error",
                    "past_seconds": "past their seconds",
                    "past_calls": "past their calls",
                    "other": "otherwise",
                },
                {},
            ),
        ]
    else:
        text += ["No attempt of any job ended in an error."]

    text += [
        "",
        "## What it cost",
        "",
        "Each part of the experiment. A run is an attempt at a task for the "
        "agent's parts and a call to the model for the search roles. The "
        "hours are the runs' own, added up, and not time on the clock, since "
        "several run at once.",
        "",
        table(
            pd.concat(
                [
                    spent,
                    pd.DataFrame(
                        [
                            dict(total)
                            | {
                                "part": "all",
                                "share_of_dollars_percent": 100.0,
                                "share_of_input_tokens_percent": 100.0,
                            }
                        ]
                    ),
                ]
            ),
            {
                "part": "part",
                "runs": "runs",
                "input_tokens": "input tokens",
                "output_tokens": "output tokens",
                "dollars": "dollars",
                "share_of_dollars_percent": "share of the dollars",
                "hours": "hours",
            },
            {
                "runs": "{:,.0f}",
                "input_tokens": "{:,.0f}",
                "output_tokens": "{:,.0f}",
                "dollars": "{:.2f}",
                "share_of_dollars_percent": "{:.1f}%",
                "hours": "{:.2f}",
            },
        ),
    ]
    (out / "report.md").write_text("\n".join(text) + "\n")
    print(f"Report in {out / 'report.md'}")
