"""One experiment of the loop, and the ``mas-optimiser evolve`` command.

An experiment has a folder, ``evolutions/<experiment>/<split>/``, holding a
small git repository with the harness in it (RRSI keeps every candidate as a
commit on a branch of its own) and everything the loop writes. Its steps:

1. The baseline: the starting harness on the training tasks, kept in full
   for the search roles, and on the validation tasks, kept for the gate.
2. A round, repeated: the analyst and the proposer are given the current
   harness's score on every training task and a count of how its attempts
   at them went wrong, and may read the traces of the round's pool, a share
   of those tasks picked each round; the
   proposer drafts each candidate in its own checkout, told of
   earlier proposals only whether each was accepted; a candidate that passes
   the review and still runs is scored on the validation tasks, on a share
   of them first (the filter), which drops a candidate that does no better
   there before the rest are run; the gate compares a candidate that passed
   with the current harness task by task, on all of them. An accepted candidate
   becomes the current harness and is run on the training tasks, so that the
   next round has its traces.
3. The test: every harness the experiment kept, run once on the test tasks.

Each stage may have its own number of attempts at a task: the training
tasks, the filter, the gate and the test. The filter's attempts are the
first of the gate's, so a candidate that passes the filter is run only for
the attempts it still lacks.

The command can be given again after a stop of any kind: each step that left
its result is skipped.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``evolve``.
"""

import argparse
import csv
import dataclasses
import json
import random
import shutil
from pathlib import Path

import numpy as np
import yaml
from dotenv import load_dotenv

import mas_optimiser
from mas_optimiser.benchmarks.enterpriseops import REPO_ROOT, runner, splits
from mas_optimiser.benchmarks.enterpriseops.domain import EnterpriseOps
from mas_optimiser.evolve import models
from mas_optimiser.stats import budget, gate

from rrsi import analyst, critic, digester, gitops, propose
from rrsi.config import RRSIConfig
from rrsi.evaluate import (
    EvalResult,
    TaskResult,
    aggregate,
    evaluate,
    relative_cost_change,
)
from rrsi.history import exploration
from rrsi.loop import VARIANT_LABELS, Run, log
from rrsi.schedule import edit_budget
from rrsi.selection import Candidate

# RRSI's four search roles reach their model through this one name.
for _role in (analyst, critic, digester, propose):
    _role.generate = models.generate

EVOLUTIONS = REPO_ROOT / "evolutions"
DESCRIPTION = (
    "improve a harness: rounds of proposals read from the training tasks and "
    "judged by the acceptance gate on the validation tasks"
)
# What a user accepts by letting a model rewrite the agent's code with nobody
# reading the change before it runs.
UNREVIEWED = (
    "This experiment lets a model rewrite the agent's code (editable holds "
    ".py) and runs what it writes without a person reading it first "
    "(approval: none). That code runs as this user on this machine, and can "
    "reach whatever this account can: its files, and the keys in .env. Each "
    "change is screened and reviewed by a model before it runs, and each "
    "attempt is held to a time and to a number of model calls; none of that "
    "is a guarantee."
)
# What the proposer is told of an earlier proposal. No score and no cost: of
# the validation tasks it learns only whether a proposal was accepted.
# Put before every training task's score in the analyst's and the proposer's
# instructions, each round.
SCORES = (
    "Every training task's score with the current harness, lowest first: the "
    "mean over its attempts of the fraction of checks passed. A task marked * "
    "is in this round's pool, and only those have a trace that can be read; "
    "the others are listed for their scores."
)
# Put before the count of how the current harness's training attempts went,
# in the same two roles' instructions, each round.
FAILURES = (
    "How the current harness's attempts at the training tasks went, counted "
    "over every attempt and not only those of this round's pool, and what "
    "their failed checks found, most frequent first."
)
TOLD = (
    "t",
    "variant",
    "edit_id",
    "component",
    "hypothesis",
    "targets_mode",
    "outcome",
)


class Experiment(Run):
    """One experiment's state and steps, on RRSI's ``Run``.

    Kept from ``Run``: the paths, the record of proposals, the checkouts
    and the drafting of a candidate (proposer, review, trial run). Replaced:
    the baseline and the round, which here use two sets of tasks, the pick
    of the training tasks whose traces are read, the scoring of a candidate,
    which here may begin with a filter, and the rule that keeps a change.

    Args:
        domain: The benchmark, a ``Domain`` that also gives
            ``validation_ids``, ``failures`` (how a job's attempts went
            wrong, as text) and, for a filter, ``domain_of`` (each task's
            group, of which the filter takes the same share).
        cfg: RRSI's settings: rounds, the attempts at the gate (its ``k``),
            candidates, the edits a candidate may hold and the models.
        repo: The experiment's git repository, with the harness at its top.
        runs_root: The experiment's folder.
        alpha: The Type-I error budget of the whole experiment.
        filter_share: The share of the validation tasks a candidate is run on
            first; 0 for no filter.
        filter_threshold: The gain, in percentage points of the score, that a
            candidate must be above on the filter's tasks to be run on the
            rest.
        seed: Seeds the draw of each round's filter tasks.
        pool_share: The share of the training tasks whose traces the search
            roles may read in a round; 1 for every task.
        pool_pick: How those tasks are picked from the ranking by score:
            ``"spread"`` or ``"ends"`` (see ``pool``).
        attempts: The attempts at a task on the training tasks, at the
            filter and on the test tasks, by ``"training"``, ``"filter"``
            and ``"test"``; a stage left out has the gate's. The filter's
            may not be more than the gate's.
    """

    def __init__(
        self,
        domain: EnterpriseOps,
        cfg: RRSIConfig,
        repo: Path,
        runs_root: Path,
        alpha: float,
        filter_share: float = 0.0,
        filter_threshold: float = 0.0,
        seed: int = 0,
        pool_share: float = 1.0,
        pool_pick: str = "spread",
        attempts: dict[str, int] | None = None,
    ):
        super().__init__(domain, cfg, repo, runs_root)
        self.attempts = dict.fromkeys(("training", "filter", "test"), cfg.k) | (
            attempts or {}
        )
        self.harness_rel = domain.harness_path
        self.alpha = alpha
        self.filter_share = filter_share
        self.filter_threshold = filter_threshold
        self.seed = seed
        self.pool_share = pool_share
        self.pool_pick = pool_pick
        # The roles' instructions as the benchmark gives them; each round's
        # scores are added to a copy.
        self.briefs = dict(domain.briefs)

    def baseline(self, job: str = "base") -> EvalResult:
        """Run the starting harness on the training and validation tasks.

        Args:
            job: The first part of the two jobs' names.

        Returns:
            The result on the validation tasks.

        Raises:
            SystemExit: If too many attempts of either job are missing for
                it to stand as a baseline.
        """
        d, cfg = self.domain, self.cfg
        self.ensure_branch()
        checkout = self.checkout("current", self.branch)
        results = {}
        # The validation scores are what the gate compares a candidate with,
        # so they have the gate's attempts.
        for name, ids, attempts in (
            ("training", d.evolve_ids(), self.attempts["training"]),
            ("validation", d.validation_ids(), cfg.k),
        ):
            result = evaluate(d, checkout, self.runs, f"{job}_{name}", ids, attempts)
            if result.missing > cfg.invalid_missing_frac * result.n_expected:
                raise SystemExit(
                    f"error: the baseline on the {name} tasks is missing "
                    f"{result.missing} of {result.n_expected} attempts"
                )
            result.save(self.eval_path(result.job))
            results[name] = result
        validation = results["validation"]
        commit = gitops.rev(self.repo, self.branch)
        self.save_frontier(
            {
                "domain": d.name,
                "incumbent": {
                    "t": 0,
                    "commit": commit,
                    "harness_tree": self.harness_tree(self.branch),
                    "job": validation.job,
                    "training_job": results["training"].job,
                    "S": validation.S,
                    "C": validation.C,
                },
                "S_star": validation.S,
                "promotions": 0,
                "trajectory": [
                    {
                        "t": 0,
                        "S": validation.S,
                        "C": validation.C,
                        "commit": commit,
                        "job": validation.job,
                    }
                ],
                "config": cfg.dump(),
            }
        )
        self.history.append(
            {
                "t": 0,
                "variant": "-",
                "edit_id": None,
                "component": None,
                "hypothesis": "the starting harness",
                "outcome": "BASELINE",
                "accepted": True,
                "S": validation.S,
                "C": validation.C,
            }
        )
        return validation

    def filter_ids(self, t: int, ids: list[str]) -> list[str]:
        """The tasks a round's candidates are filtered on.

        The same share of each of the benchmark's groups (its domains),
        drawn afresh each round, so that no fixed part of the validation
        tasks carries every filter.

        Args:
            t: The round, from 0.
            ids: The validation tasks.

        Returns:
            The filter's tasks, in the order of ``ids``; none with no filter.
        """
        if not self.filter_share:
            return []
        draw = random.Random(f"{self.seed}:{t}")
        groups: dict[str, list[str]] = {}
        for task in ids:
            groups.setdefault(self.domain.domain_of[task], []).append(task)
        chosen: set[str] = set()
        for tasks in groups.values():
            chosen.update(
                draw.sample(tasks, max(1, round(self.filter_share * len(tasks))))
            )
        return [task for task in ids if task in chosen]

    def _evaluate(self, t: int, c: Candidate, rdir: Path, ids: list[str]) -> None:
        """Score a candidate on the validation tasks, on the filter's tasks first.

        With no filter this is the released step. With one, the candidate is
        run on the filter's tasks, with the filter's attempts, and if its
        mean score there is not above the current harness's by more than the
        threshold it is dropped and nothing more is run. A candidate that
        passes is run for the attempts it still lacks: on the filter's tasks
        up to the gate's number, where the filter had fewer, and on the other
        tasks. The parts make its one result, every task with the gate's
        attempts, so no attempt is run twice. The filter can only drop a
        candidate: one that is kept has still passed the gate's test on every
        validation task with all of its attempts, so the chance of keeping a
        change that does nothing is no higher than without it.

        Args:
            t: The round, from 0.
            c: The candidate; its result goes to ``c.ev``, or the reason it
                has none to ``c.gate_failure``.
            rdir: The round's folder.
            ids: The validation tasks.
        """
        first = self.filter_ids(t, ids)
        done = rdir / c.variant / "eval.json"
        if not first or done.exists():
            super()._evaluate(t, c, rdir, ids)
            return
        cfg, job = self.cfg, f"r{t}{c.variant}"
        current = EvalResult.load(self.eval_path(self.frontier()["incumbent"]["job"]))
        rest = [task for task in ids if task not in set(first)]
        per_task, totals = {}, {}
        for stage, tasks, attempts in (
            ("filter", first, self.attempts["filter"]),
            ("more", first, cfg.k - self.attempts["filter"]),
            ("rest", rest, cfg.k),
        ):
            if not tasks or not attempts:
                continue
            # As the released step does: a second run when attempts are
            # missing, and a candidate that cannot be scored is set aside.
            for _ in range(2):
                try:
                    part = evaluate(
                        self.domain,
                        self.wt_root / job,
                        self.runs,
                        f"{job}_{stage}",
                        tasks,
                        attempts,
                    )
                except Exception as error:  # noqa: BLE001 - recorded with the candidate
                    c.gate_failure = "eval_invalid"
                    c.detail = f"evaluation crashed: {error!r}"[:600]
                    return
                if part.missing <= cfg.invalid_missing_frac * part.n_expected:
                    break
            else:
                c.gate_failure = "eval_invalid"
                c.detail = f"{part.missing}/{part.n_expected} attempts missing after a second run"
                return
            for task, result in part.per_task.items():
                # A task of the filter gets its further attempts added to
                # those the filter ran.
                earlier = per_task.get(task)
                per_task[task] = (
                    TaskResult(
                        rewards=earlier.rewards + result.rewards,
                        tokens=earlier.tokens + result.tokens,
                        missing=earlier.missing + result.missing,
                    )
                    if earlier
                    else result
                )
            for key, value in part.extra.items():
                totals[key] = totals.get(key, 0) + value
            if stage == "filter":
                gain = 100 * float(
                    np.mean(
                        [
                            part.per_task[i].mean - current.per_task[i].mean
                            for i in tasks
                        ]
                    )
                )
                passed = gain > self.filter_threshold
                record = {
                    "tasks": len(tasks),
                    "gain_points": round(gain, 3),
                    "threshold_points": self.filter_threshold,
                    "passed": passed,
                }
                (rdir / c.variant / "filter.json").write_text(json.dumps(record))
                log(
                    self.domain.name,
                    f"{c.variant}: {gain:+.1f} points on the filter's {len(tasks)} "
                    f"tasks, {'passed' if passed else 'dropped'}",
                )
                if not passed:
                    c.gate_failure = "filtered_out"
                    return
        c.ev = aggregate(job, cfg.k, {task: per_task[task] for task in ids}, totals)
        c.ev.save(done)
        # The parts ran as jobs of their own, so the whole has no folder yet.
        self.eval_path(job).parent.mkdir(parents=True, exist_ok=True)
        c.ev.save(self.eval_path(job))

    def pool(self, t: int, per_task: dict) -> dict[str, int]:
        """The training tasks whose traces may be read in a round: the pool.

        The tasks are ranked by score, lowest first, and a share of them is
        taken. ``"spread"`` takes them at even steps along the ranking and
        starts one place further each round, so that over the rounds every
        task comes into the pool; the attempt shown is a task's
        lowest-scoring one. ``"ends"`` takes the lowest and the highest, 22
        to 6 as RRSI does, and shows the lowest-scoring attempt of a low task
        and the highest-scoring attempt of a high one.

        Args:
            t: The round, from 0.
            per_task: The current harness's results on the training tasks.

        Returns:
            Each task of the pool with the attempt of it to show, from 0.
        """
        ranked = sorted(per_task, key=lambda task: per_task[task].mean)
        if not ranked:
            return {}
        n = max(1, round(self.pool_share * len(ranked)))
        if self.pool_pick == "ends":
            high = round(n * 6 / 28)
            low, top = ranked[: n - high], ranked[len(ranked) - high :]
        else:
            step = len(ranked) / n
            low = [ranked[int(t + i * step) % len(ranked)] for i in range(n)]
            top = []
        shown = {}
        for tasks, choose in ((low, min), (top, max)):
            for task in tasks:
                rewards = per_task[task].rewards
                shown[task] = rewards.index(choose(rewards))
        return shown

    def round(self, t: int) -> None:
        """One round: read the training traces, draft, score on validation, decide.

        Args:
            t: The round, from 0.
        """
        d, cfg = self.domain, self.cfg
        state = self.frontier()
        current = state["incumbent"]
        folder = self.runs / f"r{t}"
        folder.mkdir(exist_ok=True)
        training = EvalResult.load(self.eval_path(current["training_job"]))
        validation = EvalResult.load(self.eval_path(current["job"]))

        # What went wrong, from the training tasks only: the score of every
        # one of them, and the traces of this round's pool.
        traces = {}
        for task, attempt in self.pool(t, training.per_task).items():
            record = d.load_trial(self.runs, current["training_job"], task, attempt)
            if record is not None:
                traces[task] = record
        scores = "\n".join(
            f"{task} {training.per_task[task].mean:.2f}{' *' if task in traces else ''}"
            for task in sorted(
                training.per_task, key=lambda task: training.per_task[task].mean
            )
        )
        failures = d.failures(self.runs, current["training_job"])
        d.briefs = self.briefs | {
            role: f"{self.briefs[role]}\n\n{FAILURES}\n{failures}\n\n{SCORES}\n{scores}"
            for role in ("analyst", "proposer")
        }
        # The most independent edits a candidate may hold this round: RRSI's
        # schedule, a half cosine from the first round's number to the last
        # round's. The released round divides by the number of rounds, which
        # ends one step short of the last number; here the last round is the
        # schedule's end.
        allowed = edit_budget(t, cfg.T - 1, cfg.b_min, cfg.b_max)
        log(
            d.name,
            f"round {t}: the traces of {len(traces)} of {len(training.per_task)} "
            f"training tasks can be read ({self.pool_pick}); a candidate may "
            f"hold {allowed} {'edit' if allowed == 1 else 'edits'}",
        )
        report_path = folder / "analysis_report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
        else:
            report = analyst.analyze(
                d, traces, training.per_task, folder, model=cfg.analyst_model
            )
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1))
        told = [
            {key: record[key] for key in TOLD if record.get(key) is not None}
            for record in self.history.records()
            if record.get("edit_id")
        ][-40:]
        rules, patterns = d.constitution(self.repo)
        candidates = [
            self._draft(
                t,
                VARIANT_LABELS[n],
                folder,
                report,
                analyst.load_digests(folder),
                traces,
                training,
                allowed,
                exploration(t, 0, self.history.tried(), cfg.m_draft),
                False,
                [],
                told,
                rules,
                patterns,
            )
            for n in range(cfg.m)
        ]

        # Each candidate that got this far, on the validation tasks.
        ids = d.validation_ids()
        for candidate in candidates:
            if candidate.gate_failure is None:
                self._evaluate(t, candidate, folder, ids)

        # The gate: a paired test of each candidate against the current
        # harness, at this decision's share of the experiment's error budget.
        level = budget.reuse_level(self.alpha, t + 1, state["promotions"], cfg.m)
        decisions, winner, best = [], None, None
        for candidate in candidates:
            decision = {"variant": candidate.variant, "level": level, "accept": False}
            filtered = folder / candidate.variant / "filter.json"
            if filtered.exists():
                decision["filter"] = json.loads(filtered.read_text())
            if candidate.ev is None:
                # The released loop files a person's no under the trial run
                # it came before; here it is recorded as what it was.
                declined = '"stage": "approval"' in (candidate.detail or "")
                decision["stopped"] = (
                    "declined by a person"
                    if declined
                    else candidate.gate_failure or "not evaluated"
                )
            else:
                differences = np.array(
                    [
                        candidate.ev.per_task[i].mean - validation.per_task[i].mean
                        for i in ids
                    ]
                )
                try:
                    test = gate.mean_test(differences, level)
                except ValueError as error:
                    decision["stopped"] = f"no test: {error}"
                else:
                    decision |= dataclasses.asdict(test)
                    if test.accept and (best is None or test.mean > best):
                        winner, best = candidate, test.mean
            decisions.append(decision)
        (folder / "decisions.json").write_text(json.dumps(decisions, indent=1))

        for candidate, decision in zip(candidates, decisions, strict=True):
            if self.history.has(t, candidate.variant):
                continue
            if candidate.ev is None:
                # A candidate dropped at the filter is told to the proposer
                # as rejected, like one the gate turned down: of the
                # validation tasks it learns no more than that.
                filtered_out = candidate.gate_failure == "filtered_out"
                self.history.append_candidate(
                    t,
                    candidate.variant,
                    candidate.edits,
                    "REJECTED"
                    if filtered_out
                    else candidate.gate_failure or "not_evaluated",
                    None,
                    None,
                    False,
                    None,
                    None,
                    candidate.diff_path,
                    candidate.detail,
                )
                continue
            outcome = "LOST" if decision["accept"] else "REJECTED"
            self.history.append_candidate(
                t,
                candidate.variant,
                candidate.edits,
                "ACCEPTED" if candidate is winner else outcome,
                candidate.ev.S - validation.S,
                relative_cost_change(candidate.ev.C, validation.C),
                candidate is winner,
                candidate.ev.S,
                candidate.ev.C,
                candidate.diff_path,
            )

        if winner is not None:
            # Its training traces first, then the move, so that a stop in
            # between leaves the current harness as it was.
            job = f"r{t}{winner.variant}_training"
            checkout = self.wt_root / f"r{t}{winner.variant}"
            result = evaluate(
                d, checkout, self.runs, job, d.evolve_ids(), self.attempts["training"]
            )
            result.save(self.eval_path(job))
            gitops.fast_forward(self.repo, self.branch, winner.commit)
            current = {
                "t": t + 1,
                "commit": winner.commit,
                "harness_tree": self.harness_tree(winner.commit),
                "job": winner.ev.job,
                "training_job": job,
                "S": winner.ev.S,
                "C": winner.ev.C,
            }
            state["incumbent"] = current
            state["S_star"] = max(state["S_star"], winner.ev.S)
            state["promotions"] += 1
        state["trajectory"] = [x for x in state["trajectory"] if x["t"] <= t] + [
            {
                "t": t + 1,
                "S": current["S"],
                "C": current["C"],
                "commit": current["commit"],
                "job": current["job"],
            }
        ]
        self.save_frontier(state)
        for candidate in candidates:
            gitops.worktree_remove(self.repo, self.wt_root / f"r{t}{candidate.variant}")

    def test(self) -> list[dict]:
        """Run every harness the experiment kept on the test tasks, once each.

        Returns:
            One row per harness, in the order they became current: the round
            after which it was current, its commit, and its scores on the
            validation and the test tasks. Also written to ``test.csv``.
        """
        rows, done = [], set()
        for entry in self.frontier()["trajectory"]:
            if entry["commit"] in done:
                continue
            done.add(entry["commit"])
            label = f"t{entry['t']}"
            path = self.eval_path(f"heldout_{label}")
            result = (
                EvalResult.load(path)
                if path.exists()
                else self.heldout(
                    label,
                    self.domain.heldout_ids(),
                    ref=entry["commit"],
                    k=self.attempts["test"],
                )
            )
            rows.append(
                {
                    "round": entry["t"],
                    "commit": entry["commit"],
                    "validation": round(entry["S"], 4),
                    "test": round(result.S, 4),
                    "missing_attempts": result.missing,
                }
            )
        with (self.runs / "test.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return rows


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``evolve`` options to ``parser``: those of ``run`` and the loop's own.

    Args:
        parser: The ``evolve`` subparser.
    """
    runner.add_arguments(parser)
    parser.add_argument(
        "--proposer-model",
        help="the model file of the search roles (default: the agent's model)",
    )
    parser.add_argument("--rounds", type=int, help="rounds of the loop")
    parser.add_argument("--candidates", type=int, help="candidates proposed per round")
    parser.add_argument(
        "--edits-first",
        type=int,
        help="the most independent edits one candidate may hold in the first round",
    )
    parser.add_argument(
        "--edits-last",
        type=int,
        help="the same in the last round; the rounds between follow RRSI's "
        "schedule from the one number to the other",
    )
    for stage, words in (
        ("training", "on the training tasks, whose traces the search roles read"),
        ("filter", "at the filter; not more than the gate's, being the first of them"),
        ("gate", "on the validation tasks, where the gate decides"),
        ("test", "on the test tasks"),
    ):
        parser.add_argument(
            f"--attempts-{stage}",
            type=int,
            help=f"attempts at a task {words} (default: --attempts)",
        )
    parser.add_argument(
        "--alpha",
        type=float,
        help="the chance, over the whole experiment, of keeping a change that "
        "does nothing",
    )
    parser.add_argument(
        "--filter-share",
        type=float,
        help="the share of the validation tasks a candidate is run on first; "
        "one that does no better there is dropped before the rest are run "
        "(0 for no filter)",
    )
    parser.add_argument(
        "--filter-threshold",
        type=float,
        help="the gain, in percentage points of the score, a candidate must "
        "be above on the filter's tasks",
    )
    parser.add_argument(
        "--pool-share",
        type=float,
        help="the share of the training tasks whose traces the search roles "
        "may read in a round (1 for every task); every task's score is shown "
        "whatever the share",
    )
    parser.add_argument(
        "--pool-pick",
        choices=runner.POOL_PICKS,
        help="how the pool is picked from the training tasks ranked by score: "
        "spread, evenly over the ranking and one place further each round, or "
        "ends, its lowest and its highest",
    )
    parser.add_argument(
        "--editable",
        nargs="+",
        help="the types of file in the harness folder a proposal may edit, "
        "e.g. .md for text only",
    )
    parser.add_argument(
        "--approval",
        choices=("none", "each"),
        help="each: show every proposed change and wait for a yes at the "
        "terminal before it is run; none: run without asking (the default)",
    )
    parser.add_argument(
        "--accept-unreviewed-code",
        action="store_true",
        help="needed to start an experiment that edits code (.py) with "
        "approval none: the risk it prints is accepted",
    )


def run(args: argparse.Namespace) -> None:
    """Run the experiment named by the parsed arguments, or carry it on.

    Args:
        args: The parsed arguments.

    Raises:
        SystemExit: If the experiment names no split, names one set of it,
            names a harness with no ``agent.py``, or would run code no person
            has read without that having been accepted.
    """
    overrides = {key: getattr(args, key) for key in runner.EXPERIMENT_DEFAULTS}
    experiment = runner.load_experiment(args.experiment, overrides)
    print("Settings that differ from the defaults:")
    for key, value in experiment["changed"].items():
        print(f"  {key:<17}{value}", flush=True)
    split = experiment["split"]
    if not split or ":" in split:
        raise SystemExit(
            "error: evolve needs a split by its name alone (split: <name>), "
            "since it uses all three of its sets"
        )
    if ".py" in experiment["editable"] and experiment["approval"] == "none":
        if not args.accept_unreviewed_code:
            raise SystemExit(
                f"error: {UNREVIEWED}\nTo go on as it is, add "
                "--accept-unreviewed-code. To read and approve each change, "
                "add --approval each. To have only the agent's text changed, "
                "add --editable .md"
            )
        print(f"warning: {UNREVIEWED}", flush=True)
    load_dotenv(REPO_ROOT / ".env")
    label = f"-{experiment['label']}" if experiment["label"] else ""
    folder = EVOLUTIONS / experiment["experiment"] / f"{split}{label}"
    repository = folder / "repository"
    if not repository.exists():
        harness = str(experiment["harness"])
        start = Path(harness) if "/" in harness else runner.HARNESSES / harness
        if not (start / "agent.py").is_file():
            raise SystemExit(
                f"error: no harness at {start} (a folder holding agent.py)"
            )
        shutil.copytree(start, repository / EnterpriseOps.harness_path)
        gitops.git(repository, "init", "-q", check=True)
        gitops.git(repository, "add", "-A", check=True)
        gitops.git(repository, "commit", "-q", "-m", "the starting harness", check=True)
    proposer = experiment["proposer_model"] or experiment["model"]
    cfg = RRSIConfig(
        T=experiment["rounds"],
        k=experiment["attempts_gate"] or experiment["attempts"],
        m=experiment["candidates"],
        b_min=experiment["edits_last"],
        b_max=experiment["edits_first"],
        delta=0.0,
        proposer_model=proposer,
        analyst_model=proposer,
        critic_model=proposer,
    )
    loop = Experiment(
        EnterpriseOps(experiment),
        cfg,
        repository,
        folder,
        experiment["alpha"],
        experiment["filter_share"],
        experiment["filter_threshold"],
        splits.load_split(split)["seed"],
        experiment["pool_share"],
        experiment["pool_pick"],
        {
            stage: experiment[f"attempts_{stage}"] or experiment["attempts"]
            for stage in ("training", "filter", "test")
        },
    )
    models.usage_log = folder / "search_usage.jsonl"
    models.timeout = experiment["model_timeout"]
    models.limit = experiment["attempt_timeout"]
    models.wait = experiment["service_wait"]
    # The search roles' model file as written: it names the variables that
    # hold a key or an address, and never their values.
    settings = experiment | {
        "proposer_settings": yaml.safe_load(
            runner.config_path("models", proposer).read_text()
        ),
        "mas_optimiser_version": mas_optimiser.code_version(),
    }
    (folder / "experiment.json").write_text(json.dumps(settings, indent=2, default=str))

    if not loop.frontier_path.exists():
        print(f"Baseline on the training and validation tasks; results in {folder}")
        loop.baseline()
    for t in range(cfg.T):
        if len(loop.frontier()["trajectory"]) > t + 1:
            continue
        loop.round(t)
        state = loop.frontier()
        print(
            f"Round {t + 1} of {cfg.T}: {state['promotions']} accepted so far, "
            f"validation score {state['incumbent']['S']:.3f}"
        )
    for row in loop.test():
        print(
            f"After round {row['round']}: validation {row['validation']:.3f}, "
            f"test {row['test']:.3f}"
        )
    print(f"Results in {folder}")
