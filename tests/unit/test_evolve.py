"""The improvement loop: the model call, the benchmark adapter and one round."""

import csv
import json
import re
import threading
from types import SimpleNamespace
from unittest.mock import ANY

import pytest
from langchain_core.messages import AIMessage

from mas_optimiser.benchmarks.enterpriseops import domain as adapter
from mas_optimiser.evolve import loop, models
from mas_optimiser.stats import budget

from rrsi import critic, gitops
from rrsi.config import RRSIConfig
from rrsi.domain import Domain
from rrsi.evaluate import TaskResult
from rrsi.selection import Candidate

# ---------------------------------------------------------------- the model call


class Scripted:
    """A model that gives its answers in turn and keeps what it was sent."""

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def invoke(self, messages):
        self.sent.append([(type(m).__name__, m.content) for m in messages])
        return self.answers.pop(0)


def test_generate_returns_the_text_and_logs_the_call(tmp_path, monkeypatch):
    usage = {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
    model = Scripted(AIMessage(content="OK", usage_metadata=usage))
    monkeypatch.setitem(models._clients, "a-model", model)
    monkeypatch.setattr(models, "usage_log", tmp_path / "usage.jsonl")
    answer = models.generate("Say OK.", system="Be brief.", model="a-model")
    assert answer == "OK"
    assert model.sent == [[("SystemMessage", "Be brief."), ("HumanMessage", "Say OK.")]]
    logged = json.loads((tmp_path / "usage.jsonl").read_text())
    assert (logged["model"], logged["input_tokens"], logged["output_tokens"]) == (
        "a-model",
        12,
        3,
    )


def test_generate_builds_its_client_with_the_time_limit_and_the_model_files_tries(
    monkeypatch,
):
    client = SimpleNamespace(timeout=None, max_retries=2)
    model = SimpleNamespace(
        root_client=client, invoke=lambda messages: AIMessage(content="OK")
    )
    config = models.runner.ModelConfig("openai", "m", "not-a-key", max_retries=7)
    monkeypatch.setattr(models.runner, "config_path", lambda kind, name: name)
    monkeypatch.setattr(models.runner, "load_model", lambda path: (config, {}))
    monkeypatch.setattr(
        models, "LLMClient", lambda *args, **kwargs: SimpleNamespace(llm=model)
    )
    monkeypatch.setattr(models, "_clients", {})
    monkeypatch.setattr(models, "timeout", 45)
    assert models.generate("Say OK.", model="a-model") == "OK"
    assert (client.timeout, client.max_retries) == (45, 7)


def test_generate_asks_for_json_and_returns_only_the_json(monkeypatch):
    model = Scripted(AIMessage(content='Here it is:\n```json\n{"a": 1}\n```'))
    monkeypatch.setitem(models._clients, "a-model", model)
    answer = models.generate(
        "Give a.", json_only=True, model="a-model", cache_prefix="The rules."
    )
    assert answer == '{"a": 1}'
    (system, instruction), (_, prompt) = model.sent[0]
    assert system == "SystemMessage" and "single valid JSON object" in instruction
    # The text RRSI would mark for a provider's cache goes before the prompt.
    assert prompt == "The rules.\n\nGive a."


def test_generate_tries_again_and_then_gives_up(monkeypatch):
    monkeypatch.setattr(models.time, "sleep", lambda seconds: None)
    model = Scripted(AIMessage(content=""), AIMessage(content="late"))
    monkeypatch.setitem(models._clients, "a-model", model)
    assert models.generate("Say something.", model="a-model") == "late"
    monkeypatch.setitem(models._clients, "a-model", Scripted(AIMessage(content="")))
    with pytest.raises(RuntimeError, match="no answer after 1 tries"):
        models.generate("Say something.", model="a-model", max_retries=1)


def test_generate_gives_up_a_call_that_runs_past_its_limit_of_seconds(monkeypatch):
    release = threading.Event()
    stuck = SimpleNamespace(invoke=lambda messages: release.wait(5))
    monkeypatch.setitem(models._clients, "a-model", stuck)
    monkeypatch.setattr(models, "limit", 0.05)
    with pytest.raises(RuntimeError, match="within its limit of 0.05 seconds"):
        models.generate("Say something.", model="a-model")
    release.set()


# ------------------------------------------------------------------ the adapter

SETS = {
    "training": ["t1", "t2", "t3"],
    "validation": ["v1", "v2", "x9"],
    "test": ["h1"],
}


@pytest.fixture
def benchmark(monkeypatch):
    """The adapter over two made-up domains; ``x9`` is in the split but is no task."""
    tasks = {"itsm": ["t1", "t2", "v1", "h1"], "hr": ["t3", "v2"]}
    monkeypatch.setattr(
        adapter.runner,
        "load_tasks",
        lambda name, mode: {task: {"verifiers": ["a check"]} for task in tasks[name]},
    )
    monkeypatch.setattr(adapter.checks, "prepare", lambda task, verifiers: (verifiers,))
    monkeypatch.setattr(adapter.splits, "load_split", lambda name: SETS)
    experiment = {
        "experiment_file": "configs/experiments/an-experiment.yaml",
        "domain": ["itsm", "hr"],
        "mode": "all_tools",
        "model": "a-model",
        "split": "drawn",
        "limit": None,
        "editable": [".md"],
        "approval": "none",
        "concurrency": 2,
        "model_timeout": 300,
        "attempt_timeout": 900,
        "attempt_calls": 40,
        "attempt_retries": 1,
        "service_wait": 120,
        "trace": False,
        "thinking_summary": True,
        "prompt_caching": True,
    }
    return adapter.EnterpriseOps(experiment)


def write_rows(out, rows):
    """A job's ``tasks.csv`` per domain, from (domain, task, attempt, passed, error)."""
    for name, task, attempt, passed, error in rows:
        folder = out / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "tasks.csv"
        fresh = not path.exists()
        with path.open("a", newline="") as f:
            writer = csv.writer(f)
            if fresh:
                writer.writerow(
                    ["task_id", "attempt", "success", "checks_passed", "checks_total"]
                    + ["error", "input_tokens", "output_tokens", "cost_usd", "seconds"]
                )
            writer.writerow(
                [task, attempt, passed == 4, passed, 4, error, 100, 10, 0.01, 5]
            )


def test_the_sets_are_the_splits_tasks_that_can_be_run(benchmark):
    assert benchmark.evolve_ids() == ["t1", "t2", "t3"]
    # A task of the split that the benchmark does not run is in no set.
    assert benchmark.validation_ids() == ["v1", "v2"]
    assert benchmark.heldout_ids() == ["h1"]
    assert benchmark.smoke_ids() == ["t1"]
    assert ".md" in benchmark.briefs["proposer"]
    assert benchmark.constitution("anywhere")[0].startswith("# Rules")


def test_run_starts_one_run_per_domain_with_the_harness_and_the_set(
    benchmark, tmp_path, monkeypatch
):
    commands = []
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda command, **kwargs: (
            commands.append(command) or SimpleNamespace(returncode=0)
        ),
    )
    benchmark.run(tmp_path / "checkout", tmp_path, "r0A", ["v1", "v2"], 2)
    by_domain = {c[c.index("--domain") + 1]: c for c in commands}
    assert set(by_domain) == {"itsm", "hr"}
    itsm = by_domain["itsm"]
    assert itsm[itsm.index("--harness") + 1] == str(tmp_path / "checkout" / "harness")
    assert itsm[itsm.index("--out") + 1] == str(tmp_path / "jobs" / "r0A" / "itsm")
    assert itsm[itsm.index("--attempts") + 1] == "2"
    # The settings as the loop was given them, an option of its command
    # included, and not as the experiment file alone has them.
    assert itsm[itsm.index("--model-timeout") + 1] == "300"
    assert itsm[itsm.index("--attempt-timeout") + 1] == "900"
    assert itsm[itsm.index("--attempt-calls") + 1] == "40"
    assert itsm[itsm.index("--attempt-retries") + 1] == "1"
    assert itsm[itsm.index("--service-wait") + 1] == "120"
    assert "--thinking-summary" in itsm
    assert itsm[itsm.index("--task") + 1 :] == ["v1"]
    # Named as the validation set, so that its summary leaves out the failures.
    assert itsm[itsm.index("--split") + 1] == "drawn:validation"
    # Tasks of more than one set are given no set.
    commands.clear()
    benchmark.run(tmp_path / "checkout", tmp_path, "mixed", ["t1", "v1"], 1)
    assert "--split" not in commands[0]


def test_run_leaves_a_finished_job_alone_and_repeats_one_with_no_verdict(
    benchmark, tmp_path, monkeypatch
):
    commands = []
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda command, **kwargs: (
            commands.append(command) or SimpleNamespace(returncode=0)
        ),
    )
    # Every attempt has a row: finished, an attempt that the harness ended
    # in an error included, since that is a result like any other.
    out = tmp_path / "jobs" / "done"
    write_rows(out, [("itsm", "t1", 1, 4, ""), ("hr", "t3", 1, 0, "no such tool")])
    benchmark.run(tmp_path, tmp_path, "done", ["t1", "t3"], 1)
    assert commands == []
    # An attempt has no row: the job was cut short, and is run again whole.
    out = tmp_path / "jobs" / "short"
    write_rows(out, [("itsm", "t1", 1, 4, "")])
    benchmark.run(tmp_path, tmp_path, "short", ["t1", "t3"], 1)
    assert len(commands) == 2 and not out.exists()
    # An attempt a service never answered is no verdict: run again whole.
    commands.clear()
    out = tmp_path / "jobs" / "unanswered"
    (out / "itsm").mkdir(parents=True)
    (out / "itsm" / "tasks.csv").write_text(
        "task_id,attempt,error,service_error\nt1,1,down,True\n"
    )
    benchmark.run(tmp_path, tmp_path, "unanswered", ["t1"], 1)
    assert len(commands) == 1 and not out.exists()


def test_run_stops_the_experiment_when_a_service_did_not_answer(
    benchmark, tmp_path, monkeypatch
):
    out = tmp_path / "jobs" / "r0A"

    def run(command, **kwargs):
        # The run it starts leaves one attempt the service never answered.
        folder = out / "itsm"
        folder.mkdir(parents=True)
        (folder / "tasks.csv").write_text(
            "task_id,attempt,error,service_error\nt1,1,down,True\n"
        )
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(adapter.subprocess, "run", run)
    with pytest.raises(SystemExit, match="1 attempts of r0A ended because a service"):
        benchmark.run(tmp_path / "checkout", tmp_path, "r0A", ["t1"], 1)


def test_score_is_the_fraction_of_checks_and_an_error_is_a_failure_not_a_gap(
    benchmark, tmp_path
):
    rows = [
        ("itsm", "t1", 1, 4, ""),
        ("itsm", "t1", 2, 2, ""),
        ("itsm", "t2", 1, 0, "no such tool"),
    ]
    write_rows(tmp_path / "jobs" / "a-job", rows)
    per_task, totals = benchmark.score(tmp_path, "a-job", ["t1", "t2"], 2)
    assert per_task["t1"].rewards == [1.0, 0.5] and per_task["t1"].missing == 0
    assert per_task["t1"].tokens == [110, 110]
    # One attempt ended in an error, which is the harness's failure and a
    # result; one never ran, which is the only attempt missing. Both zero.
    assert per_task["t2"].rewards == [0.0, 0.0] and per_task["t2"].missing == 1
    assert totals == {"attempts_passed": 1, "cost_usd": 0.03, "seconds": 15}


def test_the_trace_marks_each_turn_and_the_checks(benchmark):
    page = "# t1\n## Request\nDo it.\n### Turn 1\nA call.\n## Checks\nFailed."
    text = benchmark.render_trace({"page": page})
    assert "[step 1] ### Turn 1\n[step 1] A call." in text
    assert "=== VERIFIER" in text and "[step 1] Failed." not in text


def test_the_trial_run_refuses_a_record_of_another_shape(
    benchmark, tmp_path, monkeypatch
):
    monkeypatch.setattr(benchmark, "run", lambda *args, **kwargs: None)
    out = tmp_path / "jobs" / "smoke"
    write_rows(out, [("itsm", "t1", 1, 4, "")])
    folder = out / "itsm" / "tasks" / "t1"
    folder.mkdir(parents=True)

    def record(kinds):
        flow = [{"type": kind} for kind in kinds]
        (folder / "attempt-1.json").write_text(
            json.dumps({"runs": [{"conversation_flow": flow}]})
        )
        return benchmark.smoke(tmp_path, tmp_path, "smoke", ["t1"])

    assert record(["system_message", "user_message", "ai_message"])[0] is True
    ok, where = record(["system_message", "user_message", "a_plan"])
    assert ok is False and where["stage"] == "record"
    assert record(["user_message", "ai_message"])[0] is False
    # An attempt that ended in an error fails the trial before its record is read.
    write_rows(tmp_path / "jobs" / "failed", [("itsm", "t1", 1, 0, "it raised")])
    ok, where = benchmark.smoke(tmp_path, tmp_path, "failed", ["t1"])
    assert ok is False and where == {"stage": "run", "detail": "it raised"}


def test_with_approval_a_person_sees_the_change_before_it_first_runs(
    benchmark, tmp_path, monkeypatch, capsys
):
    benchmark.approval = "each"
    ran = []
    monkeypatch.setattr(benchmark, "run", lambda *args, **kwargs: ran.append(args))
    shown = SimpleNamespace(stdout="+a line the proposer added")
    monkeypatch.setattr(adapter.subprocess, "run", lambda *args, **kwargs: shown)
    # A no ends the candidate before anything of it has run.
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    ok, where = benchmark.smoke(tmp_path, tmp_path, "smoke", ["t1"])
    assert (ok, where["stage"], ran) == (False, "approval", [])
    assert "+a line the proposer added" in capsys.readouterr().out
    # A yes lets the trial run go ahead.
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    benchmark.smoke(tmp_path, tmp_path, "smoke", ["t1"])
    assert len(ran) == 1


def test_approval_with_nobody_at_the_terminal_stops_the_experiment(
    benchmark, tmp_path, monkeypatch
):
    benchmark.approval = "each"
    monkeypatch.setattr(benchmark, "run", lambda *args, **kwargs: None)
    shown = SimpleNamespace(stdout="")
    monkeypatch.setattr(adapter.subprocess, "run", lambda *args, **kwargs: shown)

    def nobody(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", nobody)
    with pytest.raises(SystemExit, match="nobody is at the terminal"):
        benchmark.smoke(tmp_path, tmp_path, "smoke", ["t1"])


def test_the_screen_passes_the_starting_harness_and_prose_and_stops_unsafe_code():
    patterns = critic.GENERIC_PATTERNS + adapter.EnterpriseOps.critic_patterns

    def reasons(text):
        return [why for pattern, why in patterns if re.search(pattern, text)]

    # Every line of the starting harness can appear in a change as context.
    for file in sorted(adapter.runner.HARNESSES.joinpath("default").iterdir()):
        assert reasons(file.read_text()) == []
    prose = (
        "Before you finish a request, check every field. Handle all requests "
        "in the environment the user names. Open the record, then compile a "
        "list of what changed. If a tool fails, invoke it once more."
    )
    assert reasons(prose) == []
    unsafe = [
        "key = os.environ['A_KEY']",
        "key = logging.os.environ.get('A_KEY')",
        "import os",
        "from subprocess import run",
        "    import httpx",
        "data = open('/tmp/x').read()",
        "eval(text)",
        "x.__class__.__subclasses__()",
        "Path.home() / '.ssh'",
        "(Path(__file__).parent / '../other').read_text()",
        "p.write_text(k)",
        "await self.llm_client.llm.ainvoke(messages)",
        "client = LLMClient('openai', 'm', key)",
    ]
    assert all(reasons(line) for line in unsafe)


def test_an_experiment_says_and_records_what_differs_from_the_defaults(
    tmp_path, monkeypatch, capsys
):
    class Reached(Exception):
        pass

    class Benchmark:
        harness_path = "harness"

        def __init__(self, experiment):
            pass

    class Stop:
        frontier_path = tmp_path / "none"

        def __init__(self, *args):
            pass

        def baseline(self):
            raise Reached

    monkeypatch.setattr(loop, "EVOLUTIONS", tmp_path)
    monkeypatch.setattr(loop, "EnterpriseOps", Benchmark)
    monkeypatch.setattr(loop, "Experiment", Stop)
    for name in ("usage_log", "timeout", "limit"):
        monkeypatch.setattr(loop.models, name, getattr(loop.models, name))
    unset = dict.fromkeys(adapter.runner.EXPERIMENT_DEFAULTS)
    chosen = {"experiment": "evolve-smoke", "accept_unreviewed_code": False}
    with pytest.raises(Reached):
        loop.run(SimpleNamespace(**(unset | chosen)))

    said = capsys.readouterr().out
    assert "Settings that differ from the defaults:" in said
    assert "rounds           1" in said and "proposer_model" not in said
    folder = tmp_path / "evolve-smoke" / "haiku-20-40-40"
    record = json.loads((folder / "experiment.json").read_text())
    assert record["changed"]["rounds"] == 1
    assert record["changed"]["editable"] == [".md"]
    # The search roles' model is the agent's here, and its file is kept.
    assert record["proposer_settings"]["llm_provider"] == "anthropic"
    assert "llm_api_key" not in record["proposer_settings"]


def test_code_edits_with_no_approval_start_only_once_the_risk_is_accepted(
    tmp_path, monkeypatch
):
    class Reached(Exception):
        pass

    class Stop:
        harness_path = "harness"

        def __init__(self, experiment):
            raise Reached

    monkeypatch.setattr(loop, "EVOLUTIONS", tmp_path)
    monkeypatch.setattr(loop, "EnterpriseOps", Stop)
    unset = dict.fromkeys(adapter.runner.EXPERIMENT_DEFAULTS)
    chosen = {"experiment": "evolve-smoke", "editable": [".md", ".py"]}

    def start(**options):
        accepted = {"accept_unreviewed_code": False} | options
        loop.run(SimpleNamespace(**(unset | chosen | accepted)))

    with pytest.raises(SystemExit, match="--accept-unreviewed-code"):
        start()
    assert list(tmp_path.iterdir()) == []
    # Accepted, or with a person approving each change, the experiment starts.
    with pytest.raises(Reached):
        start(accept_unreviewed_code=True)
    with pytest.raises(Reached):
        start(approval="each")


# -------------------------------------------------------------------- one round

TRAINING, VALIDATION, TEST = ["t1", "t2"], [f"v{n}" for n in range(1, 7)], ["h1", "h2"]


class Scores(Domain):
    """A benchmark whose every job has the scores it is given."""

    name = "made-up"
    harness_path = "harness"
    # Two groups of three validation tasks, for a filter to take a share of each.
    domain_of = {f"v{n}": "odd" if n % 2 else "even" for n in range(1, 7)}
    briefs = {"analyst": "the analyst's brief", "proposer": "the proposer's brief"}

    def __init__(self, scores):
        self.scores, self.jobs = scores, []

    def evolve_ids(self):
        return TRAINING

    def validation_ids(self):
        return VALIDATION

    def heldout_ids(self):
        return TEST

    def harness_dir(self, root):
        return root / self.harness_path

    def constitution(self, root):
        return "the rules", "the patterns"

    def run(self, root, runs_dir, job, ids, k, log_prefix=""):
        (runs_dir / "jobs" / job).mkdir(parents=True, exist_ok=True)
        self.jobs.append((job, list(ids)))

    def score(self, runs_dir, job, ids, k):
        return {i: TaskResult(rewards=[self.scores[job][i]]) for i in ids}, {}

    def load_trial(self, runs_dir, job, task_id, trial):
        return {"task_id": task_id, "job": job}


def experiment(tmp_path, monkeypatch, candidate_scores, **settings):
    """An experiment on a made-up benchmark, with the search roles stubbed."""
    repository = tmp_path / "repository"
    (repository / "harness").mkdir(parents=True)
    (repository / "harness" / "prompt.md").write_text("")
    gitops.git(repository, "init", "-q", check=True)
    gitops.git(repository, "add", "-A", check=True)
    gitops.git(repository, "commit", "-q", "-m", "the starting harness", check=True)
    flat = dict.fromkeys(TRAINING + VALIDATION + TEST, 0.5)
    candidate = dict(zip(VALIDATION, candidate_scores, strict=True))
    scores = {
        "base_training": flat,
        "base_validation": flat,
        "r0A": candidate,
        # The two parts a candidate is scored in when there is a filter.
        "r0A_filter": candidate,
        "r0A_rest": candidate,
        "r0A_training": dict.fromkeys(TRAINING, 0.75),
        "heldout_t0": flat,
        "heldout_t1": dict.fromkeys(TEST, 1.0),
    }
    cfg = RRSIConfig(T=3, k=1, m=1, b_min=1, b_max=1, delta=0.0)
    run = loop.Experiment(
        Scores(scores), cfg, repository, tmp_path, alpha=0.05, **settings
    )
    given = {}
    monkeypatch.setattr(loop.analyst, "analyze", lambda *args, **kwargs: {})

    def draft(t, variant, folder, report, digests, traces, current, *rest):
        given.update(traces=traces, current=current, told=rest[4])
        given.update(briefs=dict(run.domain.briefs))
        (folder / variant).mkdir(exist_ok=True)
        branch = f"{run.domain.name}/r{t}{variant}"
        checkout = gitops.worktree_add(
            repository, run.wt_root / f"r{t}{variant}", branch, run.branch
        )
        (checkout / "harness" / "prompt.md").write_text(f"round {t}\n")
        commit = gitops.commit_path(checkout, "harness", "a change")
        edit = {"id": "C1", "component": "prompt", "hypothesis": "it helps"}
        return Candidate(variant, [edit], branch=branch, commit=commit)

    monkeypatch.setattr(run, "_draft", draft)
    return run, given


def test_an_accepted_candidate_becomes_the_current_harness(tmp_path, monkeypatch):
    better = [0.9, 0.8, 1.0, 0.85, 0.95, 0.9]
    run, given = experiment(tmp_path, monkeypatch, better)
    run.baseline()
    start = run.frontier()["incumbent"]["commit"]
    run.round(0)
    state = run.frontier()
    assert state["promotions"] == 1 and state["incumbent"]["commit"] != start
    assert gitops.rev(run.repo, run.branch) == state["incumbent"]["commit"]
    assert state["incumbent"]["S"] == pytest.approx(0.9)
    # Scored on the validation tasks, then run on the training tasks for the
    # next round's traces.
    assert ("r0A", VALIDATION) in run.domain.jobs
    assert state["incumbent"]["training_job"] == "r0A_training"
    assert ("r0A_training", TRAINING) in run.domain.jobs
    decision = json.loads((run.runs / "r0" / "decisions.json").read_text())[0]
    assert decision["accept"] is True and decision["n"] == 6
    # The first decision's share of the budget, with no promotion before it.
    assert decision["level"] == budget.reuse_level(0.05, 1, 0, 1)
    assert [r["outcome"] for r in run.history.records()] == ["BASELINE", "ACCEPTED"]

    # The test runs: the starting harness and the accepted one, once each.
    rows = run.test()
    assert [(r["round"], r["test"]) for r in rows] == [(0, 0.5), (1, 1.0)]
    assert (run.runs / "test.csv").read_text().splitlines()[0] == (
        "round,commit,validation,test,missing_attempts"
    )


def test_a_candidate_that_is_no_better_is_dropped(tmp_path, monkeypatch):
    same = [0.5, 0.4, 0.6, 0.5, 0.45, 0.55]
    run, given = experiment(tmp_path, monkeypatch, same)
    run.baseline()
    start = run.frontier()["incumbent"]
    run.round(0)
    state = run.frontier()
    assert state["promotions"] == 0 and state["incumbent"] == start
    assert gitops.rev(run.repo, run.branch) == start["commit"]
    assert len(state["trajectory"]) == 2
    assert ("r0A_training", TRAINING) not in run.domain.jobs
    assert run.history.records()[-1]["outcome"] == "REJECTED"
    # Only the starting harness is run on the test tasks.
    assert [r["round"] for r in run.test()] == [0]


def test_a_candidate_that_does_no_better_on_the_filter_is_not_run_on_the_rest(
    tmp_path, monkeypatch
):
    run, _ = experiment(tmp_path, monkeypatch, [0.4] * 6, filter_share=0.5)
    run.baseline()
    start = run.frontier()["incumbent"]
    run.round(0)
    first = run.filter_ids(0, VALIDATION)
    # Two of each group's three tasks, and nothing else of the candidate's.
    assert len(first) == 4
    assert [job for job in run.domain.jobs if job[0].startswith("r0A")] == [
        ("r0A_filter", first)
    ]
    decision = json.loads((run.runs / "r0" / "decisions.json").read_text())[0]
    assert decision["stopped"] == "filtered_out" and decision["accept"] is False
    assert decision["filter"] == {
        "tasks": 4,
        "gain_points": -10.0,
        "threshold_points": 0.0,
        "passed": False,
    }
    assert run.frontier()["incumbent"] == start
    # The proposer is told no more than of a candidate the gate turned down.
    record = run.history.records()[-1]
    assert record["outcome"] == "REJECTED" and record["delta_S"] is None


def test_a_candidate_that_passes_the_filter_is_judged_on_every_validation_task(
    tmp_path, monkeypatch
):
    better = [0.9, 0.8, 1.0, 0.85, 0.95, 0.9]
    run, _ = experiment(tmp_path, monkeypatch, better, filter_share=0.5)
    run.baseline()
    run.round(0)
    first = run.filter_ids(0, VALIDATION)
    rest = [task for task in VALIDATION if task not in first]
    # The rest is run by itself, so no attempt of the filter is run again.
    assert [job for job in run.domain.jobs if job[0].startswith("r0A")] == [
        ("r0A_filter", first),
        ("r0A_rest", rest),
        ("r0A_training", TRAINING),
    ]
    decision = json.loads((run.runs / "r0" / "decisions.json").read_text())[0]
    assert decision["filter"]["passed"] is True
    # The gate's test is on all six tasks, the filter's among them.
    assert decision["accept"] is True and decision["n"] == 6
    state = run.frontier()
    assert state["promotions"] == 1 and state["incumbent"]["S"] == pytest.approx(0.9)
    kept = loop.EvalResult.load(run.eval_path(state["incumbent"]["job"]))
    assert list(kept.per_task) == VALIDATION


@pytest.mark.parametrize(("threshold", "passed"), [(30.0, True), (50.0, False)])
def test_the_filters_threshold_is_the_gain_a_candidate_must_be_above(
    tmp_path, monkeypatch, threshold, passed
):
    # Every task up by 0.35 to 0.45: a gain of about 40 points on any filter.
    better = [0.9, 0.95, 0.85, 0.9, 0.95, 0.85]
    run, _ = experiment(
        tmp_path, monkeypatch, better, filter_share=0.5, filter_threshold=threshold
    )
    run.baseline()
    run.round(0)
    decision = json.loads((run.runs / "r0" / "decisions.json").read_text())[0]
    assert 30 < decision["filter"]["gain_points"] < 50
    assert decision["filter"]["threshold_points"] == threshold
    assert decision["filter"]["passed"] is passed
    assert (("r0A_rest", ANY) in run.domain.jobs) is passed


def test_the_filter_takes_a_share_of_each_group_and_is_drawn_afresh_each_round(
    tmp_path, monkeypatch
):
    run, _ = experiment(tmp_path, monkeypatch, [0.5] * 6, filter_share=0.25, seed=3)
    ids = [f"a{n}" for n in range(12)] + [f"b{n}" for n in range(8)]
    run.domain.domain_of = {task: task[0] for task in ids}
    rounds = [run.filter_ids(t, ids) for t in range(6)]
    for drawn in rounds:
        # A quarter of each group, in the order of the tasks.
        assert [task[0] for task in drawn] == ["a"] * 3 + ["b"] * 2
        assert drawn == [task for task in ids if task in drawn]
    assert len({tuple(drawn) for drawn in rounds}) > 1
    # The same round of the same experiment draws the same tasks again.
    assert run.filter_ids(4, ids) == rounds[4]
    run.filter_share = 0.0
    assert run.filter_ids(0, ids) == []


def test_a_change_a_person_declined_is_recorded_as_declined(tmp_path, monkeypatch):
    run, _ = experiment(tmp_path, monkeypatch, [0.5] * 6)
    run.baseline()
    detail = json.dumps({"stage": "approval", "detail": "declined by a person"})
    declined = Candidate("A", [], gate_failure="smoke_fail", detail=detail)
    monkeypatch.setattr(run, "_draft", lambda *args: declined)
    run.round(0)
    decisions = json.loads((run.runs / "r0" / "decisions.json").read_text())
    assert decisions[0]["stopped"] == "declined by a person"
    # It was never scored, and the current harness is unchanged.
    assert ("r0A", VALIDATION) not in run.domain.jobs
    assert run.frontier()["promotions"] == 0


def test_the_pool_is_spread_over_the_ranking_and_moves_one_place_on_each_round(
    tmp_path, monkeypatch
):
    run, _ = experiment(tmp_path, monkeypatch, [0.5] * 6, pool_share=0.3)
    # Ten tasks ranked t0 (lowest) to t9; three attempts each, the second the lowest.
    results = {f"t{n}": TaskResult(rewards=[n / 10, n / 20, n / 10]) for n in range(10)}
    assert list(run.pool(0, results)) == ["t0", "t3", "t6"]
    assert list(run.pool(1, results)) == ["t1", "t4", "t7"]
    # Over the rounds every task comes into the pool.
    read = {task for t in range(4) for task in run.pool(t, results)}
    assert read == set(results)
    # The attempt shown is the task's lowest-scoring one.
    assert run.pool(1, results) == {"t1": 1, "t4": 1, "t7": 1}
    # A share of 1 is every task, and of nearly none still one task.
    run.pool_share = 1.0
    assert set(run.pool(5, results)) == set(results)
    run.pool_share = 0.01
    assert list(run.pool(0, results)) == ["t0"]
    assert run.pool(0, {}) == {}


def test_the_ends_pick_takes_the_lowest_and_the_highest_22_to_6(tmp_path, monkeypatch):
    run, _ = experiment(
        tmp_path, monkeypatch, [0.5] * 6, pool_share=0.5, pool_pick="ends"
    )
    # 56 tasks ranked t00 to t55, two attempts each, the first the higher.
    results = {
        f"t{n:02d}": TaskResult(rewards=[(n + 1) / 100, n / 100]) for n in range(56)
    }
    # Half of 56 is 28: the 22 lowest and the 6 highest, as in RRSI.
    shown = run.pool(3, results)
    assert list(shown) == [f"t{n:02d}" for n in [*range(22), *range(50, 56)]]
    # A low task by its lowest attempt, a high one by its highest.
    assert shown["t00"] == 1 and shown["t55"] == 0
    # The same tasks every round: this pick does not move.
    assert run.pool(4, results) == shown


def test_both_roles_are_given_every_training_score_with_the_pool_marked(
    tmp_path, monkeypatch
):
    run, given = experiment(tmp_path, monkeypatch, [0.5] * 6, pool_share=0.5)
    run.baseline()
    run.round(0)
    # One of the two training tasks is in the pool, and only its trace is given.
    assert list(given["traces"]) == ["t1"]
    for role in ("analyst", "proposer"):
        brief = given["briefs"][role]
        assert brief.startswith(f"the {role}'s brief")
        assert brief.endswith("t1 0.50 *\nt2 0.50")
    # The next round's pool starts one place further.
    run.domain.scores["r1A"] = run.domain.scores["r0A"]
    run.round(1)
    assert list(given["traces"]) == ["t2"]
    assert given["briefs"]["analyst"].endswith("t1 0.50\nt2 0.50 *")
    # The benchmark's own texts are left as they were.
    assert Scores.briefs["analyst"] == "the analyst's brief"


def test_the_proposer_reads_training_tasks_and_hears_only_the_outcome(
    tmp_path, monkeypatch
):
    run, given = experiment(tmp_path, monkeypatch, [0.5, 0.4, 0.6, 0.5, 0.45, 0.55])
    run.baseline()
    run.round(0)
    held_out = set(VALIDATION) | set(TEST)
    assert set(given["traces"]) <= set(TRAINING)
    assert set(given["current"].per_task) == set(TRAINING)
    assert given["told"] == []
    # The second round is told of the first proposal: what it was and whether
    # it was accepted, and no number from the validation tasks.
    run.domain.scores["r1A"] = run.domain.scores["r0A"]
    run.round(1)
    assert given["told"] == [
        {
            "t": 0,
            "variant": "A",
            "edit_id": "C1",
            "component": "prompt",
            "hypothesis": "it helps",
            "outcome": "REJECTED",
        }
    ]
    assert not held_out & set(json.dumps(given["told"]).replace('"', " ").split())
    # The record kept for people does hold the numbers.
    assert run.history.records()[1]["delta_S"] == pytest.approx(0.0)
