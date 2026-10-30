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
smoke --limit 1 --domain csm` is a quick variant without editing anything,
and `--split haiku-20-40-40:training` runs only that set of a split (see
below).
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
    summary.md                   how the run went and what went wrong, one page
    run.json                     every setting, the task list and the totals
    tasks.csv                    one row per attempt: verdict, checks, tool errors, tokens, cost
    verifiers.csv                one row per attempt and check: verdict, diagnosis, kind, step
    cells.csv                    one row per failed check, touched row and condition
    changes.csv                  one row per attempt and changed table
    run.log                      the benchmark's log of every request
    tasks/<task_id>/
      attempt-<n>.md             the attempt as a page: request, turns, changes, checks
      attempt-<n>.json           the executor's complete record of the attempt
      attempt-<n>.changes.json   every row the attempt inserted, updated or deleted
```

To read a run, open `summary.md` first: the headline table, then the failed
checks by step and the cells wrong by column, then the worst tasks with
their dominant failure. For a task on that list, open its `attempt-<n>.md`:
under each failed check, the row the agent wrote, which turn wrote it, and
each condition marked `ok` or `WRONG` with the value found and the turn that
set it. `cells.csv` holds the same conditions as data, for filtering across
an evaluation (every wrong `status`, say), and `changes.csv` shows what each
attempt touched, including tables no check mentions.

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
the run's database is deleted, the runner reads the final state and compares
it with the seed (`attempt-<n>.changes.json`: every row inserted, updated or
deleted), then takes each failed count check apart at its top-level ANDs and
tests every condition on its own against the rows the agent inserted or
updated in the check's table, fetching the value actually found where a
condition fails. A check expecting no rows gets the surplus rows instead; a
check that reads one value is already wanted against actual; the few queries
with a top-level OR or another shape keep expected and actual only. Each
failed check is then traced back to the agent's turns: the calls that write
the check's table (a writing verb and the table's name in the tool's name),
which of them set each wrong cell, and the `step` the check failed at:
`skipped` (no call wrote the table), `failed` (a call returned an error),
`wrong` (the row is there with wrong cells) or `attempted` (the calls
succeeded and the row is still not what the check wants). The `kind` column
of `verifiers.csv` says what the diagnosis found: `cells`, `no_rows`,
`too_many_rows`, `value` or `unsupported`.

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

Every evaluation folder is written to be read without a tracing server:

- `summary.md`: a page on how the run went and what went wrong, by table and
  column, with the worst tasks and where the files are;
- `tasks.csv` (one row per attempt), `verifiers.csv` (one per attempt and
  check, with the failure's kind and step), `cells.csv` (one per failed
  check, touched row and condition, with the turn that set a wrong cell) and
  `changes.csv` (one per attempt and changed table);
- `tasks/<task_id>/attempt-<n>.md`: the attempt as a page, the request, what
  the agent did turn by turn, the changes against the seed, and every check
  with, under a failure, each condition marked ok or WRONG and the turn
  behind it, beside the full record (`.json`) and the changes
  (`.changes.json`).

## Splitting the tasks

```
mas-optimiser split --experiment baseline-haiku --name haiku-20-40-40 --seed 0
```

Draws the training, validation and test sets and writes
`configs/splits/<name>.json`. For each domain it reads the experiment's
latest full evaluation that has no label and no command-line option that
changes what is measured (`--domain`, `--concurrency`, `--out` and the
tracing switch change nothing), and stops if a domain with a full
evaluation has none that qualifies, or if the evaluations chosen differ in
model, mode, harness or attempts. The
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
with one exception: in many rows the HR server replaces `created_at` and
`updated_at` with the time it loads the seed (which rows depends on the seed
file), so those two HR columns are left out of the comparison. The CSM
server returns at most 100 rows to a query that sets no `LIMIT` of its own. Both folders are found from the repository,
whatever the working directory.

## Recomputing an evaluation

```
mas-optimiser rescore runs/<experiment>/<evaluation> [...]
```

Recomputes every attempt of an evaluation from its saved record and its
changes against the seed, without running the agent: the final database is
rebuilt locally, every check is run again as the benchmark's engine would run
it, each failure is diagnosed and traced, and the tables, pages and summary
are rewritten. Use it after a change to the diagnosis or to the checks. An
attempt with no saved changes (a run that raised before it finished) is left
as it was. The record keeps the verdicts the run first produced as
`verification_results_as_run` beside the recomputed ones.

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
