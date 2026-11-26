"""This benchmark as the loop sees it: how a harness is run, scored and shown.

The loop (``mas_optimiser.evolve``) knows nothing of EnterpriseOps-Gym. It
asks a benchmark for a few things through RRSI's ``Domain`` interface, and
``EnterpriseOps`` answers them with what this package already does:

- the task sets are the three sets of the experiment's split;
- running a harness on some tasks is ``mas-optimiser run`` in a process of
  its own, given the harness folder and the task ids, so that a candidate's
  code never runs inside the loop;
- an attempt's score is its fraction of checks passed, the outcome the
  acceptance gate tests, read from ``tasks.csv``;
- the trace the search roles read is the attempt's page;
- how a job's attempts went wrong, counted over all of them, is the
  runner's own table of what the failed checks found, added up;
- the check that a candidate still works is one task run with it, and its
  record read for the shape everything else depends on.

Another benchmark is added by writing another class like this one.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``evolve``.
"""

import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import mas_optimiser.evolve  # noqa: F401  (puts vendor/rrsi on sys.path)
from mas_optimiser.benchmarks.enterpriseops import (
    REPO_ROOT,
    checks,
    prompts,
    report,
    runner,
    splits,
)

from rrsi.components import K
from rrsi.domain import Domain
from rrsi.evaluate import TaskResult

TURN = re.compile(r"^### Turn (\d+)")
# The kinds of entry an attempt's record may hold; the pages, the diagnosis
# and the cost all read them.
RECORD_TYPES = {"system_message", "user_message", "ai_message", "tool_result"}
# How an attempt that ended in an error ended, by the start of its error:
# the runner's fixed messages for its two limits (``runner.run_task`` and
# ``runner.TaskExecutor.initialize``), and what the benchmark's agent raises
# when the model asks for a tool its server does not have
# (``orchestrators/base.py``, a ``raise`` with nothing to raise).
ENDINGS = (
    ("the attempt ran past its limit", "ran past its seconds"),
    ("the attempt made more than", "made more calls to the model than allowed"),
    ("No active exception to reraise", "asked for a tool the server does not have"),
)
# A file of code among those a diff changes, by its header line.
CODE = r"(?m)^(?:\+\+\+ b|--- a)/\S+\.py$"


class EnterpriseOps(Domain):
    """EnterpriseOps-Gym behind RRSI's ``Domain`` interface.

    Args:
        experiment: The settings from ``runner.load_experiment``; ``split``
            names the split, ``limit`` caps each of its sets (for a trial of
            the loop), ``editable`` gives the file types a proposal may
            touch, and ``feedback`` says what the proposer is told of its
            earlier proposals.

    Attributes:
        name: The folder under the experiment's that holds the loop's files.
        harness_path: The harness folder inside the experiment's repository.
        critic_patterns: What a proposal's diff must not contain, each with
            the reason given when it does. An experiment's own list adds a
            change to a file whose type is not among its ``editable``.
        component_signals: The patterns by which RRSI settles the kind of
            change an edit is recorded as. RRSI keeps the kind the proposer
            declares only where the diff holds one of that kind's patterns,
            and otherwise reads the kind from the diff. A change to the
            agent's code can be of any kind, so every kind has the pattern
            of a changed code file and the proposer's word stands there; a
            change to the text file alone is ``prompt`` whatever was declared.
    """

    name = "enterpriseops"
    harness_path = "harness"
    # What a proposal's diff must not contain, checked before any model reads
    # it: content of a task, a reference to the measure, and code that reaches
    # past the agent's own work. The last five are a screen for accidents and
    # plain misuse, written so that neither the starting harness nor ordinary
    # prose matches them; code set on getting round them can.
    critic_patterns = [
        (r"task_\d{8}_\d{6}_\d{3}_[0-9a-f]{8}_[0-9a-f]{8}", "a task id in the diff"),
        (r"\b[A-Z]{2,6}[_-]?\d{3,}\b", "a record number or row id in the diff"),
        (
            r"verifier|checks\.csv|cells\.csv|database_state",
            "a reference to the measure in the diff",
        ),
        (
            r"\.environ\b|\bgetenv\(|\bdotenv\b",
            "code that reads the environment, where keys are kept",
        ),
        (
            r"\b(import|from)\s+(os|sys|subprocess|multiprocessing|socket|ssl|"
            r"requests|httpx|urllib3?|aiohttp|ftplib|smtplib|shutil|tempfile|"
            r"ctypes|pickle|marshal|importlib|runpy|builtins|pty)\b",
            "an import that reaches files, other programs, the network or the "
            "interpreter",
        ),
        (
            r"\b(eval|exec|compile|__import__|open)\(|__builtins__|"
            r"\.__(?!init__|name__|doc__)[a-z][a-z_]*__",
            "code that runs text as code, opens a file by itself or reaches "
            "into the interpreter",
        ),
        (
            r"\.\./|~/|/etc/|/home/|/root/|\.ssh\b|\.env\b|expanduser|Path\.home|"
            r"\.write_(text|bytes)\(|\.unlink\(|\.rmdir\(",
            "code that reaches files outside the harness folder, or writes or "
            "removes files",
        ),
        (
            r"llm_client\.llm\b|\.a?invoke\(|\.astream\(|\.bind_tools\(|"
            r"\b(LLMClient|MCPClient)\(",
            "a call to the model or the tools by a route that is not the one "
            "the agent is given",
        ),
    ]
    component_signals = [
        (kind, [CODE] + ([r"prompt\.md"] if kind == "prompt" else [])) for kind in K
    ]

    def __init__(self, experiment: dict):
        self.experiment = experiment
        # A task with no check left to judge it by is not run (as in
        # ``runner.evaluate``), so it belongs to no set here.
        self.domain_of = {
            task_id: domain
            for domain in experiment["domain"]
            for task_id, config in runner.load_tasks(domain, experiment["mode"]).items()
            if checks.prepare(task_id, config["verifiers"])[0]
        }
        split = splits.load_split(experiment["split"])
        self.sets = {
            name: [t for t in split[name] if t in self.domain_of][: experiment["limit"]]
            for name in splits.SETS
        }
        self.source_exts = set(experiment["editable"])
        # RRSI's file tools check the type of a file that is created and not
        # of one that is edited, so a change to a file of a type that may not
        # be edited is stopped here, at the screen every diff goes through.
        types = "|".join(re.escape(ext.lstrip(".")) for ext in self.source_exts)
        self.critic_patterns = [
            *type(self).critic_patterns,
            (
                rf"(?m)^(?:\+\+\+ b|--- a)/\S+\.(?!(?:{types})$)\w+$",
                "a change to a file of a type that may not be edited",
            ),
        ]
        self.approval = experiment["approval"]
        self.briefs = {
            "analyst": prompts.ANALYST,
            "digester": prompts.DIGESTER,
            "proposer": prompts.PROPOSER.format(
                editable=", ".join(sorted(self.source_exts))
            )
            + f"\n\n{prompts.WORKING}"
            + (
                f"\n\n{prompts.FEEDBACK}"
                if experiment["feedback"] == "training"
                else ""
            ),
            "critic": prompts.CRITIC,
        }

    def evolve_ids(self) -> list[str]:
        """The tasks the search roles may read: the training set.

        Returns:
            The task ids.
        """
        return self.sets["training"]

    def validation_ids(self) -> list[str]:
        """The tasks a candidate is judged on, hidden from the search roles.

        Returns:
            The task ids.
        """
        return self.sets["validation"]

    def heldout_ids(self) -> list[str]:
        """The tasks run once at the end of an experiment: the test set.

        Returns:
            The task ids.
        """
        return self.sets["test"]

    def smoke_ids(self, incumbent_per_task: dict | None = None) -> list[str]:
        """The task a candidate is tried on before it is scored.

        Args:
            incumbent_per_task: The current harness's results; not used.

        Returns:
            The first training task.
        """
        return self.sets["training"][:1]

    def harness_dir(self, root: Path) -> Path:
        """The harness folder inside a checkout of the experiment's repository.

        Args:
            root: The checkout.

        Returns:
            The folder.
        """
        return Path(root) / self.harness_path

    def constitution(self, root: Path) -> tuple[str, str]:
        """The proposer's rules and its list of what a change can be.

        Args:
            root: The checkout; not used, since the texts belong to the
                benchmark and not to the harness.

        Returns:
            The two texts.
        """
        return prompts.RULES, prompts.PATTERNS

    def rows(self, out: Path) -> dict[tuple[str, int], dict]:
        """A job's ``tasks.csv`` rows over its domains, by task and attempt.

        Args:
            out: The job's folder, holding one evaluation folder per domain.

        Returns:
            The rows that exist.
        """
        found = {}
        for path in out.glob("*/tasks.csv"):
            with path.open() as f:
                for row in csv.DictReader(f):
                    found[row["task_id"], int(row["attempt"])] = row
        return found

    def run(
        self,
        root: Path,
        runs_dir: Path,
        job: str,
        ids: list[str],
        k: int,
        log_prefix: str = "",
    ) -> None:
        """Run the harness in a checkout on some tasks, ``k`` attempts each.

        One ``mas-optimiser run`` per domain, each in a process of its own.
        A job whose every attempt has a row is left alone, an attempt that
        ended in an error included: that is the harness's failure and a
        result like any other. Otherwise the whole job is run again.

        An attempt that ended because a service it needs did not answer is
        no verdict on the harness, so a job that holds one stops the
        experiment, and is run again when the command is next given. Its
        other domains are not run first: the whole job is run again, and
        each would wait for the service as long.

        Args:
            root: The checkout whose harness folder is run.
            runs_dir: The experiment's folder; the job goes to ``jobs/<job>``.
            job: The job's name.
            ids: The task ids.
            k: Attempts per task.
            log_prefix: The name of the log file, the job's by default.

        Raises:
            SystemExit: If attempts of the job ended because a service
                they need did not answer.
        """
        out = Path(runs_dir) / "jobs" / job
        rows = self.rows(out)
        if all(
            (task, n) in rows and rows[task, n].get("service_error") != "True"
            for task in ids
            for n in range(1, k + 1)
        ):
            return
        if out.exists():
            shutil.rmtree(out)
        # What the job is to hold, for a look at it while it runs
        # (``mas-optimiser status``): the filter's tasks are drawn each round,
        # so nothing else on disk says how many attempts to expect.
        out.mkdir(parents=True)
        (out / "job.json").write_text(json.dumps({"tasks": len(ids), "attempts": k}))
        log = Path(runs_dir) / "logs" / f"{log_prefix or job}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        settings = self.experiment
        # Named when the tasks are of one set, so that the summary of a
        # held-out job leaves out what went wrong.
        subset = next(
            (name for name, members in self.sets.items() if set(ids) <= set(members)),
            None,
        )
        for domain in settings["domain"]:
            chosen = [task for task in ids if self.domain_of[task] == domain]
            if not chosen:
                continue
            command = [
                sys.executable,
                "-c",
                "from mas_optimiser.cli import main; main()",
            ]
            command += ["run", settings["experiment_file"], "--domain", domain]
            command += ["--model", settings["model"], "--mode", settings["mode"]]
            command += ["--harness", str(self.harness_dir(root))]
            command += ["--out", str(out / domain), "--attempts", str(k)]
            command += ["--limit", str(len(chosen))]
            command += ["--concurrency", str(settings["concurrency"])]
            command += ["--model-timeout", str(settings["model_timeout"])]
            command += ["--attempt-timeout", str(settings["attempt_timeout"])]
            command += ["--attempt-calls", str(settings["attempt_calls"])]
            command += ["--attempt-retries", str(settings["attempt_retries"])]
            command += ["--service-wait", str(settings["service_wait"])]
            command += ["--trace" if settings["trace"] else "--no-trace"]
            if settings["thinking_summary"]:
                command += ["--thinking-summary"]
            if not settings["prompt_caching"]:
                command += ["--no-prompt-caching"]
            if subset:
                command += ["--split", f"{settings['split']}:{subset}"]
            for task in chosen:
                command += ["--task", task]
            with log.open("a") as f:
                done = subprocess.run(
                    command, cwd=REPO_ROOT, stdout=f, stderr=subprocess.STDOUT
                )
            if done.returncode:
                print(
                    f"[{self.name}] run of {job}/{domain} ended with {done.returncode}; see {log}"
                )
            if any(
                row.get("service_error") == "True" for row in self.rows(out).values()
            ):
                break
        unanswered = sum(
            row.get("service_error") == "True" for row in self.rows(out).values()
        )
        if unanswered:
            # No verdict on the harness: the experiment waits for the service
            # and carries on from this job when the command is given again.
            raise SystemExit(
                f"error: {unanswered} attempts of {job} ended because a "
                "service they need did not answer (the model service or the "
                "domain's server). Nothing was scored. Give the command again "
                "when the service answers; the experiment carries on from "
                "this job."
            )

    def score(
        self, runs_dir: Path, job: str, ids: list[str], k: int
    ) -> tuple[dict[str, TaskResult], dict]:
        """Each task's attempts as scores, and the job's totals.

        Args:
            runs_dir: The experiment's folder.
            job: The job's name.
            ids: The task ids.
            k: Attempts per task.

        Returns:
            Per task, the fraction of checks each attempt passed and the
            tokens it used; and the attempts fully passed, the cost in
            dollars and the seconds, over the job. An attempt that ended in
            an error is the harness's failure: none of its checks passed, as
            the runner recorded it. Only an attempt with no row is counted
            as missing, and scored zero.
        """
        rows = self.rows(Path(runs_dir) / "jobs" / job)
        per_task, passed, dollars, seconds = {}, 0, 0.0, 0.0
        for task in ids:
            rewards, tokens = [], []
            for n in range(1, k + 1):
                row = rows.get((task, n))
                if row is None:
                    continue
                rewards.append(int(row["checks_passed"]) / int(row["checks_total"]))
                tokens.append(int(row["input_tokens"]) + int(row["output_tokens"]))
                passed += row["success"] == "True"
                dollars += float(row["cost_usd"])
                seconds += float(row["seconds"])
            missing = k - len(rewards)
            per_task[task] = TaskResult(
                rewards=rewards + [0.0] * missing,
                tokens=tokens + [None] * missing,
                missing=missing,
            )
        totals = {
            "attempts_passed": passed,
            "cost_usd": round(dollars, 4),
            "seconds": round(seconds),
        }
        return per_task, totals

    def failures(self, runs_dir: Path, job: str, most: int = 20) -> str:
        """How a job's attempts went, counted over all of them, for the search roles.

        The runner writes, for every evaluation, what its failed checks
        found (``failures.csv``: the table, the column, what was wrong and
        how the value came to be set, with the attempts and the tasks it
        happened in). The job's domains are added up here. It is asked for
        a job on the training tasks only.

        Args:
            runs_dir: The experiment's folder.
            job: The job's name.
            most: The most rows of what the failed checks found to give.

        Returns:
            One line with the attempts, those that passed every check, those
            that ended in an error, by how (``ENDINGS``), and those in which
            a tool answered with an error; then a line for each thing the
            failed checks found, most frequent first; then the meaning of
            the words those lines use (``report.WORDS``).
        """
        out = Path(runs_dir) / "jobs" / job
        rows = list(self.rows(out).values())
        said = [row["error"] for row in rows if row["error"]]
        how = {
            words: sum(error.startswith(start) for error in said)
            for start, words in ENDINGS
        }
        how["ended otherwise"] = len(said) - sum(how.values())
        ended = ", ".join(f"{n} {words}" for words, n in how.items() if n)
        lines = [
            f"{len(rows)} attempts: "
            f"{sum(row['success'] == 'True' for row in rows)} passed every check; "
            f"{len(said)} ended in an error{f' ({ended})' if ended else ''}; "
            f"a tool answered with an error in "
            f"{sum(int(row.get('tool_errors') or 0) > 0 for row in rows)}."
        ]
        found: dict[tuple, list[int]] = {}
        for path in sorted(out.glob("*/failures.csv")):
            with path.open() as f:
                for row in csv.DictReader(f):
                    key = (row["table"], row["column"], row["failure"], row["how"])
                    counts = found.setdefault(key, [0, 0])
                    counts[0] += int(row["attempts"])
                    counts[1] += int(row["tasks"])
        listed = sorted(found.items(), key=lambda item: -item[1][0])[:most]
        for (table, column, failure, came), (attempts, tasks) in listed:
            where = f"{table}.{column}" if column else table
            lines.append(
                f"- {where}: {failure}{f', {came}' if came else ''} "
                f"({attempts} attempts, {tasks} tasks)"
            )
        used = {word for (_, _, failure, came), _ in listed for word in (failure, came)}
        lines += [
            f"{word}: {meaning}." for word, meaning in report.WORDS if word in used
        ]
        return "\n".join(lines)

    def load_trial(
        self, runs_dir: Path, job: str, task_id: str, trial: int
    ) -> dict | None:
        """One attempt's page and row, for the search roles to read.

        Args:
            runs_dir: The experiment's folder.
            job: The job's name.
            task_id: The task.
            trial: The attempt, counted from 0 as RRSI does.

        Returns:
            The page and the row, or ``None`` if the attempt left none.
        """
        out = Path(runs_dir) / "jobs" / job
        attempt = trial + 1
        page = (
            out / self.domain_of[task_id] / "tasks" / task_id / f"attempt-{attempt}.md"
        )
        row = self.rows(out).get((task_id, attempt))
        if row is None or not page.exists():
            return None
        return {"task_id": task_id, "page": page.read_text(), "row": row}

    def render_trace(self, rec: dict, detail: bool = False) -> str:
        """The attempt's page with each line of a turn marked for RRSI's trace reader.

        Every line of a turn is prefixed ``[step N]``, so that a range of
        turns can be asked for. RRSI's reader gives every line with no such
        prefix whatever range is asked for, and gives a page read with no
        range only up to a number of characters from its start. The checks,
        which the page has before its turns, are therefore in every read.
        No line marks them as RRSI's verifier section: that reader gives
        everything after such a mark, which before the turns would be every
        turn every time, and after them would be cut from a long page.

        Args:
            rec: What ``load_trial`` returned.
            detail: Not used: the page is given as it was written.

        Returns:
            The page's text, in the page's own order.
        """
        lines, step = [], None
        for line in rec["page"].splitlines():
            turn = TURN.match(line)
            if turn:
                step = int(turn.group(1))
            elif line.startswith("## "):
                step = None
            lines.append(f"[step {step}] {line}" if step is not None else line)
        return "\n".join(lines)

    def task_row(self, task_id: str, rec: dict, tr: TaskResult) -> str:
        """One line about a traced task, for the tables the search roles read.

        Args:
            task_id: The task.
            rec: What ``load_trial`` returned for the traced attempt.
            tr: The task's result over its attempts.

        Returns:
            The line.
        """
        row = rec["row"]
        return (
            f"{task_id} | passed={row['success']} checks {row['checks_passed']}/"
            f"{row['checks_total']} (mean over attempts {tr.mean:.2f}) | "
            f"model_calls={row['model_calls']} tool_errors={row['tool_errors']} "
            f"error={row['error'] or '-'}"
        )

    def smoke(
        self, root: Path, runs_dir: Path, job: str, ids: list[str]
    ) -> tuple[bool, dict]:
        """Check that a candidate still works, before it is scored.

        This is the first time a candidate's files are run. With
        ``approval: each`` the change is shown and a person is asked whether
        to run it; a no ends the candidate here. Then each task is run once
        with it. The attempt must end without an error, and its record must
        keep the shape the rest of the package reads: a conversation that
        opens with the system and user messages and holds no entry of an
        unknown kind.

        Args:
            root: The candidate's checkout.
            runs_dir: The experiment's folder.
            job: The job's name.
            ids: The task ids, from ``smoke_ids``.

        Returns:
            Whether the candidate works, and where the check stopped.

        Raises:
            SystemExit: If a person's approval is needed and there is no
                terminal to ask at.
        """
        if self.approval == "each":
            # The first time the change would run: a person reads it first.
            change = subprocess.run(
                ["git", "-C", str(root), "show", "--stat", "--patch", "HEAD", "--"]
                + [self.harness_path],
                capture_output=True,
                text=True,
            ).stdout
            print(f"\n{change}\nThis change has not been run yet.", flush=True)
            try:
                answer = input("Run it on the tasks? [y/N] ")
            except EOFError:
                raise SystemExit(
                    "error: approval is set to each, and nobody is at the "
                    "terminal to answer. Give the command again from a "
                    "terminal; the experiment carries on from this change."
                ) from None
            if answer.strip().lower() not in ("y", "yes"):
                return False, {"stage": "approval", "detail": "declined by a person"}
        out = Path(runs_dir) / "jobs" / job
        self.run(root, runs_dir, job, ids, 1)
        rows = self.rows(out)
        for task in ids:
            row = rows.get((task, 1))
            if row is None or row["error"]:
                detail = row["error"] if row else "the attempt left no row"
                return False, {"stage": "run", "detail": detail[:600]}
            folder = out / self.domain_of[task] / "tasks" / task
            record = json.loads((folder / "attempt-1.json").read_text())
            flow = record["runs"][0].get("conversation_flow") or []
            kinds = [str(entry.get("type")) for entry in flow]
            if kinds[:2] != ["system_message", "user_message"] or not (
                set(kinds) <= RECORD_TYPES
            ):
                detail = f"the record's entries are of kinds {sorted(set(kinds))}"
                return False, {"stage": "record", "detail": detail}
        return True, {"stage": "run", "n": len(ids)}
