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
YAML file in `configs/experiments/` that sets the model, the domain or
domains, mode, task limit and tracing options, with a comment on each;
`smoke.yaml` is the annotated example, `demo.yaml` the two-task
demonstration and `baseline-haiku.yaml` the full baseline (every task of the
three domains, three runs each). Run one by name:

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

## Reading the results

Results live under `runs/` (gitignored), grouped by experiment and then by
evaluation, where an evaluation is one model on one domain's tasks:

```
runs/<experiment>/
  index.csv                      one row per evaluation: settings and totals
  <evaluation>/
    run.json                     every setting, the task list and the totals
    tasks.csv                    one row per task run
    verifiers.csv                one row per verifier per run
    run.log                      the benchmark's log of every request
    tasks/<task_id>/attempt-<n>.json  the executor's complete record of one run
```

`attempts` in the experiment file (or `--attempts`) runs every task that many
times, each run from a freshly seeded database, so that a task the agent
always fails can be told from one it fails half the time; `tasks.csv` has a
row per run and `run.json` counts the tasks that passed every attempt.
`concurrency` (default 4) is how many runs are in flight at once. Each row
also carries the run's cost in US dollars, priced with the table in the model
file (`configs/models/<model>.yaml`, which records when the prices were
checked), and `run.json` and `index.csv` carry the evaluation's total.

A task passes when every one of its verifiers passes. Two things differ from
the benchmark's own count, and the `upstream_` columns of `tasks.csv` and
`verifiers.csv` give that count alongside. The benchmark keys verdicts by
verifier name, so where a task gives two verifiers the same name only the
last one counts; in 59 of the 308 oracle tasks names clash, and in 27 of
those the shared name hides different checks. Here an exact copy of a check
(32 tasks simply repeat their verifier list) counts once, and every distinct
check counts. And 33 count checks in seven tasks give their expected value as
the string `'1'`, which the benchmark compares with `==` against the integer
the database returns, so they can never pass; here count checks are compared
as numbers.

A failed verifier says only that its whole SQL condition did not hold. Before
the run's database is deleted, each failed count check is taken apart
condition by condition: the query is re-run with its first one, two, three
conditions until the count falls short, every remaining condition is then
tested on top of the ones that hold, and for a failing `column = value` the
value actually found is fetched. The result is the `diagnosis` column of
`verifiers.csv` and the `why:` line of a replay, such as
`channel = 'phone' (actual: chat); priority = 'moderate' (actual: None)` or
`nothing matches incident_id = 'INC_024'`. A check expecting no rows gets the
surplus rows instead. About 95% of the verifiers are count checks this
applies to; the rest keep expected and actual only.

An evaluation folder is named by its start time and domain, then anything
that makes it differ from the experiment file: a `--label` word, an
overridden model or mode as `model=...`, and `limitN` when only the first N
tasks ran. So `runs/smoke/20261027-093012-itsm-limit5/` is the smoke
experiment's first five ITSM tasks run on 27 October. The experiment, not
the model, domain or date, is the folder level, because it is the question
being asked; the others are columns of `index.csv`, where any cut of the
results is one table away. A task whose run raises an error counts as failed
and the error count is reported separately; the benchmark keeps no token
counts for such a run.

Each `attempt-<n>.json` file is the executor's complete record of one task
run, but as a single JSON document it is hard to read. Print it as a
transcript instead: the policy and request the agent was given, each model
turn with its tool calls, each tool result, and the verifier table at the end:

```bash
mas-optimiser replay runs/<experiment>/<evaluation>/tasks/<task_id>/attempt-1.json
```

`--full-policy` prints the whole policy rather than its first lines, and
`--max-chars N` changes where long tool results and model text are clipped.

## Splitting the tasks

```
mas-optimiser split --experiment baseline-haiku --name haiku-20-40-40 --seed 0
```

Draws the training, validation and test sets and writes
`configs/splits/<name>.json`. For each domain it reads the experiment's
latest full evaluation that has no label and no command-line overrides, and
stops if those evaluations differ in model, mode, harness or attempts. The
strata are domain crossed with baseline outcome (all, some or none of the
task's attempts passed), so each set has the same mix of domains and
difficulty. Tasks listed in `configs/splits/forced-training.yaml` (read or
inspected by hand, dataset defects, crashes) go into training before the
draw; the file must exist, and `--forced` names another one.
`--proportions` sets the shares (default `0.2 0.4 0.4`).

A split is drawn once. The command refuses a name that already has a file,
since a redraw could move tasks someone has read into a sealed set. The file
records the seed, proportions, evaluations read, code version, forced list
and every task's stratum; those fix the draw, and a unit test checks that
the committed split follows from its own record, so a clean clone can verify
it without the local runs. The proposer reads training traces only, the
acceptance gate decides on validation and returns its verdict alone, and the
test set is opened once for the reported number. Paths here are inside the
repository, whatever the working directory.

## Local copies of the seed databases

```
mas-optimiser schema
```

Seeds a database on each domain's server from every seed file its tasks
use, reads the CREATE statements of its tables and indexes back, checks
that all of a domain's seed files share one schema, and writes it to
`data/schemas/<domain>.sql`. With that file, `seeds.build_seed_database`
builds a local SQLite copy of any task's starting database, so new checks
can be tested without the servers. The copies match the servers' databases
row for row (`uv run pytest -m integration tests/integration/test_seeds.py`),
with one exception: the HR server sets `created_at` and `updated_at` to the
time it loads a seed. The CSM server returns at most 100 rows to a query
that sets no `LIMIT` of its own. The
project's own check files, one per task under `data/checks/`, are
described in `data/checks/README.md`; the runner scores the benchmark's
checks until they are complete. Both folders are found from the repository,
whatever the working directory.

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
