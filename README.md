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
`uv.lock`, unpacks the benchmark's seed databases and runs the unit tests.
Afterwards, run commands inside the environment with `uv run`, for example
`uv run pytest`.

## Benchmark servers

Each [EnterpriseOps-Gym](https://github.com/ServiceNow/EnterpriseOps-Gym)
domain runs as its own server in Docker. Start the ITSM, CSM and HR servers,
then check that they answer:

```bash
docker compose -f docker/compose.yaml up -d
uv run pytest -m integration
```

Stop them with `docker compose -f docker/compose.yaml down`.

## Running benchmark tasks

Copy `.env.example` to `.env` and add the API key. With the servers up, run
the benchmark's own ReAct agent on the first five ITSM tasks:

```bash
uv run python -m mas_optimiser.benchmarks.enterpriseops.runner \
    --model configs/models/haiku-5-5.yaml --domain itsm --limit 5
```

`./scripts/demo.sh` does the same, starting the servers first if needed.

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
uv run python -m mas_optimiser.benchmarks.enterpriseops.replay \
    runs/<stamp>/tasks/<task_id>.json
```

`--full-policy` prints the whole policy rather than its first lines, and
`--max-chars N` changes where long tool results and model text are clipped.

## Watching a run in Langfuse

[Langfuse](https://langfuse.com) shows each task as a tree: the task, every
model call with the messages sent and the reply, its tool calls, token usage
and cost, and the verifier verdict as two scores (`task_success` and
`verifier_fraction`). Tasks from one run share a session named after the run
folder, and traces are tagged with the domain, mode, arm and model.

Self-host it with Docker outside this repository (the stack needs about
16 GiB of memory):

```bash
git clone https://github.com/langfuse/langfuse.git ~/dev/tools/langfuse
cd ~/dev/tools/langfuse
# Put the secrets, and the LANGFUSE_INIT_* values that create the first
# organisation, project, API keys and user, in a .env file, then:
docker compose up -d
```

The UI is at http://localhost:3000 once the web container logs "Ready".
Copy the project's public and secret keys into this repository's `.env`
(names in `.env.example`); the runner traces whenever they are set and runs
untraced otherwise. `--no-trace` turns tracing off for one run.

`--thinking-summary` asks the model for a summary of its reasoning on each
call so the trace shows it. This changes the agent's behaviour and cost, so
use it for demonstrations only, never for a run whose numbers are reported;
`run.json` records whether it was on.

## Licence

MIT; see `LICENSE`. Third-party material is listed in `NOTICE.md`.
