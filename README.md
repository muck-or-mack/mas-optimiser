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

Documentation: <https://mas-optimiser.readthedocs.io/>, rebuilt from `main`
on every push. To read a change before it is pushed, `./scripts/docs.sh`
builds the documentation and opens it in the browser.

How it all works, file by file, with one attempt followed from the command
to the wrong cell and the turn that set it: `docs/how-it-works.md`, with
its figures in `docs/figures/`.

## Install

One command, from the repository root:

```bash
./setup.sh
```

This installs [uv](https://docs.astral.sh/uv/), which builds the Python
environment, into `~/.local/bin` if the machine has none (the version the
tests run on; it needs `curl` or `wget` and no administrator rights). It
then fetches the two submodules (the benchmark, and the loop code that
`mas-optimiser evolve` is built on), installs exactly the versions in
`uv.lock` into `.venv/`, with a Python of 3.11 or newer that uv fetches if
the system has none, unpacks the benchmark's seed databases and runs the
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

## Serving an open-weight model

An open-weight model runs on the machine's own GPUs, served by
[vLLM](https://docs.vllm.ai/). The package starts the server from the
model's file:

```bash
mas-optimiser server qwen3.6-27b
```

`configs/models/qwen3.6-27b.yaml` ends in a `server` block: the release of
vLLM, and the options the server starts with (how many GPUs the model is
spread over, the longest prompt it takes, and the model card's options for
tool calling). The first time, the command installs that release of vLLM
with uv into an environment of its own, apart from the package's
(`~/.cache/mas-optimiser/vllm-<release>/`, about 19 GB with the kernels
vLLM fetches for itself; a few minutes on a fast connection). It then
starts the server as a background process that outlives the terminal,
listening where the file's `llm_api_endpoint` says and reachable from this
machine only, and prints each command it runs. Docker is not needed, and
neither are administrator rights: the machine needs its NVIDIA driver and
nothing else.

The first start also downloads the model's weights (56 GB for
Qwen3.6-27B) into the user's Hugging Face cache, `~/.cache/huggingface`, or
into the folder `--cache <folder>` names, and then loads the model. Run the
same command again at any time: it starts nothing new and says whether the
server is still `loading` or `ready`. The server's output is in
`~/.cache/mas-optimiser/<name>.log`.

```bash
mas-optimiser server qwen3.6-27b --stop
```

ends the server. The environment and the weights stay, so the next start
installs and downloads nothing.

| The command says | What it means | What to do |
|---|---|---|
| `loading` | the server is running and does not answer yet | wait; `tail -f` on the log shows progress |
| `ready at <address>` | the server answers | run an experiment with `--model <name>` |
| the server `stopped by itself` | it ended without being asked, for instance for lack of GPU memory | read the log, then `--stop` and start again |

With `--docker` the same release runs as a container of vLLM's image
instead, named after the model file, with the weights in the Docker volume
`mas-optimiser-models` (or the folder `--cache` names); `--docker --stop`
removes the container and `docker logs -f <name>` shows its output. This
needs Docker, room for the image (about 10 GB to download) on Docker's
disk, and NVIDIA's Container Toolkit
([its install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)),
which takes administrator rights to install.

Another model is another file in `configs/models/` with its own `server`
block; a file without one, such as a hosted model's, is refused. Releases
of vLLM after 0.32.0 need an NVIDIA driver that supports CUDA 13
(`nvidia-smi` shows the driver's CUDA version on its first line).

The command has been run end to end on one GPU with a small model: vLLM
installed, the server started, a task run against it, the server stopped.
The options in the Qwen3.6-27B file, and `--docker` with vLLM's own image,
have not been run yet.

## Running an experiment

Copy `.env.example` to `.env` and add the API key. An experiment is a short
YAML file in `configs/experiments/` that sets the model, the domain or
domains, mode, task limit and tracing options, with a comment on each;
`smoke.yaml` is the annotated example, `demo.yaml` the two-task
demonstration and `baseline-haiku.yaml` the full baseline (every task of the
three domains, three attempts each). Run one by name:

```bash
mas-optimiser run smoke
```

Any option on the command line overrides the file, so `mas-optimiser run
smoke --limit 1 --domain csm` is a quick variant without editing anything,
`--task <task_id>` runs one chosen task (repeat it for several), and
`--split haiku-20-40-40:training` runs only that set of a split (see
below). Without `--split` a run takes every task. The summary of a run of
a split's validation or test set gives the headline and leaves out what
went wrong ("Reading the results" in the documentation, section 8).
Experiments give the agent every tool its domain's server offers
(`mode: all_tools`); `--mode oracle` gives it only the tools the benchmark
lists with each task, the setting that published results use (see "What is
fixed and what is optimised" in the documentation).
To define a new experiment, copy `smoke.yaml` under a new name. Models are
named the same way: `model: haiku-5-5` means `configs/models/haiku-5-5.yaml`,
and a path works in place of any name. The command checks that the domain's
server answers before it starts, and names the servers' start command if not.

Prompt caching is on by default (`prompt_caching: true`; `--no-prompt-caching`
turns it off for one command). Every call of an attempt repeats most of the
call before it: the tool descriptions, the policy and the conversation so
far. With caching the provider stores that input and bills it at about a
tenth of the input rate when it is sent again within five minutes, and at a
quarter more than the input rate when it is first stored. The model receives
the same input either way. In a check on training tasks the same calls cost
about half as much with each task's own tools and about a fifth as much with
every tool. It is built for Anthropic models only, since it uses that
provider's request field and tool format: with any other provider the
setting does nothing and the run says so. `evaluation.json` records whether
it was used, and costs with and without it are not comparable.

A call to the model has a time limit of 300 seconds (`model_timeout: 300`;
`--model-timeout <seconds>` changes it for one command). A try that gets no
answer in that time is given up and repeated, up to nine tries in all, and
if none succeeds the attempt is recorded as an error and counted as a
failure. Without a limit, a connection that goes silent would hold the run
for ever; real calls answer in seconds. The limit is set on the provider's
own client, for Anthropic models and for models reached through an
OpenAI-style client (the providers `openai`, `vllm` and `openrouter`); with
any other provider none is set and the run says so.

A self-hosted open-weight model is run the same way, through its own model
file. `configs/models/qwen3.6-27b.yaml` is one for Qwen3.6-27B served by
vLLM, which answers in the OpenAI API's format: the file gives the provider
`vllm`, the server's address and no API key, and its `server` block says how
the server is started ([Serving an open-weight model](#serving-an-open-weight-model)).
With the server ready,

```
mas-optimiser run smoke --model qwen3.6-27b
```

runs the smoke experiment on that model. Nothing is billed per token, so
the cost in dollars is zero and the tokens and seconds, recorded as for any
model, are the measures of cost. Prompt caching does not apply. This path
has been run against a live vLLM server with a small model of the same
family (Qwen3-0.6B, with the task's own tools), and not yet with
Qwen3.6-27B.

The agent that runs the tasks comes from a harness folder. `harnesses/default/`
is the starting one: its `agent.py` is the benchmark's own agent loop, 150
lines, and its `prompt.md`, empty to start with, is text the agent adds to
the end of every task's system message. `harness: default` in the
experiment file names it, and `--harness <folder>` takes any copy of the
folder by its path, so a changed copy is run and compared like any other
harness. Everything outside the folder (the tasks, the servers, the checks,
the scoring) is the same for every harness; see "What is fixed and what is
optimised" in the documentation. `evaluation.json` records the harness an
evaluation used.

`./scripts/demo.sh` starts the servers if needed and runs the `demo`
experiment. `mas-optimiser --help` lists the commands, `mas-optimiser run
--help` the options, and every script in `scripts/` and `setup.sh` answers
`-h`.

## A demonstration in ten minutes

One attempt at one ITSM task, watched live, then read. It costs a few cents.

1. Start Docker Desktop, then the tracing UI (optional, once):
   `scripts/langfuse.sh up`, and open http://localhost:3000.
2. Run one task of the `demo` experiment (the servers start if needed):
   ```
   scripts/demo.sh --limit 1
   ```
3. While it runs, open the newest session in Langfuse: the trace grows one
   turn at a time, each with the model's reasoning summary, its tool call
   and the server's answer.
4. The command ends by printing the evaluation folder. Open its `summary.md`
   for the verdict, then `tasks/<task_id>/attempt-1.md` for the attempt as
   a page: the turns, every row the agent wrote, and each check as a table
   of what it wanted against what it found.
5. To query the database the attempt ended with:
   `mas-optimiser rebuild <folder> <task_id> 1`
   writes `attempt-1.sqlite` beside the page.
6. `docs/how-it-works.md` follows a made-up attempt with the same parts
   through the code, file by file, and `docs/data-model/itsm.md` names
   every table the checks query.

## Reading the results

Results live under `evaluations/` (gitignored), grouped by experiment and then by
evaluation, where an evaluation is one model on one domain's tasks:

```
evaluations/<experiment>/
  index.csv                      one row per evaluation: settings and totals
  <evaluation>/
    summary.md                   how the evaluation went and what went wrong, one page
    evaluation.json                     every setting, the task list and the totals
    tasks.csv                    one row per attempt: verdict, checks, tables by result, tool errors, tokens, cost
    checks.csv                   one row per attempt and check: verdict, diagnosis, kind, failure, patch
    cells.csv                    one row per failed check, touched row and condition: wanted against found
    tables.csv                   one row per attempt and table: the checks on it, the rows changed, the result
    failures.csv                 one row per table, column and kind of failure, most frequent first
    evaluation.log                      the benchmark's log of every request
    tasks/<task_id>/
      attempt-<n>.md             the attempt as a page: request, tables, tools, checks, turns, rows
      attempt-<n>.json           the executor's complete record of the attempt
      attempt-<n>.changes.json   every row the attempt inserted, updated or deleted
```

`docs/results.md` ("Reading the results" in the documentation) follows one
small run through these files: every column of every file, the words the
results use, and how to read one attempt's page.

To read an evaluation, open `summary.md` first: the headline table (a row
per measure, with what it counts), then the tables that were wrong or
missed and the most frequent failures, then the tasks that passed least
with their most frequent failure. For a task on that list, open its `attempt-<n>.md`:
under each failed check, the row the agent wrote, which turn wrote it, and
each condition marked `ok` or `WRONG` with the value found and the turn that
set it. `cells.csv` holds the same conditions as data, for filtering across
an evaluation (every wrong `status`, say). `tables.csv` says for each
attempt which tables the checks look at and how the attempt did on each:
`correct`, `wrong` (changed, and a check on it failed) or `missed` (a check
on it failed and the attempt changed nothing in it), with the tables it
changed that were `not asked for`. `failures.csv` ranks what went wrong
across the evaluation: tables not written, and wrong cells by column with
whether the value was passed wrong or left out of the call.

`attempts` in the experiment file (or `--attempts`) runs every task that many
times, each attempt from a freshly seeded database, so that a task the agent
always fails can be told from one it fails half the time; `tasks.csv` has a
row per attempt and `evaluation.json` counts the tasks that passed every attempt.
`concurrency` (default 4) is how many attempts are in flight at once. Each row
also carries the attempt's cost in US dollars, priced with the table in the model
file (`configs/models/<model>.yaml`, which records when the prices were
checked), and `evaluation.json` and `index.csv` carry the evaluation's total.

A task passes when every one of its checks (the benchmark's "verifiers") passes.
Three things differ from
the benchmark's own count, and the `benchmark_` columns of `tasks.csv` and
`checks.csv` give that count alongside. All three are applied to a task's
check list before each attempt (`checks.prepare`), so the server's verdicts
come back for every check under a unique name. The benchmark keys verdicts by
name, so where a task gives two checks the same name only the
last one counts; in 59 of the 308 oracle tasks names clash, and in 27 of
those the shared name hides different checks. Here an exact copy of a check
(32 tasks simply repeat their list) counts once, and every distinct
check counts. And 33 count checks in seven tasks give their expected value as
the string `'1'`, which the benchmark compares with `==` against the integer
the database returns, so they can never pass; here count checks are compared
as numbers. And a short patch list (`data/check_patches.json`) excludes the
few checks that no correct attempt can satisfy and repairs the few whose query
names the wrong record, each entry with its evidence; the benchmark's own
verdict on every patched check is still recorded beside ours (the
`benchmark_passed` and `patch` columns of `checks.csv`). A task whose every
check is excluded cannot be judged and is not run (one ITSM task at the
current patch list).

How a failed check is diagnosed cell by cell and traced to the turn that
set each wrong value, and what the `kind` and `failure` columns of `checks.csv`
mean, is in `docs/how-it-works.md` (sections 2.6 and 2.7), which follows
one real attempt through the whole pipeline.

A run is one invocation of an experiment, and its folder is named by its
start minute and any `--label` word: `evaluations/smoke/2026-10-27-0930/`,
or `2026-10-27-0930-retry` with a label; the seconds are added only when a
second run starts within the minute. Inside it, one evaluation folder per
domain (`itsm/`, `csm/`, `hr/`), `run.json` with the settings and the
pooled totals, and a `summary.md` with the headline for the run as a whole
and a row per domain. Every other setting (model, mode, split, task limit,
command-line overrides) is a column of `index.csv` and a field of
`evaluation.json`, not part of a name. Runs made before 13 November 2026
were regrouped into this layout; their index rows carry the same columns. The experiment, not
the model, domain or date, is the folder level, because it is the question
being asked; the others are columns of `index.csv`, where any cut of the
results is one table away. An attempt that raises an error counts as failed
and the error count is reported separately; the benchmark keeps no token
counts for such an attempt.

Every evaluation folder is written to be read without a tracing server:

- `summary.md`: a page on how the evaluation went and what went wrong, by table and
  column, with the tasks that passed least and what each file holds;
- `tasks.csv` (one row per attempt), `checks.csv` (one per attempt and
  check, with the failure and its kind), `cells.csv` (one per failed
  check, touched row and condition, wanted against found, with the turn the
  value came from), `tables.csv` (one per attempt and table, with its
  result) and `failures.csv` (one per table, column and kind of failure);
- `tasks/<task_id>/attempt-<n>.md`: the attempt as a page: the request; one
  row per table the checks look at, with the result; the tools the task
  was given and the arguments each takes; every check with, under a
  failure, a table of each column wanted against found, marked ok or
  WRONG, with where the value came from; what the agent did turn by turn,
  each call with its arguments and what came back; and every row it
  inserted, updated or deleted. Beside it, the full record (`.json`) and
  the changes (`.changes.json`).

## Where to find what

| To find | Open | Written by |
|---|---|---|
| what every arm of an experiment shares, and what may differ | `docs/methodology/fixed-and-optimised.md` | by hand |
| whether it passed, and how many | `summary.md` (headline); `index.csv`, one row per evaluation of the experiment | `report.summary`, `runner.write_index_row` |
| the run as a whole: the headline pooled over its domains, then a row per domain | the run's `summary.md` and `run.json` | `runner.write_run_summary` |
| what went wrong overall: by table, then by failure and column; the tasks that passed least | `summary.md`, "What went wrong" | `report.summary` |
| what went wrong most often, as data | `failures.csv` | `runner.write_tables` |
| each attempt's verdict, tables by result, errors, tokens and cost | `tasks.csv` | `runner.task_outcome` |
| each check's verdict, kind, failure and patch | `checks.csv` | `checks.check_records` |
| every cell a failed check tests, wanted against found, and the turn its value came from | `cells.csv` | `report.cell_rows` |
| which tables each attempt got right, got wrong, missed or changed unasked | `tables.csv` | `report.table_rows` |
| every row each attempt changed in the database | `attempt-<n>.changes.json` | `states.diff` |
| one attempt, readable: tables, tools, checks, turns, rows | `tasks/<task_id>/attempt-<n>.md` | `report.attempt_text` |
| one attempt, complete and raw | `attempt-<n>.json` | the benchmark's executor |
| every tool the domain server offered, with its description and argument schema | `tools.json` | `runner.save_tools` |
| the database an attempt ended with | `mas-optimiser rebuild`, then `attempt-<n>.sqlite` | `rebuild` |
| what the model was sent and answered, call by call; a run in progress | Langfuse: a session per evaluation, a trace per attempt | `tracing` |
| the evaluation's settings and totals | `evaluation.json` | `runner.evaluate` |
| the benchmark's own log of the run | `evaluation.log` | the benchmark |
| the tasks' policy, request, checks and tools | the dataset and `vendor/enterpriseops-gym/data/revised/`; the corrections in `data/check_patches.json` | |
| the tables a domain has, with keys and references | `docs/data-model/<domain>.md`; the schema in `data/schemas/<domain>.sql` | `mas-optimiser schema` |
| which tasks are training, validation and test | `configs/splits/<name>.json` | `mas-optimiser split` |
| every run of an experiment, at a glance, with its domains | `mas-optimiser list <experiment>`, from `index.csv` | `listing` |
| whether a candidate beat the current harness, by how much, and the smallest gain the data could show | `comparison.md`, its data files and `figures/` in the folder `mas-optimiser compare` writes | `stats.compare` |
| whether that test can be trusted on this benchmark and model | `calibration.md` and its data files in the folder `mas-optimiser calibrate` writes | `stats.calibrate` |

## Splitting the tasks

```
mas-optimiser split --name domain-20-40-40
```

Draws the training, validation and test sets with a seeded shuffle and
writes `configs/splits/<name>.json`. The proposer reads training traces
only, the acceptance gate decides on validation and returns its verdict
alone, and the test set is opened once for the reported number.

| option          | what it sets                                            | default       |
|-----------------|---------------------------------------------------------|---------------|
| `--proportions` | shares of training, validation and test, summing to one | `0.2 0.4 0.4` |
| `--seed`        | seed of the shuffle; the same seed gives the same sets  | `0`           |
| `--balance`     | what every set holds the same mix of                    | `domain`      |
| `--experiment`  | the baseline run read for each task's difficulty        |               |
| `--seen`        | a file of tasks already read by hand, kept in training  | none          |

```
mas-optimiser split --name trial --proportions 0.6 0.2 0.2 --seed 7
mas-optimiser split --name plain --balance
mas-optimiser split --name by-difficulty --balance domain difficulty --experiment baseline-haiku
mas-optimiser split --name with-seen --seen seen-tasks.yaml
```

`--balance` takes `domain`, `difficulty`, both, or nothing after it. The
tasks are grouped by what is named and each group is shared out in the
proportions, so every set holds the same mix; with nothing named the tasks
are shuffled as one group. The more that is balanced, the smaller the groups,
and the set sizes can differ from the exact shares by a few tasks.

A task's difficulty is whether all, some or none of its attempts passed in a
baseline run, named by `--experiment` and given only with a balance on
difficulty. For each domain the command reads the experiment's latest full
evaluation that has no label and no command-line option that changes what is
measured (`--domain`, `--concurrency`, `--out` and the tracing switch change
nothing). It stops if a domain with a full evaluation has none that
qualifies, or if the evaluations chosen differ in model, mode, harness or
attempts, and the split covers the tasks that run scored. Without a balance
on difficulty the task list comes from the dataset and no run is needed.

`--seen` names a YAML file that gives each task id a reason. A task someone
has read cannot server as an unseen one, so these go into training before the
shuffle. Validation and test tasks are never read; only their scores are
looked at.

A split is drawn once. The command refuses a name that already has a file,
since a redraw could move tasks someone has read into a sealed set. The file
holds the seed, the proportions, the balance, what was read, the seen tasks
and the three lists of task ids, and no result of any single task. The split
`haiku-20-40-40`, used in the examples of this page, was drawn on 27 October
with both balances and 20 seen tasks. Paths here are inside the repository,
whatever the working directory.

## Local copies of the seed databases

```
mas-optimiser schema
```

Seeds a database on each domain's server from every seed file its tasks
use, reads the CREATE statements of its tables and indexes back, checks
that all of a domain's seed files share one schema, and writes it to
`data/schemas/<domain>.sql`, with a readable version at
`docs/data-model/<domain>.md`: every table, its key, its columns and the
tables it references. With the schema file, `seeds.build_seed_database`
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
mas-optimiser rescore evaluations/<experiment>/<evaluation> [...]
```

Recomputes every attempt of an evaluation from its saved record and its
changes against the seed, without running the agent: the final database is
rebuilt locally, every check is run again as the benchmark's engine would run
it, each failure is diagnosed and traced, and the tables, pages and summary
are rewritten, with the totals in `evaluation.json`, the evaluation's row
in the experiment's `index.csv` and, for one domain of a run, the run's
`run.json` and `summary.md`. Use it after a change to the diagnosis or to the
checks. An attempt with no saved changes (one that raised before it
finished) keeps its record and counts as failed, as the runner counted it;
a task with no check left to judge by has its pages rewritten and stays out
of the tables. The record keeps the verdicts the attempt first produced as
`verification_results_as_run` beside the recomputed ones. A condition on the
clock (`datetime('now', '-1 day')`) is judged at the time the attempt's
checks ran, so the result does not depend on the day of the rescore.

To look at the database one attempt ended with:

```
mas-optimiser rebuild evaluations/<experiment>/<evaluation> <task_id> <attempt>
```

writes it, rebuilt from the saved changes, as `attempt-<n>.sqlite` beside
the record. Open it with `sqlite3` or an editor's SQLite viewer to run a
check's query by hand or browse every table; it is the database `rescore`
judges on.

## Listing an experiment's evaluations

```
mas-optimiser list <experiment> [--domain itsm]
```

Prints the experiment's `index.csv` as a table, one block per run: a row
for the run as a whole, its domains' counts summed, then a row per domain,
with mode, model, label, attempts and checks passed, errors and cost. It
is the quickest way to find a run without reading folder names.

## Comparing two evaluations

```
mas-optimiser compare --experiment <experiment> --a <name> --b <name> [--split <name>] [--out <folder>]
```

Compares a candidate (`--b`) with the current harness (`--a`) task by task,
each side one evaluation per domain, on the tasks of a split's set (the
validation set by default) or on every task both sides scored. A side is
named by its run (`2026-11-03-0803`), by a day when the experiment ran
once that day, or by a run folder or evaluation folders given one `--a` or
`--b` at a time; names are resolved through the experiment's `index.csv`.
The two baseline runs of the same harness, which give the standard error
and the minimum detectable effect:

```
mas-optimiser compare --experiment baseline-haiku --a 2026-10-27-0528 --b 2026-11-03-0803 --split haiku-20-40-40
```

It writes its results as data: `tests.csv`, a row per outcome and set of
tasks with every number of the test; `rules.csv`, a row per decision rule;
`sides.csv`, the two sides with their settings, cost and time; and
`differences.csv`, a row per task. `comparison.md` is a report of those
files for reading, with the figures of `figures/` embedded: what is
compared, the result for both outcomes (checks passed and attempts fully
passed, in percent, their difference in percentage points), cost and time,
the result by domain, the test in full with what each quantity is, and the
decision rules. The method is in `docs/methodology/acceptance-gate.md`, and
how to read the report, part by part, in `docs/results.md` ("Reading a
comparison").
Running it on the 27 October and 3 November baselines, two runs of one
harness, gives the minimum detectable effect every later gain is measured
against.

## Checking the test on two runs of one harness

```
mas-optimiser calibrate --experiment <experiment> --a <run> --b <run> [--split <name>] [--out <folder>]
```

When two runs are of the same harness, any difference between them is
chance. The command pools each task's attempts from both runs, splits them
at random into two halves 200,000 times, and computes the comparison's
statistic for each split, so every split compares the harness with itself:

```
mas-optimiser calibrate --experiment baseline-haiku --a 2026-10-27-0528 --b 2026-11-03-0803 --split haiku-20-40-40
```

It writes `calibration.csv` (the mean and width of the statistic and the
share of splits each decision rule accepts, each measured against the
value expected), `pulls.csv` (the statistic of every split), `scores.csv`
(each task's score in each run) and `calibration.md`, a report of them
with three figures. It refuses two runs that differ in mode, model or
harness. Run it on a new model or benchmark before relying on a
comparison's verdicts there; the method is in
`docs/methodology/acceptance-gate.md`.

## Improving a harness

```
mas-optimiser evolve <experiment>
```

Runs the improvement loop on one split of the tasks: the baseline of the
starting harness, then rounds in which a model reads how the current harness
did on the training tasks and proposes a change to a copy of the harness
folder, the copy is scored on the validation tasks, and the acceptance gate
decides whether it replaces the current harness. At the end every harness
that was kept is run once on the test tasks. The method, who may read what,
and the files it writes are on "The improvement loop" in the documentation.

The experiment file holds the settings of `run` and the loop's own:

| setting          | what it sets                                                 | default           |
|------------------|--------------------------------------------------------------|-------------------|
| `split`          | the split whose three sets are used, by its name alone       | none, must be set |
| `harness`        | the folder the experiment starts from                        | `default`         |
| `proposer_model` | the model file of the roles that read and propose            | the agent's model |
| `rounds`         | rounds of the loop                                           | 10                |
| `candidates`     | candidates proposed in each round                            | 1                 |
| `alpha`          | the chance, over the experiment, of keeping a useless change | 0.05              |
| `editable`       | the types of file a proposal may edit                        | `.md` and `.py`   |

```
mas-optimiser evolve evolve-smoke
```

is a trial of the loop itself: three tasks of each set, one attempt, one
round, text edits only, a few minutes and a few cents. Results go to
`evolutions/<experiment>/<split>/`, and giving the command again carries an
interrupted experiment on. With `.py` among the editable types the proposer
changes the agent's code, which then runs on the machine that runs the
tasks: use a machine that holds no paid key, such as one serving its own
model.

## Watching an attempt in Langfuse

[Langfuse](https://langfuse.com) shows each attempt as a tree: the task, every
model call with the messages sent and the reply, its tool calls, token usage
and cost, and the check verdict as two scores (`task_success` and
`check_fraction`). Attempts from one evaluation share a session named after the
evaluation folder, and traces are tagged with the domain, mode, arm and model.

Every run goes to the one Langfuse project whose keys are in `.env`, and
runs are told apart inside it:

| Level in Langfuse | What it is here                     | Named or valued                                                 |
|-------------------|-------------------------------------|-----------------------------------------------------------------|
| session           | one evaluation: a run on one domain | the run and the domain, such as `2026-11-16-0808/itsm`          |
| trace             | one attempt at one task             | the task ID                                                     |
| tags              | labels to filter traces by          | the domain, the mode, `arm:default`, the model, `attempt:<n>`   |
| scores            | the verdict, on each trace          | `task_success` (true or false), `check_fraction` (from 0 to 1)  |

To find a run, open the list of sessions and pick the one named after the
run's folder; its traces are that evaluation's attempts. To look across
runs, filter the list of traces by tag (a domain, a mode, a model) or by
score (every attempt whose `task_success` is false, say).

A trace reads from the top down:

- its input is the attempt's settings (the task ID, the attempt number and
  the dataset revision);
- under it comes one entry per model call, in order, with the messages sent
  (the policy and the request first, then the conversation so far), the
  model's reply, the tool calls it asked for, and the tokens and cost of
  that call;
- its output, written when the attempt ends, is the verdict: whether the
  task passed, the benchmark's own verdict, and for every check what it
  expected, what it found and why it failed, followed by the rows the
  attempt changed.

The same verdict, in full, is the attempt's page in the evaluation folder
(`tasks/<task_id>/attempt-<n>.md`); the trace adds what the model was sent
and what it answered at each call.

One command installs and starts a self-hosted copy with Docker, outside this
repository (the stack wants about 16 GiB of memory):

```bash
scripts/langfuse.sh up
```

Used for the first time, the script clones Langfuse into `~/dev/tools/langfuse`, creates an
account for you (your git name and email, a generated password) and the
project, starts the containers, waits for them, and writes the project's
keys into this repository's `.env`. It ends by printing the sign-in details;
`scripts/langfuse.sh login` prints them again and `scripts/langfuse.sh down`
stops the stack. The UI is at http://localhost:3000. The runner traces
whenever the keys are set and the experiment's `trace` is true (the default);
`--no-trace` turns tracing off for one command.

`thinking_summary: true` in an experiment, or `--thinking-summary` on the
command line, asks the model for a summary of its reasoning on each call so
the trace shows it. This changes the agent's behaviour and cost, so use it for
demonstrations only, never for an evaluation whose numbers are reported; `evaluation.json`
records whether it was on.

## Licence

MIT; see `LICENSE`. Third-party material is listed in `NOTICE.md`.
