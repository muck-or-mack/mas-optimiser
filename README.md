# mas-optimiser

Planning-layer evolution for multi-agent systems. An optimiser proposes
changes to how a planner-executor system plans (its planner prompt, plan
constraints and replanning rules) and keeps a change only when a paired test
on tasks it has not seen supports it. The system is evaluated on
[EnterpriseOps-Gym](https://github.com/ServiceNow/EnterpriseOps-Gym), a
benchmark of enterprise IT-operations workflows graded by SQL checks on the
final database state.

Status: early development. The benchmark's own agent can be run on its
tasks; the planner and the optimiser are not written yet.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer (uv
fetches a suitable Python if the system one is older). From the repository
root:

```bash
./setup.sh
```

This fetches the benchmark submodule, installs exactly the versions in
`uv.lock` into `.venv/`, unpacks the benchmark's seed databases and runs the
unit tests. Then activate the environment in each new terminal:

```bash
source .venv/bin/activate
```

The commands below assume it is active. Without activating, `uv run <command>`
runs any of them; the scripts in `scripts/` work either way.

## Benchmark servers

Each [EnterpriseOps-Gym](https://github.com/ServiceNow/EnterpriseOps-Gym)
domain runs as its own server in Docker. Start the ITSM, CSM and HR servers,
then check that they answer:

```bash
docker compose -f docker/compose.yaml up -d
pytest -m integration
```

Stop them with `docker compose -f docker/compose.yaml down`.

## Running an experiment

Copy `.env.example` to `.env` and add the API key. An experiment is a short
YAML file in `configs/experiments/` that sets the model, domain, mode, task
limit and tracing options, with a comment on each; `smoke.yaml` is the
annotated example and `demo.yaml` the two-task demonstration. Run one by
name:

```bash
mas-optimiser run smoke
```

Any option on the command line overrides the file, so `mas-optimiser run
smoke --limit 1 --domain csm` is a quick variant without editing anything.
To define a new experiment, copy `smoke.yaml` under a new name. Models are
named the same way: `model: haiku-5-5` means `configs/models/haiku-5-5.yaml`,
and a path works in place of any name. The run checks that the domain's
server answers before it starts, and names the servers' start command if not.

`./scripts/demo.sh` starts the servers if needed and runs the `demo`
experiment. `mas-optimiser --help` lists the commands, `mas-optimiser run
--help` the options, and every script in `scripts/` and `setup.sh` answers
`-h`.

Each run writes a folder under `runs/` (gitignored) with `results.csv` (one
row per task), `run.json` (settings and totals), `run.log` (the benchmark's
log) and `tasks/` (the executor's result for each task, or the error if it
never started). A task whose run raises an error counts as failed and the
error count is reported separately; the benchmark keeps no token counts for
such a run.

## Reading a run

Each `tasks/<task_id>.json` file is the executor's complete record of one
task, but as a single JSON document it is hard to read. Print it as a
transcript instead: the policy and request the agent was given, each model
turn with its tool calls, each tool result, and the verifier table at the end:

```bash
mas-optimiser replay runs/<stamp>/tasks/<task_id>.json
```

`--full-policy` prints the whole policy rather than its first lines, and
`--max-chars N` changes where long tool results and model text are clipped.

## Watching a run in Langfuse

[Langfuse](https://langfuse.com) shows each task as a tree: the task, every
model call with the messages sent and the reply, its tool calls, token usage
and cost, and the verifier verdict as two scores (`task_success` and
`verifier_fraction`). Tasks from one run share a session named after the run
folder, and traces are tagged with the domain, mode, arm and model.

One command installs and starts a self-hosted copy with Docker, outside this
repository (the stack wants about 16 GiB of memory):

```bash
scripts/langfuse.sh up
```

The first run clones Langfuse into `~/dev/tools/langfuse`, creates an
account for you (your git name and email, a generated password) and the
project, starts the containers, waits for them, and writes the project's
keys into this repository's `.env`. It ends by printing the sign-in details;
`scripts/langfuse.sh login` prints them again and `scripts/langfuse.sh down`
stops the stack. The UI is at http://localhost:3000. The runner traces
whenever the keys are set and the experiment's `trace` is true (the default);
`--no-trace` turns tracing off for one run.

`thinking_summary: true` in an experiment, or `--thinking-summary` on the
command line, asks the model for a summary of its reasoning on each call so
the trace shows it. This changes the agent's behaviour and cost, so use it for
demonstrations only, never for a run whose numbers are reported; `run.json`
records whether it was on.

## Licence

MIT; see `LICENSE`. Third-party material is listed in `NOTICE.md`.
