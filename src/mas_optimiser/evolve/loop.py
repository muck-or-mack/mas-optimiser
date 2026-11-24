"""One experiment of the loop, and the ``mas-optimiser evolve`` command.

An experiment has a folder, ``evolutions/<experiment>/<split>/``, holding a
small git repository with the harness in it (RRSI keeps every candidate as a
commit on a branch of its own) and everything the loop writes. Its steps:

1. The baseline: the starting harness on the training tasks, kept in full
   for the search roles, and on the validation tasks, kept for the gate.
2. A round, repeated: the analyst reads the current harness's training
   traces; the proposer drafts each candidate in its own checkout, told of
   earlier proposals only whether each was accepted; a candidate that passes
   the review and still runs is scored on the validation tasks; the gate
   compares it with the current harness task by task. An accepted candidate
   becomes the current harness and is run on the training tasks, so that the
   next round has its traces.
3. The test: every harness the experiment kept, run once on the test tasks.

The command can be given again after a stop of any kind: each step that left
its result is skipped.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``evolve``.
"""

import argparse
import csv
import dataclasses
import json
import shutil
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

import mas_optimiser
from mas_optimiser.benchmarks.enterpriseops import REPO_ROOT, runner
from mas_optimiser.benchmarks.enterpriseops.domain import EnterpriseOps
from mas_optimiser.evolve import models
from mas_optimiser.stats import budget, gate

from rrsi import analyst, critic, digester, gitops, propose
from rrsi.config import RRSIConfig
from rrsi.evaluate import EvalResult, evaluate, relative_cost_change
from rrsi.history import exploration
from rrsi.loop import VARIANT_LABELS, Run
from rrsi.schedule import edit_budget

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

    Kept from ``Run``: the paths, the record of proposals, the checkouts,
    the drafting of a candidate (proposer, review, trial run) and its
    scoring. Replaced: the baseline and the round, which here use two sets
    of tasks, and the rule that keeps a change.

    Args:
        domain: The benchmark, a ``Domain`` that also gives
            ``validation_ids``.
        cfg: RRSI's settings: rounds, attempts, candidates and the models.
        repo: The experiment's git repository, with the harness at its top.
        runs_root: The experiment's folder.
        alpha: The Type-I error budget of the whole experiment.
    """

    def __init__(
        self,
        domain: EnterpriseOps,
        cfg: RRSIConfig,
        repo: Path,
        runs_root: Path,
        alpha: float,
    ):
        super().__init__(domain, cfg, repo, runs_root)
        self.harness_rel = domain.harness_path
        self.alpha = alpha

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
        for name, ids in (
            ("training", d.evolve_ids()),
            ("validation", d.validation_ids()),
        ):
            result = evaluate(d, checkout, self.runs, f"{job}_{name}", ids, cfg.k)
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

        # What went wrong, from the training tasks only.
        traces = self.build_traces(current["training_job"], training.per_task)
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
                edit_budget(t, cfg.T, cfg.b_min, cfg.b_max),
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
                self.history.append_candidate(
                    t,
                    candidate.variant,
                    candidate.edits,
                    candidate.gate_failure or "not_evaluated",
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
            result = evaluate(d, checkout, self.runs, job, d.evolve_ids(), cfg.k)
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
                else self.heldout(label, self.domain.heldout_ids(), ref=entry["commit"])
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
        "--alpha",
        type=float,
        help="the chance, over the whole experiment, of keeping a change that "
        "does nothing",
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
        k=experiment["attempts"],
        m=experiment["candidates"],
        b_min=1,
        b_max=1,
        delta=0.0,
        proposer_model=proposer,
        analyst_model=proposer,
        critic_model=proposer,
    )
    loop = Experiment(
        EnterpriseOps(experiment), cfg, repository, folder, experiment["alpha"]
    )
    models.usage_log = folder / "search_usage.jsonl"
    models.timeout = experiment["model_timeout"]
    models.limit = experiment["attempt_timeout"]
    settings = experiment | {"mas_optimiser_version": mas_optimiser.code_version()}
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
