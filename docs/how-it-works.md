# How mas-optimiser works

This repository runs an LLM agent on the EnterpriseOps-Gym benchmark (IT
service management, customer service and HR tasks against a simulated
ServiceNow-style system), measures the result properly, and explains every
failure down to the database cell that is wrong and the turn of the
conversation that set it. That measurement layer is finished. On top of it
sits the improvement loop, an optimiser that changes the agent's harness,
the code around the model, starting from the benchmark's own (section 4).

Figure 1, at the head of section 2, is the life of one attempt as it runs
today. It is the first page of `figures/architecture.drawio`, which draw.io
opens for editing. The file's second page was drawn for an earlier plan of
the optimiser and is to be redrawn as the improvement loop (section 4).

The document follows one attempt so that every mechanism is shown on
data. The attempt is made up. It describes no task of the benchmark, so no
task has to count as already read when the tasks are split, and no task's
result is published here. Its names resemble the ITSM domain's. It is the
attempt the unit tests build their page from (`EXECUTION` in
`tests/unit/test_report.py`), so every table of the example below is what
the code gives for it. A real attempt's page has the same parts: the
demonstration in the README makes one.

A function, class or constant of this package is given by its name, and the
name links to its entry in the API reference, which links to its source.
Line numbers are given only for the benchmark's own code, which is pinned
to one commit, and for the starting harness.


## 1. The pieces, sized

| Layer | Files | Lines | What it does |
|---|---|---|---|
| The harness (the agent, to be improved) | `harnesses/default/agent.py`, `prompt.md` | 150 | the loop of model calls and tool calls, and the text it adds to a task's system message |
| The benchmark (unchanged) | `vendor/enterpriseops-gym/orchestrators/base.py`; `benchmark/executor.py`, `llm_client.py`, `mcp_client.py`, `verifier.py`, `models.py` | about 2,000 | the tool call, the model and tool clients, the database set-up on the domain server, the SQL checks |
| The runner (ours) | `src/mas_optimiser/cli.py`, `tracing.py`, `benchmarks/enterpriseops/runner.py` | about 2,000 | loads the tasks, runs attempts in parallel, applies the patch list, saves every record and the tables |
| The measure (ours) | `checks.py`, `states.py`, `sql.py`, `diagnosis.py`, `report.py`, `rescore.py`, `rebuild.py` | about 2,700 | verdicts, changes against the seed, the cell-by-cell diagnosis, the trace to the turn, the results files, recomputation |
| Set-up tools (ours) | `seeds.py`, `splits.py` | about 800 | local copies of the seed databases; the seeded draw of the training, validation and test sets |
| The improvement loop (ours, round RRSI's released loop) | `evolve/loop.py`, `evolve/models.py`, `domain.py`, `prompts.py` | about 1,200 | one experiment of the loop: baseline, rounds, test; the model call of its search roles; this benchmark as the loop sees it |
| Statistics (ours) | `stats/` | about 2,150 | the gate's test and the level of each decision; the `compare` and `calibrate` commands and their figures |
| A self-hosted model's server (ours) | `server.py` | about 330 | the `server` command: vLLM installed, started and stopped |

Line counts are from `wc -l` on 24 November 2026, and a test holds them to
within 15% of the files; a third of this package's
lines are docstrings, since every function states its arguments, its return
value and what it raises. The benchmark's own
runner (`vendor/enterpriseops-gym/evaluate.py`) is not used, because the
runner has to own the loop so that a harness's agent plugs in as an
orchestrator class (section 2.4), and because its scoring loses checks
(section 2.5).


## 2. One attempt, start to finish

:::{figure} figures/one-attempt.svg
:alt: One attempt, from the command that starts it to the files it leaves

Figure 1. One attempt, from the command that starts it to the files it
leaves. An arrow's label says what passes along it. The key at the foot
gives the colours: this package, the harness that the improvement loop
changes, the benchmark's own code, services outside the process, and files.
:::

### 2.1 The command

`mas-optimiser run baseline-haiku` enters at [`main`][cli.main], which
builds one sub-command per module in its `COMMANDS` table: `run`,
`rescore`, `rebuild`, `split`, `schema`, `compare`, `calibrate`, `list`,
`evolve` and `server`.
Each module gives its options in `add_arguments` and its work in `run`.

[`runner.run`][runner.run] reads the experiment file
`configs/experiments/baseline-haiku.yaml` through
[`load_experiment`][runner.load_experiment], which layers the defaults in
[`EXPERIMENT_DEFAULTS`][runner.EXPERIMENT_DEFAULTS], then the file, then any
command-line override, and stops on a bad setting before any money is spent.
[`load_model`][runner.load_model] reads `configs/models/haiku-5-5.yaml`: the
benchmark's model fields, the names of the variables of `.env` that hold
what belongs to one's own account (the API key, and for a service such as
Azure OpenAI its address and deployment too; never written to a config, and
left out for a self-hosted server that asks for no key), a number of tries
for a failed call if the service needs one (`max_retries`, section 2.3), and
a price table. Then one [`evaluate`][runner.evaluate] per domain.

### 2.2 The tasks

[`load_tasks`][runner.load_tasks] reads the Hugging Face dataset at a pinned
commit ([`HF_REVISION`][runner.HF_REVISION]), so a later edit of the dataset
cannot change the tasks between evaluations. The benchmark's corrected
copies under `vendor/enterpriseops-gym/data/revised/` replace the dataset's
versions. Every arm of an experiment is given every tool the domain's
server offers: that is the `all_tools` mode, the default, in which the tool
filter is switched off by an empty `selected_tools`. In `oracle` mode, the
benchmark's own setting, the agent gets only the tools listed with the task;
it is kept as the second setting, for comparison with published numbers.
The reasons are on the page "What is fixed and what is optimised". The
`plus_N_tools` modes are the dataset's own configs with N distractors
added, and take no corrected copies. A task config has four parts the rest
of this document keeps returning to:

- `system_prompt`: the domain's policy document, several pages of roles,
  mandatory fields, defaults and rules. For ITSM it says, among much else,
  that an incident's `channel` defaults to `chat` and its `impact` to `low`
  when the request gives neither, and that `priority` is derived from
  impact and urgency by a matrix.
- `user_prompt`: the request. The example's reads: "Log a critical,
  high-impact phone incident for Benjamin".
- `gym_servers_config`: which domain server to use and which seed file to
  load into it.
- `verifiers`: the checks, each an SQL query on the final database with an
  expected value. The example has four. Three of them failed:

```sql
Incident:     SELECT COUNT(*) FROM incident WHERE incident_id = 'INC_024' AND channel = 'phone'
              AND priority = 'critical' AND impact = 'high'
Notification: SELECT COUNT(*) FROM notification WHERE incident_id = 'INC_024'
Task:         SELECT COUNT(*) FROM incident_task WHERE incident_id = 'INC_024'
```

The fourth, Caller, passed. Each expects the count 1. A task passes only
when every check passes.

### 2.3 The executor

[`evaluate`][runner.evaluate] keeps the tasks of one split set if asked,
leaves out a task whose every check the patch list excludes (it cannot be
judged), keeps only the tasks named with `--task` if any are, cuts to
`--limit`, checks that the domain servers answer
([`check_servers`][runner.check_servers]), makes the evaluation folder,
sends the benchmark's logging to `evaluation.log` there, and calls
[`run_tasks`][runner.run_tasks], which runs every attempt through
[`run_task`][runner.run_task] with at most `concurrency` in flight (four in
the baseline). Each attempt seeds its own database on the server, so
attempts share nothing.

`run_task` builds a [`TaskExecutor`][runner.TaskExecutor], our subclass of the
benchmark's `BenchmarkExecutor` (`executor.py:23`). The subclass adds to it
at three points. Before the attempt, the checks are renamed and patched
([`checks.prepare`][checks.prepare], section 2.5). Once the executor has
built the model,
[`TaskExecutor.initialize`][runner.TaskExecutor.initialize] attaches the
Langfuse callback to it, asks it for a summary of its reasoning on each
call when the experiment sets `thinking_summary` (for demonstrations only,
since that changes what the agent does and costs), switches prompt caching
on, gives each call to the model a time limit, sets how many more times a
failed call is sent where the model file says, and caps the attempt's calls
to the model (the last four below). And after the checks run, while the
database still exists, the final state is read and every failed check
diagnosed (the `_run_verifiers` override, section 2.6).

Prompt caching changes what an attempt costs and nothing the model receives.
Each call of the loop sends the tool descriptions, the policy and the whole
conversation so far, so most of a call repeats the call before it. The
provider can store a request up to a mark and bill that part at about a
tenth of the input rate when it arrives again within five minutes; storing
costs a quarter more than the input rate, once. Two marks are set
([`CACHE_MARK`][runner.CACHE_MARK]). One on the request stores each call to
its end and moves forward as the conversation grows, so every call reads
what the last one sent. One on the last tool
([`mark_tool_list`][runner.mark_tool_list]) stores the tool list by itself,
so that attempts at other tasks given the same tools read it too; without it
each attempt stores its first call afresh, since a store is kept only where
a mark sits. In a check on training tasks the same calls cost 54% less with
each task's own tools and 82% less with every tool the server offers. The
marks are Anthropic's: the request field and the tool format are that
provider's own, so the setting is used for Anthropic models only
(`runner.run` decides, and says so when it does not apply), and another
provider's caching would need its own few lines here. [`cost`][runner.cost]
charges the tokens read and written at their own rates from the model file's
price table.

The time limit is how long a call to the model may wait for its answer
(`model_timeout`, 300 seconds unless the experiment says otherwise). The
benchmark builds its model with no limit (`llm_client.py:77`), and the
LangChain class passes that on in a form that also switches off the
ten-minute default of Anthropic's library, so a call on a connection that
has gone silent would wait for ever and hold the whole run. With the limit,
a try that runs out is given up and repeated: the library makes two more
tries, and the benchmark's client makes three such rounds
(`llm_client.py:363`), nine tries in all. A model file may give the library
another number of further tries (`max_retries`, a field of
[`ModelConfig`][runner.ModelConfig]), which a service that limits its rate
needs: a call refused for the rate limit is told how long to wait, and the
library waits that long before each further try. If every try fails, the
attempt ends with an error, which `run_task` records and the totals count as
a failure. The limit is far above any real call: over the stored baseline
runs a whole attempt, several calls long, took under a minute at most. The
limit is set on the provider's own client, so it is built provider by
provider (`TIMEOUT_PROVIDERS`): on the model for Anthropic, whose client is
made at the first call, and on the client itself for the OpenAI-style one
that the benchmark builds for `openai`, `azureopenai`, `vllm` and
`openrouter`, which is made with the model. With any other provider
`runner.run` says that no limit is set.

An attempt as a whole has two limits of its own, which the runner keeps and
the harness does not, so that no change to a harness lifts them:
`attempt_timeout` (3,600 seconds unless the experiment says otherwise) and
`attempt_calls` (100). [`run_task`][runner.run_task] ends an attempt that
runs past its seconds, and the executor counts the calls where the harness
reaches the model and raises at the first one past the cap. Either way the
attempt is recorded as an error and counted as a failure. The waiting that
a rate limit causes counts towards the seconds, so that limit is also the
cap on the waiting.

The benchmark's `execute_benchmark` (`executor.py:482`) then does the work:

1. `initialize` (`executor.py:59`) connects an `MCPClient` to each gym
   server (`executor.py:79-92`), discovers the tools the server offers
   (`executor.py:95`; `_discover_and_merge_tools`, `executor.py:214`),
   builds the `LLMClient` (`executor.py:110`) and the `VerifierEngine`
   (`executor.py:126`).
2. `execute_single_run` (`executor.py:309`) seeds a fresh database from the
   task's seed file (`executor.py:323`, which posts the SQL to the server's
   `/api/seed-database`, `mcp_client.py:19-58`), points the client at it
   (`executor.py:349`), builds the orchestrator (`executor.py:352`) and
   calls its `execute()` (`executor.py:360`), runs the checks
   (`executor.py:363`), and declares the attempt a success only if all of
   them passed (`executor.py:371`).
3. The `finally` block deletes the attempt's database
   (`executor.py:553-563`). This is why the final state has to be read
   inside our `_run_verifiers` override: afterwards it is gone.

### 2.4 The agent loop: what the harness is

A harness is a folder, and the runner loads the agent from the one the
experiment names (`harness`, read in [`runner.run`][runner.run]): the class
`Agent` in its `agent.py`. The starting harness is `harnesses/default/`. Its
`agent.py` is a copy of the benchmark's own agent, `orchestrators/react.py`,
changed in one respect: the text of `prompt.md`, the file beside it, is
added to the end of the system message when that file is not empty. It
starts empty, so the default harness sends and records what the benchmark's
agent does.

The whole agent is `Agent.execute` (`agent.py:41-150`), a plain
loop:

1. The conversation starts as two messages: the policy as the system
   message and the request as the user message (`agent.py:47-50`).
2. For up to `max_iterations` turns (`agent.py:59`; 50 by default,
   `base.py:23`): call the model with the messages and the tool list
   (`agent.py:63`). `invoke_with_tools` (`llm_client.py:353`) converts the
   MCP tool schemas into function definitions (`llm_client.py:220`,
   cleaning schema shapes the model APIs reject, `llm_client.py:250`),
   binds them to the model (`llm_client.py:362`) and retries a failed call
   up to three times (`llm_client.py:363`).
3. The reply is appended to the messages (`agent.py:72`) and recorded in
   `conversation_flow` with its text, token usage and tool calls
   (`agent.py:83-94`).
4. If the reply has no tool calls, the loop ends (`agent.py:99-101`): the
   agent has decided it is done, or has answered in prose.
5. Otherwise each tool call is executed (`agent.py:110`, through
   `base.py:46`, which looks up the tool's gym at `base.py:49` and calls
   `client.call_tool` at `base.py:59`). `call_tool` (`mcp_client.py:306`)
   sends one JSON-RPC `tools/call` request to the server's `/mcp` endpoint
   with the attempt's database id in a header (`mcp_client.py:185`), so
   every tool acts on this attempt's database. The result comes back as a
   `ToolMessage` the model sees on its next turn (`agent.py:128-133`), and
   is recorded in `conversation_flow` (`agent.py:135-142`).

The loop returns the final text, the full `conversation_flow`, the tools
used and the tool results (`agent.py:144-150`); the executor keeps all of
it in the attempt's record (`executor.py:380-398`).

The example took four turns:

| Turn | Tool call                                                            | What came back                 |
|------|----------------------------------------------------------------------|--------------------------------|
| 1    | `list_users` with first name Benjamin                                | the user, id 7                 |
| 2    | `create_incident` with caller 7, channel `chat`, priority `moderate` | incident INC_024               |
| 3    | `send_notification` for INC_024                                      | an error: the email is unknown |
| 4    | no tool call: "Logged INC_024."                                      | the loop ends                  |

So the harness, the thing the project improves, is that folder: the loop
(`agent.py`) and the text it adds (`prompt.md`). What the loop works with
stays as the benchmark gives it: the model client (`llm_client.py`), the
tool client (`mcp_client.py`), the policy (from the task) and the tool list
(from the server). A different agent gets in without touching the
benchmark's code because `BenchmarkExecutor` accepts any `AgentOrchestrator`
subclass (`executor.py:39-46`, `base.py:14`), and `run_task` passes it the
class loaded from the harness folder. A changed copy of the folder,
anywhere on disk, is run by giving its path (`--harness <folder>`), and
`evaluation.json` records which harness an evaluation used.

### 2.5 The checks: the measure

On the benchmark's side, `_run_verifiers` (`executor.py:412`) runs each
check through `VerifierEngine.execute_verifier` (`verifier.py:48`). A
`database_state` check (`verifier.py:81`) posts its query to the server's
`/api/sql-runner` (`verifier.py:195-255`), reduces the answer to a value
(`_extract_value_from_sql_result`, `verifier.py:291`: a single cell comes
back as that cell), and compares it with the expected value
(`_compare_values`, `verifier.py:374`: equals, greater than, less than,
contains). The verdicts are kept in a dict keyed by the check's name
(`executor.py:474`).

Three details of the data make the benchmark's own count wrong, and
`checks.py` corrects them while keeping the benchmark's verdict beside ours:

- In 59 of the 308 tasks two or more checks share a name, so the dict keeps
  only the last. [`unique_names`][checks.unique_names] drops exact copies and
  renames the clashes before the attempt, so each distinct check is kept once.
- 33 count checks give their expected value as the string `'1'` where the
  SQL runner returns the integer `1`, which `==` never matches.
  [`recheck`][checks.recheck] compares such a pair numerically after the
  attempt.
- Some checks are wrong in themselves: no correct attempt can satisfy them,
  or they name a record from another seed. `data/check_patches.json` lists
  22 as excluded and 8 as repaired, each with its evidence.
  [`apply_patches`][checks.apply_patches] takes a task's list through it
  before the attempt, and [`original_verdicts`][checks.original_verdicts]
  runs each patched check's original query afterwards, so `checks.csv`
  carries the benchmark's own verdict (`benchmark_passed`) next to ours and
  a `patch` column saying which.

[`benchmark_verdict`][checks.benchmark_verdict] recovers what the benchmark
would have reported for the attempt, and
[`check_records`][checks.check_records] flattens the verdicts into the rows
of `checks.csv`. In the example three of the four checks failed and one
passed.

### 2.6 What changed, and why each check failed

A failed count check says only that a conjunction did not hold. To say which
condition failed, and what the agent left instead, our `_run_verifiers`
override (a method of [`TaskExecutor`][runner.TaskExecutor]) does two more
things while the database exists.

First it reads back what the attempt changed.
[`read_changes`][runner.TaskExecutor.read_changes] calls
[`states.read_state`][states.read_state], which reads every table of the
attempt's database through the SQL runner a page at a time, and
[`states.diff`][states.diff], which compares it row by row with a local copy
of the seed: the rows inserted, the rows deleted, and for each updated row
the cells that changed with their values before and after. The local seed
copy comes from [`seed_copy`][states.seed_copy], built once per seed file by
[`seeds.build_seed_database`][seeds.build_seed_database] from the schema
committed under `data/schemas/` (fetched from the servers once by
`mas-optimiser schema`, which also writes the readable data-model pages
under `docs/data-model/`) and the seed file's inserts. The example changed
two tables: one row inserted in `incident`, and one in `incident_knowledge`,
a table no check asks about.

Then each failed check goes through
[`diagnosis.diagnose`][diagnosis.diagnose]. The query is read by
[`sql.parse`][sql.parse], the one place in the package that reads SQL: the
structure (count or single value, the main table, which table a condition
reads) comes from sqlglot's syntax tree of the query, and the text of each
condition is cut out of the query as it was written
([`sql.clauses`][sql.clauses]), so the outputs keep the benchmark's wording
and no rewritten SQL is run. A query that reads one value (`SELECT col FROM
t WHERE key`) is already wanted against actual, the kind `value`. A count
query is taken apart into its main table and its top-level AND conditions.
If the count was too high, a sample of the surplus rows is fetched
(`too_many_rows`). If the attempt touched no row of the table, the kind is
`no_rows`. Otherwise every condition is tested on its own against each
touched row, on the main table alone unless the condition reads a joined
table, and the column's actual value is fetched beside each (`cells`). A
query of any other shape, or a table with no key to find its rows by, gets
the kind `unsupported` and no explanation. Every query runs on the
attempt's own database; no model is involved, and a condition an untouched
row happens to satisfy is never credited.

The example's diagnosis (written into the test data by hand, since the unit
tests of the page run without a database; `tests/unit/test_diagnosis.py`
tests the diagnosis itself on small databases):

| Check        | Kind      | What the diagnosis found                                                                                                                      |
|--------------|-----------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| Incident     | `cells`   | row INC_024: the id right; `channel` is `chat`, wanted `phone`; `priority` is `moderate`, wanted `critical`; `impact` is `low`, wanted `high` |
| Notification | `no_rows` | the attempt inserted or updated nothing in `notification`                                                                                     |
| Task         | `no_rows` | the attempt inserted or updated nothing in `incident_task`                                                                                    |

### 2.7 From a wrong cell to the turn that set it

`report.py` makes the link back to the conversation, mechanically.
[`calls`][report.calls] pairs each tool call with its result, numbered by
turn, and marks whether it succeeded ([`succeeded`][report.succeeded]: the
request got through and the tool did not answer with an error). A lookup
that answers that no such record exists is reported by the servers as an
error; `calls` marks it `nothing_found`, since the lookup did its work, and
it is counted apart from the tool errors.
[`writers_of`][report.writers_of] finds the calls that write a table: a
writing verb in the tool's name and the table's words in the rest of it
(`create_incident` writes `incident`; `send_notification` does not), with a
successful call whose result echoes the table's key column as the fallback.
[`trace`][report.trace] then links those calls to the touched row through
its key and says, for each cell a condition tests, where its value came
from:

- `passed`: the last linked call whose arguments name the column;
- `left out`: no call passed it, and the tool that wrote the row takes an
  argument of that name (the tools' arguments are read from the
  evaluation's `tools.json` by [`tool_arguments`][runner.tool_arguments]);
- `no argument`: the tool has no argument of that name, so the server set
  the value;
- `not passed`: no call passed it and the evaluation saved no tool list.

It also gives the failed check its failure:

- `wrong values`: the row is there with wrong cells;
- `too many rows`: more rows match than the check allows;
- `table not written`: no call wrote the table;
- `call failed`: a call that writes the table returned an error;
- `no row written`: the calls succeeded and no row of the table changed.

On the example:

| Check        | Failure           | Trace                                                                                                                                           |
|--------------|-------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| Incident     | wrong values      | `channel` and `priority` passed at turn 2 by `create_incident`; `impact` left out of that call, which takes it; `incident_id` set by the server |
| Notification | call failed       | `send_notification` at turn 3 returned an error                                                                                                 |
| Task         | table not written | no call wrote `incident_task`, though the task was given `create_incident_task`                                                                 |

[`table_rows`][report.table_rows] then puts the checks together by table. A
check belongs to the one table whose rows it counts; the other tables its
query reads are lookups. A table is `correct` when every check on it
passed, `wrong` when one failed and the attempt changed the table, and
`missed` when one failed and the attempt changed nothing in it. A table
the attempt changed with no check of its own is `not asked for`, or `read
by a check` when a check on another table reads it. On the example,
`incident` is wrong, `notification` and `incident_task` are missed, and
`incident_knowledge` is not asked for.

The trace stops at where each value came from. In the example the request
asks for a critical, high-impact phone incident; the agent passed `chat`
and `moderate` and left the impact out, and the page shows each at the turn
it happened. Why an agent read a request that way is not something the
package says: a person reads it from the request and the policy.

[`outputs`][runner.outputs] attaches the trace to each failed check and
builds the attempt's rows: [`task_outcome`][runner.task_outcome] for
`tasks.csv` (verdicts under both conventions, the tables with a check on
them by result, tool errors and lookups that found nothing, model calls,
tokens, cost in US dollars from the price table, seconds), `check_records`
for `checks.csv`, [`cell_rows`][report.cell_rows] for `cells.csv` (each cell
wanted against found, and where its value came from) and `table_rows` for
`tables.csv`. [`save_attempt`][runner.save_attempt] writes the record
(`attempt-N.json`), the changes (`attempt-N.changes.json`) and the page
(`attempt-N.md`, from [`attempt_text`][report.attempt_text]). The page goes
from the whole to the detail. After the request comes one row per table the
checks look at, with the checks on it, the rows the attempt changed and the
result. Then the tools the task was given (the task's `selected_tools`), the
turns each was called at and the arguments each takes; when the agent was
given every tool the server offers, only those it called are listed. Then
every check with its query and, under a failure, a table: for a row that is
there, each column with what was wanted, what was found, `ok` or `WRONG`,
and where the value came from (the value before as well, for a row that was
already in the seed); for a row that is not there, each condition with the
tool given that takes its column. Then a heading per turn, with the model's
text as a quote and each tool call followed by its arguments, never cut, and
what came back. Last, every row the attempt inserted, with all its columns,
updated or deleted. When every attempt is done,
[`write_tables`][runner.write_tables] writes the four CSVs, `failures.csv`
(each kind of failure by table and column, with the attempts and tasks it
occurs in, most frequent first) and `summary.md`
([`summary`][report.summary]: the headline as a row per measure with what it
counts, the tables that were wrong or missed, the most frequent failures
with the words they use, the tasks that passed least and what each file
holds; for a held-out set of a split, `splits.HELD_OUT`, the headline and
the files only), and `evaluate` writes `evaluation.json` (every setting and
total) and a row in the experiment's `index.csv` (`index_row` and
[`write_index_row`][runner.write_index_row]).

With Langfuse configured, [`tracing.task_trace`][tracing.task_trace] wraps each
attempt in one trace, [`attach`][tracing.attach] routes the model's calls
into it, and [`record_outcome`][tracing.record_outcome] scores the trace
with the verdict and puts the checks and changes in its output.

### 2.8 Recomputing without the agent

`rescore.py` recomputes an evaluation from its saved records:
[`rebuild`][states.rebuild] applies an attempt's saved changes to a fresh
seed copy, [`check_locally`][rescore.check_locally] runs every check on that
database as the executor would, and the same diagnosis, trace and files
follow ([`rescore_attempt`][rescore.rescore_attempt]). A condition on the
clock (`created_at >= datetime('now', '-1 day')`; nine checks have one, all
in HR) is judged at the time the attempt's checks ran
([`local_runner`][rescore.local_runner]), so a rescore gives the same answer
on any day. `mas-optimiser rebuild` (`rebuild.py`) writes the same rebuilt
database as a SQLite file beside the record, for a reader who wants to run a
check's query by hand. A rescore also recomputes the totals
(`runner.totals`) and rewrites the evaluation's row in the experiment's
`index.csv` (`runner.write_index_row`) and, for one domain of a run, the
run's `run.json` and `summary.md` (`runner.write_run_summary`), so the index
and the summaries never disagree; an attempt that raised keeps its row as a
failure, and a task with no check left to judge by gets its pages rewritten
and stays out of the tables. It exists so that a change to the diagnosis,
the trace or the patch list can be applied to every past evaluation in
seconds, with no model cost, and so that the saved changes are enough to
reproduce a verdict.


## 3. Reading an evaluation

Start at `summary.md` in the evaluation folder: the headline table, a row
per measure, then "What went wrong" (the tables that were wrong or missed,
then the most frequent failures), then the tasks that passed least.
`tables.csv` says, for each attempt and table, whether the attempt got the
table right, and `failures.csv` ranks what went wrong across the evaluation.
Open a task's `attempt-N.md` for the request, the turns, the changes and
every check with its condition table and trace. `cells.csv` is the same
information for every failed check at once, one row per touched row and
condition, wanted against found, with the turn and tool the value came from;
`checks.csv` has every check's verdict under both conventions. [Reading the
results](results.md) gives every column of every file and walks through one
attempt's page. `tools.json` holds every tool the domain server offered,
with its description and argument schema, so a reader can see what the agent
could have called beyond the task's `selected_tools`.


## 4. Where this is going

The part of the package built on all of this is the loop that improves the
harness, `mas-optimiser evolve` ([The improvement
loop](methodology/improvement-loop.md)). It starts from
`harnesses/default/`, the loop of section 2.4 as it is. A proposer (a model,
run offline) reads the pages of the training attempts and proposes one
change to a copy of the harness folder. The candidate runs on the validation
tasks through the runner of section 2, and the acceptance gate compares it
with the current harness task by task ([The acceptance
gate](methodology/acceptance-gate.md)). An accepted candidate becomes the
current harness; a rejected one is recorded and dropped. Everything outside
the folder, the runner, the checks, the scoring and the task sets, is out of
the loop's reach ([What is fixed and what is
optimised](methodology/fixed-and-optimised.md)). Two settings say how much
is left to the proposer: `editable`, whether it may change the text the
agent is given or its code as well, and `approval`, whether a person reads
each change before it first runs. It has run from end to end
in a trial of three tasks a set; no full experiment has been run with it
yet.

The drawing's second page (`figures/ladder-and-optimiser.svg`) shows an
earlier plan, in which harnesses built by hand stood between the starting
one and the optimiser. It is not shown here, and is to be redrawn as the
loop.


## 5. Running, testing and debugging

Running is in the README: the servers (`docker compose -f
docker/compose.yaml up -d`), then `mas-optimiser run <experiment>`.
To see one attempt without spending more than a few cents:

```
mas-optimiser run smoke --split haiku-20-40-40:training --limit 1 --attempts 1 --label poke
```

then read the page under `evaluations/smoke/`. `mas-optimiser rescore <folder>`
on that folder rewrites every file from the saved changes; nothing should
change.

The unit tests (`uv run pytest tests/unit`, no servers needed) cover the
measure on hand-written records. The integration
tests (`tests/integration`) need the servers and check, among other things,
that the local seed copy matches the server's database row for row.

What goes wrong, and where to look:

| Symptom | Meaning | Where |
|---|---|---|
| `error: domain server ... is not answering` | the Docker servers are down | [`check_servers`][runner.check_servers]; start them and rerun |
| `ANTHROPIC_API_KEY is not set; add it to .env`, or the same for another model's variable | no `.env`, or the model's line missing from it | [`load_model`][runner.load_model]; `.env.example` lists every line |
| a `tasks.csv` row with `error` filled and zero checks | the attempt raised before the agent started (a server fault, a bad config) | the record's `error`; the traceback in `evaluation.log` |
| `diagnosis_error` on the run in `attempt-N.json` | the checks ran but the diagnosis raised; verdicts are intact | `_run_verifiers` in [`TaskExecutor`][runner.TaskExecutor] |
| `report_error` on the run | the trace or the rows raised; the page has no trace | [`run_task`][runner.run_task] |
| a failed check with `kind` `unsupported` in `checks.csv` | a query shape the diagnosis cannot take apart (a top-level OR, a GROUP BY, a derived table), or a table with no key to find its rows by | [`sql.parse`][sql.parse], [`diagnosis.diagnose`][diagnosis.diagnose] |
| an attempt with 50 model calls | the agent hit `max_iterations` | `agent.py:59`, `base.py:23` |
| an `error` of `the attempt ran past its limit of ... seconds` or `the attempt made more than ... calls to the model` | the attempt reached one of its two limits | `attempt_timeout` and `attempt_calls`, section 2.3 |
| an `error` that holds `429` or `rate limit` | the service refused the call more times than the model file's `max_retries` allows | `evaluation.log` has a line for each refusal and each wait; section 2.3 |
| `tool_errors` above zero | a tool answered with an error; the page marks the call `ERROR`. A lookup that found no record is marked `nothing found` and counted in `lookups_empty` | [`succeeded`][report.succeeded] |


## 6. Sources

Every component below was built from the sources in its rows; where
several fed one component, each has its own row saying what it gave. A
module's docstring points here. Code reproduced from another repository
is credited in `NOTICE.md` as well.

| Component | Source | What it contributed | Where |
|---|---|---|---|
| all modules | EnterpriseOps-Gym (ServiceNow; repository at 271f2c3, Apache-2.0; paper arXiv:2603.13594; dataset `ServiceNow-AI/EnterpriseOps-Gym` at c8e538e) | the servers, executor, orchestrator, config classes, MCP client, checks and seed files, used unmodified from the submodule; the tasks and their corrected copies | `benchmarks/enterpriseops/__init__.py` puts the submodule on `sys.path` |
| all modules | the convention shared by StarHarness, GEPA, OpenEvolve and ShinkaEvolve | the benchmark adapter kept apart from the optimiser, and the benchmark with its checks never editable | the package layout |
| all modules | Google docstrings checked by ruff and pydoclint; setuptools-scm | every function documented and checked in CI; the code version stamped into every evaluation and split | `pyproject.toml`, `ci.yml`, `mas_optimiser.code_version` |
| `cli` | the author's research-computing practice | one console command declared in `pyproject.toml`; each module exposes `add_arguments` and `run` so an option is defined once | `cli.COMMANDS` |
| `server` | vLLM's installation and Docker deployment pages (docs.vllm.ai/en/latest/getting_started/installation/gpu.html, docs.vllm.ai/en/latest/deployment/docker.html); the model cards of Qwen3.6-27B and its 8-bit version (huggingface.co/Qwen/Qwen3.6-27B, huggingface.co/Qwen/Qwen3.6-27B-FP8) | the two commands that install a release with uv; the options of `docker run` (the GPUs, the host's shared memory, the cache kept outside the container); the server options for tool calling and for text only | `server.run`; the `server` blocks of `configs/models/qwen3.6-27b-fp8.yaml` and `qwen3.6-27b.yaml` |
| `runner` | EnterpriseOps-Gym `benchmark/executor.py` | `BenchmarkExecutor` subclassed, with `initialize` and `_run_verifiers` overridden; `AgentOrchestrator`, `BenchmarkConfig` and `LLMConfig` as released | `runner.TaskExecutor` |
| `harnesses/default` | EnterpriseOps-Gym `orchestrators/react.py` (Apache-2.0) | the agent loop, copied, with the changes the copy's opening lines state | `harnesses/default/agent.py` |
| `runner` | Anthropic's documentation, "Prompt caching" (platform.claude.com/docs/en/build-with-claude/prompt-caching) | the cache mark on a request and on a tool, the read and write rates, the five-minute lifetime; used for Anthropic models only | `runner.CACHE_MARK`, `mark_tool_list`, `cost` |
| `runner` | the Anthropic Python library (github.com/anthropics/anthropic-sdk-python), its client's `timeout` and `max_retries` and their defaults in `_constants.py`; langchain-anthropic, `ChatAnthropic.default_request_timeout` | the limit on one try at a model call and the two retries that follow a try that runs out; the ten-minute default that a limit passed as "none" switches off | `runner.TaskExecutor.initialize` |
| `runner` | the OpenAI Python library (github.com/openai/openai-python), its client's `timeout` and `max_retries`; langchain-openai, `ChatOpenAI` and `AzureChatOpenAI` | the same limit for a model reached through an OpenAI-style client, a self-hosted vLLM server and Azure OpenAI among them | `runner.TaskExecutor.initialize`, `TIMEOUT_PROVIDERS` |
| `runner` | the OpenAI Python library, the rule by which its client sends a call again (`_base_client.py`: after a refusal for the rate limit, a time-out or a server error, waiting as long as the service asks); Microsoft's page on Azure OpenAI quota (learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/quota, read 24 November 2026) | `max_retries` in a model file, for a service that limits its rate: a refused call is sent again after the wait the service gives, and never at once | `runner.ModelConfig`, `runner.TaskExecutor.initialize`, `evolve.models.generate` |
| `runner` | own design, on `asyncio.wait_for` of Python's standard library | the two limits on an attempt, kept by the runner so that no harness lifts them: its seconds and its calls to the model | `runner.run_task`, `runner.TaskExecutor.initialize` |
| `runner` | the Hugging Face dataset and the repository's `data/revised` | the task configs at a pinned revision, the corrected copies replacing the dataset's in oracle and all-tools mode | `runner.load_tasks` |
| `runner` | the EnterpriseOps-Gym paper, its experimental set-up and distractor study | the setting it reports, each task's own tools (`oracle`), and its finding that added tools cost nothing in completion; why every arm is given all the tools here is argued on the page "What is fixed and what is optimised" | `runner.MODES` |
| `runner` | StarHarness (arXiv:2608.24804), `docs/ADDING_A_BENCHMARK.md` | the precedent that a unit which raises is scored as a failure, never retried or dropped | `runner.task_outcome` |
| `runner` | τ-bench (arXiv:2406.12045), pass^k | `tasks_passed_every_attempt` as the strict per-task measure | `runner.totals` |
| `runner` | own design | experiments as YAML files with command-line overrides; `evaluations/<experiment>/<run>/<domain>/` with an `index.csv` per experiment; attempts in parallel under a semaphore; the price table in the model file; `tools.json` per evaluation | `runner.load_experiment`, `evaluate`, `run_tasks`, `cost`, `save_tools` |
| `tracing` | Langfuse (self-hosted, MIT core) and its Python SDK | `CallbackHandler`, `score_trace` and `propagate_attributes`: one trace per attempt, scored with the verdict | `tracing.attach`, `task_trace`, `record_outcome` |
| `tracing` | LangChain callbacks, as the benchmark builds its model on LangChain | the handler attached to the model object after `initialize()`, the point where the executor itself reassigns it | `runner.TaskExecutor.initialize` |
| `checks` | EnterpriseOps-Gym `benchmark/verifier.py` and `executor.py` | `_compare_values` reproduced branch for branch so that a local verdict matches the executor's (see `NOTICE.md`); the verdict-by-name behaviour that `prepare` corrects | `checks.compare`, `checks.prepare` |
| `checks` | own design | every distinct check counted and clashing names renamed; the patch list `data/check_patches.json`, with the benchmark's own verdict kept beside the corrected one | `checks.prepare`, `apply_patches`, `passed`, `benchmark_verdict` |
| `diagnosis` | own design | each condition of a failed check's query tested on the rows the attempt touched, with the kinds `cells`, `value`, `no_rows`, `too_many_rows` and `unsupported` | `diagnosis.diagnose`, `describe` |
| `sql` | sqlglot (MIT licence) | a check query's syntax tree, read as SQLite, and its tokens with their positions: the query's shape, its tables, the column a condition tests, and where each condition starts and ends in the query's own text | `sql.parse`, `clauses`, `column_of`, `tables` |
| `sql` | EnterpriseOps-Gym check queries; SQLite's rule for a double-quoted name | the two query shapes taken apart, from a survey of every check query in the three domains; a double-quoted name read as a column only where the schema has one | `sql.parse` |
| `states` | own design | the final database read back page by page and compared with the seed by primary key, so the record holds the changes and a scoring change re-scores instead of re-running; the rebuild from seed plus changes | `states.read_state`, `diff`, `rebuild` |
| `states` | the EnterpriseOps-Gym servers | the facts worked around: the attempt's database deleted after the checks, pages of at most 100 rows, columns a server stamps at load | `states.STAMPED`, `read_state` |
| `report` | own design | the attempt page, the CSV tables, the summary, the result of each table, and the trace from a cell to the turn and tool call its value came from | `report.attempt_text`, `trace`, `cell_rows`, `table_rows`, `summary` |
| `rescore` | own design | an evaluation recomputed from its saved records and changes, the checks run locally as the executor would | `rescore.rescore`, `local_runner` |
| `rebuild` | own design | one attempt's final database written as a SQLite file | `rebuild.rebuild_attempt` |
| `seeds` | EnterpriseOps-Gym `benchmark/mcp_client.py` | `delete_database` imported; `create_database_from_file` reproduced as `server_database` with the same endpoint and payload (see `NOTICE.md`) | `seeds.server_database` |
| `seeds` | own design | the table schema read from `sqlite_master` on a seeded server database; local seed copies; the data-model pages | `seeds.build_seed_database`, `schema_markdown` |
| `splits` | StarHarness (arXiv:2608.24804) | the protocol of a seeded draw into a proposer-visible set, a gate set and a held-out set, stratified there by baseline outcome; its `stratify_tasks.py` was not copied | `splits.draw` |
| `splits` | stratified sampling (Cochran, *Sampling Techniques*, Wiley, 1977) | what a draw balances (domain, difficulty or both) as its strata, quotas by largest remainder | `splits.quotas`, `draw` |
| `splits` | REUSE (arXiv:2609.33180); Kapoor et al. (arXiv:2407.01502); SEAL (arXiv:2607.24300) | the gate set returns only accept or reject to the proposer; a sealed test set opened once; a split drawn once and never redrawn | `splits.build_split` |
| `evolve` | RRSI (Xia et al., arXiv:2609.24972; github.com/google-research/rrsi at be50316, Apache-2.0), unchanged in `vendor/rrsi` | the analysis of traces, the proposer and its file workspace, the review of a proposal, the record of proposals, a git checkout per candidate, the `Domain` interface, and the order of a round | `evolve.loop.Experiment` (a subclass of its `Run`), `domain.EnterpriseOps` |
| `evolve` | StarHarness (arXiv:2608.24804), section 3 and Algorithm 1 | search tasks the proposer reads and selection tasks hidden from it; a candidate judged on the hidden set and kept or reverted; one evaluation on the held-out set at the end | `evolve.loop.Experiment.round`, `test` |
| `evolve` | Sun et al. (arXiv:2609.33180), Eq. 4 | the level of each decision of one experiment | `evolve.loop.Experiment.round`, through `stats.budget.reuse_level` |
| `evolve` | own design | the proposer told only whether a proposal was accepted; a harness's test score taken for every harness kept, after the last decision; the model call from a model file | `evolve.loop`, `evolve.models` |
| `evolve` | own design, on `threading` of Python's standard library | the limit of seconds on one call of a search role: the call runs in a thread that can be left behind when the limit passes | `evolve.models.generate` |
| `domain` | RRSI's `Domain` interface (`rrsi/domain.py`), its `critic_patterns` and `smoke` | the screen of a proposed change for task ids, references to the checks and code that reaches past the harness's own work; a person's yes or no asked before a change first runs | `domain.EnterpriseOps.critic_patterns`, `domain.EnterpriseOps.smoke` |
| `stats` | own design | per-task scores for two outcomes, the paired comparison over the same tasks, the `compare` command with its results as data files and a report of them, and the figures | `stats.outcomes`, `stats.compare`, `stats.figures` |
| `stats` | own design, on the exchangeability argument of permutation tests (Efron and Tibshirani, below) | the calibration of the test on two runs of one harness: its statistic over random splits of their attempts, against Student's t | `stats.calibrate` |
| `stats` | Barlow, *Statistics* (Wiley, 1989), estimation and hypothesis testing | the standard error of a mean, the one-sided paired t-test, the Type-I and Type-II errors, power and the minimum detectable effect | `stats.gate.mean_test`, `detectable`, `power` |
| `stats` | Efron and Tibshirani, *An Introduction to the Bootstrap* (Chapman and Hall, 1993) | the bootstrap spread of the mean difference and the permutation test | `stats.compare`, through SciPy |
| `stats` | SciPy (`scipy.stats`, `scipy.special`) | the t-test, the noncentral t behind power and the minimum detectable effect, the permutation test, the random splits of the calibration, the bootstrap and the relative entropy are its functions, called and not rewritten | `stats.gate`, `stats.compare`, `stats.calibrate` |
| `stats` | REUSE (arXiv:2609.33180) | the error budget over a run's decisions (Eq. 4), the exact sign test (Lemma A2) and the bounded-score lower confidence bound (appendix A.1) | `stats.budget.reuse_level`, `stats.gate.sign_test`, `bernstein_bound` |
| `stats` | Bonferroni's inequality; the significance conventions of particle physics | the equal split of the budget and the fixed threshold in standard deviations, shown beside the allocation | `stats.budget.bonferroni_level`, `fixed_level` |

## Appendix: file map

| File | Role |
|---|---|
| `src/mas_optimiser/cli.py` | the `mas-optimiser` command and its sub-commands |
| `src/mas_optimiser/tracing.py` | Langfuse: one trace per attempt, scored |
| `src/mas_optimiser/markdown.py` | the markdown table every report is written with, its columns aligned in the source |
| `src/mas_optimiser/server.py` | the `server` command: a self-hosted model's server installed, started and stopped, as a process of the machine or in Docker |
| `src/mas_optimiser/benchmarks/enterpriseops/listing.py` | the `list` command: an experiment's runs and their domains from its index |
| `src/mas_optimiser/stats/outcomes.py` | per-task scores and the paired differences of two evaluations |
| `src/mas_optimiser/evolve/loop.py` | one experiment of the improvement loop: baseline, rounds, test; the `evolve` command |
| `src/mas_optimiser/evolve/models.py` | the model call of the loop's search roles, from a model file |
| `.../enterpriseops/domain.py` | this benchmark as the loop sees it: its sets, how a harness is run and scored, the trace, the trial run |
| `.../enterpriseops/prompts.py` | what the search roles are told about the benchmark and the harness folder |
| `vendor/rrsi/rrsi/` | RRSI's released loop, unchanged |
| `src/mas_optimiser/stats/gate.py` | the gate's test, the detectable gain, and the sign, permutation, bootstrap and Bernstein checks |
| `src/mas_optimiser/stats/budget.py` | the level of each decision: the REUSE allocation, the equal split, a fixed significance |
| `src/mas_optimiser/stats/figures.py` | the comparison's four figures and the calibration's three, drawn from their data files |
| `src/mas_optimiser/stats/calibrate.py` | the `calibrate` command |
| `src/mas_optimiser/stats/compare.py` | the `compare` command |
| `.../enterpriseops/runner.py` | experiments, tasks, the executor subclass, attempts in parallel, the results files |
| `.../enterpriseops/checks.py` | distinct checks, the patch list, the numeric recheck, the benchmark's verdict beside ours |
| `.../enterpriseops/states.py` | the final state read back, the diff against the seed, the local rebuild |
| `.../enterpriseops/sql.py` | a check query read with sqlglot: its shape, tables, conditions and columns |
| `.../enterpriseops/diagnosis.py` | each condition of a failed check tested on the touched rows |
| `.../enterpriseops/report.py` | the trace to the turn, the attempt page, the CSV rows, the summary |
| `.../enterpriseops/rescore.py` | recompute an evaluation from its saved changes |
| `.../enterpriseops/rebuild.py` | write one attempt's rebuilt database as a file |
| `.../enterpriseops/seeds.py` | local copies of the seed databases; the `schema` command |
| `.../enterpriseops/splits.py` | the seeded split of the tasks into training, validation and test; drawn once |
| `harnesses/default/agent.py`, `prompt.md` | the starting harness: the agent loop, copied from the benchmark's `orchestrators/react.py`, and the text it adds |
| `vendor/.../orchestrators/base.py` | the orchestrator interface and the tool call |
| `vendor/.../benchmark/executor.py` | seed, run, check, clean up |
| `vendor/.../benchmark/llm_client.py` | the model behind LangChain, tool schema conversion, retries |
| `vendor/.../benchmark/mcp_client.py` | JSON-RPC to the domain server; database create and delete |
| `vendor/.../benchmark/verifier.py` | the SQL checks |
| `vendor/.../benchmark/models.py` | the config dataclasses |

[checks.apply_patches]: #mas_optimiser.benchmarks.enterpriseops.checks.apply_patches
[checks.benchmark_verdict]: #mas_optimiser.benchmarks.enterpriseops.checks.benchmark_verdict
[checks.check_records]: #mas_optimiser.benchmarks.enterpriseops.checks.check_records
[checks.original_verdicts]: #mas_optimiser.benchmarks.enterpriseops.checks.original_verdicts
[checks.prepare]: #mas_optimiser.benchmarks.enterpriseops.checks.prepare
[checks.recheck]: #mas_optimiser.benchmarks.enterpriseops.checks.recheck
[checks.unique_names]: #mas_optimiser.benchmarks.enterpriseops.checks.unique_names
[cli.main]: #mas_optimiser.cli.main
[diagnosis.diagnose]: #mas_optimiser.benchmarks.enterpriseops.diagnosis.diagnose
[report.attempt_text]: #mas_optimiser.benchmarks.enterpriseops.report.attempt_text
[report.calls]: #mas_optimiser.benchmarks.enterpriseops.report.calls
[report.cell_rows]: #mas_optimiser.benchmarks.enterpriseops.report.cell_rows
[report.succeeded]: #mas_optimiser.benchmarks.enterpriseops.report.succeeded
[report.summary]: #mas_optimiser.benchmarks.enterpriseops.report.summary
[report.table_rows]: #mas_optimiser.benchmarks.enterpriseops.report.table_rows
[report.trace]: #mas_optimiser.benchmarks.enterpriseops.report.trace
[report.writers_of]: #mas_optimiser.benchmarks.enterpriseops.report.writers_of
[rescore.check_locally]: #mas_optimiser.benchmarks.enterpriseops.rescore.check_locally
[rescore.local_runner]: #mas_optimiser.benchmarks.enterpriseops.rescore.local_runner
[rescore.rescore_attempt]: #mas_optimiser.benchmarks.enterpriseops.rescore.rescore_attempt
[runner.CACHE_MARK]: #mas_optimiser.benchmarks.enterpriseops.runner.CACHE_MARK
[runner.EXPERIMENT_DEFAULTS]: #mas_optimiser.benchmarks.enterpriseops.runner.EXPERIMENT_DEFAULTS
[runner.HF_REVISION]: #mas_optimiser.benchmarks.enterpriseops.runner.HF_REVISION
[runner.ModelConfig]: #mas_optimiser.benchmarks.enterpriseops.runner.ModelConfig
[runner.TaskExecutor]: #mas_optimiser.benchmarks.enterpriseops.runner.TaskExecutor
[runner.TaskExecutor.initialize]: #mas_optimiser.benchmarks.enterpriseops.runner.TaskExecutor.initialize
[runner.TaskExecutor.read_changes]: #mas_optimiser.benchmarks.enterpriseops.runner.TaskExecutor.read_changes
[runner.check_servers]: #mas_optimiser.benchmarks.enterpriseops.runner.check_servers
[runner.cost]: #mas_optimiser.benchmarks.enterpriseops.runner.cost
[runner.evaluate]: #mas_optimiser.benchmarks.enterpriseops.runner.evaluate
[runner.load_experiment]: #mas_optimiser.benchmarks.enterpriseops.runner.load_experiment
[runner.load_model]: #mas_optimiser.benchmarks.enterpriseops.runner.load_model
[runner.load_tasks]: #mas_optimiser.benchmarks.enterpriseops.runner.load_tasks
[runner.mark_tool_list]: #mas_optimiser.benchmarks.enterpriseops.runner.mark_tool_list
[runner.outputs]: #mas_optimiser.benchmarks.enterpriseops.runner.outputs
[runner.run]: #mas_optimiser.benchmarks.enterpriseops.runner.run
[runner.run_task]: #mas_optimiser.benchmarks.enterpriseops.runner.run_task
[runner.run_tasks]: #mas_optimiser.benchmarks.enterpriseops.runner.run_tasks
[runner.save_attempt]: #mas_optimiser.benchmarks.enterpriseops.runner.save_attempt
[runner.task_outcome]: #mas_optimiser.benchmarks.enterpriseops.runner.task_outcome
[runner.tool_arguments]: #mas_optimiser.benchmarks.enterpriseops.runner.tool_arguments
[runner.write_index_row]: #mas_optimiser.benchmarks.enterpriseops.runner.write_index_row
[runner.write_tables]: #mas_optimiser.benchmarks.enterpriseops.runner.write_tables
[seeds.build_seed_database]: #mas_optimiser.benchmarks.enterpriseops.seeds.build_seed_database
[sql.clauses]: #mas_optimiser.benchmarks.enterpriseops.sql.clauses
[sql.parse]: #mas_optimiser.benchmarks.enterpriseops.sql.parse
[states.diff]: #mas_optimiser.benchmarks.enterpriseops.states.diff
[states.read_state]: #mas_optimiser.benchmarks.enterpriseops.states.read_state
[states.rebuild]: #mas_optimiser.benchmarks.enterpriseops.states.rebuild
[states.seed_copy]: #mas_optimiser.benchmarks.enterpriseops.states.seed_copy
[tracing.attach]: #mas_optimiser.tracing.attach
[tracing.record_outcome]: #mas_optimiser.tracing.record_outcome
[tracing.task_trace]: #mas_optimiser.tracing.task_trace
