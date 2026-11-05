# How mas-optimiser works

This repository runs an LLM agent on the EnterpriseOps-Gym benchmark (IT
service management, customer service and HR tasks against a simulated
ServiceNow-style system), measures the result properly, and explains every
failure down to the database cell that is wrong and the turn of the
conversation that set it. That measurement layer is finished. On top of it
the project builds a ladder of better agents ("harnesses") and an optimiser
that improves the best of them by editing its planning layer.

Two figures go with this document. `figures/one-attempt.svg` is the life of
one attempt as it runs today; `figures/ladder-and-optimiser.svg` is where
the project goes next. Both are pages of `figures/architecture.drawio`,
which draw.io opens for editing. Grey boxes are the benchmark's own code,
vendored unchanged under `vendor/enterpriseops-gym/`; blue boxes are this
package; yellow boxes are services outside the process; green shapes are
files.

The document follows one real attempt from the 3 November baseline so
that every mechanism is shown on data: task
`task_20251212_191815_314_391f1eff_c23d76df` (ITSM, in the training set),
attempt 1, whose page is
`evaluations/baseline-haiku/20261103-080328-itsm/tasks/<task>/attempt-1.md`. Line
numbers refer to the files at the commit that last changed this document
(`git log -1 -- docs/how-it-works.md`).


## 1. The pieces, sized

| Layer | Files | Lines | What it does |
|---|---|---|---|
| The harness (the agent, to be improved) | `vendor/enterpriseops-gym/orchestrators/react.py`, `base.py`; `benchmark/executor.py`, `llm_client.py`, `mcp_client.py`, `verifier.py`, `models.py` | about 2,100 | the loop of model calls and tool calls, the database set-up on the domain server, the SQL checks |
| The runner (ours) | `src/mas_optimiser/cli.py`, `tracing.py`, `benchmarks/enterpriseops/runner.py` | about 1,300 | loads the tasks, runs attempts in parallel, applies the patch list, saves every record and the tables |
| The measure (ours) | `checks.py`, `states.py`, `diagnosis.py`, `report.py`, `rescore.py` | about 1,800 | verdicts, changes against the seed, the cell-by-cell diagnosis, the trace to the turn, the results files, recomputation |
| Set-up tools (ours) | `seeds.py`, `splits.py` | about 680 | local copies of the seed databases; the one-off draw of the training, validation and test sets |

Line counts are from `wc -l` on 4 November 2026; a third of this package's
lines are docstrings, since every function states its arguments, its return
value and what it raises. The benchmark's own
runner (`vendor/enterpriseops-gym/evaluate.py`) is not used, because the
runner has to own the loop so that later agents plug in as orchestrator
classes (section 2.4), and because its scoring loses checks (section 2.5).


## 2. One attempt, start to finish

### 2.1 The command

`mas-optimiser run baseline-haiku` enters at `cli.py:51`, which builds one
sub-command per module in `COMMANDS` (`cli.py:22`): `run`, `rescore`,
`split`, `schema`. Each module gives its options in `add_arguments` and its
work in `run`.

`runner.run` (`runner.py:915`) reads the experiment file
`configs/experiments/baseline-haiku.yaml` through `load_experiment`
(`runner.py:310`), which layers the defaults in `EXPERIMENT_DEFAULTS`
(`runner.py:80`), then the file, then any command-line override, and stops
on a bad setting before any money is spent. `load_model` (`runner.py:437`)
reads `configs/models/haiku-5-5.yaml`: the benchmark's model fields, the
name of the environment variable that holds the API key (read from `.env`,
never written to a config), and a price table. Then one `evaluate` per
domain (`runner.py:950`).

### 2.2 The tasks

`load_tasks` (`runner.py:256`) reads the Hugging Face dataset at a pinned
commit (`HF_REVISION`, `runner.py:66`), so an upstream edit cannot change
the tasks between evaluations. In oracle mode, the only mode used so far, the
benchmark's corrected copies under `vendor/enterpriseops-gym/data/revised/`
replace the dataset's versions, and the agent gets only the tools the task
needs. A task config has four parts the rest of this document keeps
returning to:

- `system_prompt`: the domain's policy document, several pages of roles,
  mandatory fields, defaults and rules. For ITSM it says, among much else,
  that an incident's `channel` defaults to `chat` and its `impact` to `low`
  when the request gives neither, and that `priority` is derived from
  impact and urgency by a matrix.
- `user_prompt`: the request. The example's reads, in short: Isabella Rossi
  wrote in; her email server (serial SERVER-SF-SRV01) has been down for a
  long time; it is highly impacting her work; she needs it fixed with high
  urgency; check for services linked to the server and associate them;
  record an incident; send her a report that it is registered.
- `gym_servers_config`: which domain server to use and which seed file to
  load into it.
- `verifiers`: the checks, each an SQL query on the final database with an
  expected value. The example has three:

```sql
Link SLA:          SELECT COUNT(*) FROM incident_sla WHERE incident_sla_id = "TSLA_011"
                   AND incident_id = "INC_024" AND sla_def_id = "SLA_007" AND stage = "in_progress"
Send notification: SELECT COUNT(*) FROM notification WHERE notification_id = "NOTIF_008"
                   AND incident_id = "INC_024" AND email = "isabella.rossi@acme.com" AND type = "report"
Record Incident:   SELECT COUNT(*) FROM incident WHERE incident_id = "INC_024" AND service = "SVC_003"
                   AND configuration_item = "CI_004" AND caller_id = "USER_010" AND channel = "chat"
                   AND status = "new" AND category = "software" AND impact = "high"
                   AND urgency = "high" AND priority = "critical"
```

Each expects the count 1. A task passes only when every check passes.

### 2.3 The executor

`evaluate` (`runner.py:950`) keeps the tasks of one split set if asked, cuts
to `--limit`, checks that the domain servers answer (`check_servers`,
`runner.py:418`), makes the evaluation folder, sends the benchmark's
logging to `evaluation.log` there, and calls `run_tasks` (`runner.py:784`), which
runs every attempt through `run_task` (`runner.py:671`) with at most
`concurrency` in flight (four in the baseline). Each attempt seeds its own
database on the server, so attempts share nothing.

`run_task` builds a `TaskExecutor` (`runner.py:125`), our subclass of the
benchmark's `BenchmarkExecutor` (`executor.py:23`). The subclass adds three
things: the checks are renamed and patched before the attempt
(`checks.prepare`, called at `runner.py:160`, section 2.5); the Langfuse
callback is attached to
the model once the executor has built it (`runner.py:171`); and after the
checks run, while the database still exists, the final state is read and
every failed check diagnosed (`runner.py:184`, section 2.6).

The benchmark's `execute_benchmark` (`executor.py:482`) then does the work:

1. `initialize` (`executor.py:59`) connects an `MCPClient` to each gym
   server (`executor.py:79-92`), discovers the tools the server offers
   (`executor.py:95`, `_discover_and_merge_tools` at 214), builds the
   `LLMClient` (`executor.py:110`) and the `VerifierEngine`
   (`executor.py:126`).
2. `execute_single_run` (`executor.py:309`) seeds a fresh database from the
   task's seed file (`executor.py:323`, which posts the SQL to the server's
   `/api/seed-database`, `mcp_client.py:19-58`), points the client at it
   (`executor.py:349`), builds the orchestrator (`executor.py:352`) and
   calls its `execute()` (360), runs the checks (363), and declares the
   attempt a success only if all of them passed (371).
3. The `finally` block deletes the attempt's database
   (`executor.py:553-563`). This is why the final state has to be read
   inside our `_run_verifiers` override: afterwards it is gone.

### 2.4 The agent loop: what the harness is

The whole agent is `ReactOrchestrator.execute` (`react.py:19-125`), a plain
loop:

1. The conversation starts as two messages: the policy as the system
   message and the request as the user message (`react.py:22-25`).
2. For up to `max_iterations` turns (`react.py:34`; 50 by default,
   `base.py:23`): call the model with the messages and the tool list
   (`react.py:38`). `invoke_with_tools` (`llm_client.py:353`) converts the
   MCP tool schemas into function definitions (`llm_client.py:220`,
   cleaning schema shapes the model APIs reject, 250), binds them to the
   model (362) and retries a failed call up to three times (363).
3. The reply is appended to the messages (`react.py:47`) and recorded in
   `conversation_flow` with its text, token usage and tool calls
   (`react.py:58-69`).
4. If the reply has no tool calls, the loop ends (`react.py:74-76`): the
   agent has decided it is done, or has answered in prose.
5. Otherwise each tool call is executed (`react.py:85`, through
   `base.py:46`, which looks up the tool's gym at 49 and calls
   `client.call_tool` at 59). `call_tool` (`mcp_client.py:306`) sends one
   JSON-RPC `tools/call` request to the server's `/mcp` endpoint with the
   attempt's database id in a header (`mcp_client.py:185`), so every tool
   acts on this attempt's database. The result comes back as a
   `ToolMessage` the model sees on its next turn (`react.py:103-108`), and
   is recorded in `conversation_flow` (110-117).

The loop returns the final text, the full `conversation_flow`, the tools
used and the tool results (`react.py:119-125`); the executor keeps all of
it in the attempt's record (`executor.py:380-398`).

The example took five turns:

| Turn | Tool call | What came back |
|---|---|---|
| 1 | `get_user_using_email` and `find_configuration_item_by_serial_number` | the user (USER_010) and the server (CI_004, in use) |
| 2 | `find_services` | the service list; the agent picks "Acme Email Service" (SVC_003) |
| 3 | `create_incident` with caller USER_010, service SVC_003, CI_004, channel `email`, impact `low`, urgency `high`, category `software` | incident INC_024 with priority `planning`, set by the server |
| 4 | `send_notification` to the user with type `update` | notification NOTIF_008 |
| 5 | no tool call: a summary for the user | the loop ends |

So the harness, the thing the project improves, is: the loop (`react.py`),
the model client (`llm_client.py`), the tool client (`mcp_client.py`), the
policy prompt (from the task), and the tool list (from the server). Three
seams let a different agent in without touching the benchmark's code:
`BenchmarkExecutor` accepts any `AgentOrchestrator` subclass
(`executor.py:39-46`, `base.py:14`), and our `run_task` passes
`ReactOrchestrator` by name; the task config can be edited before the attempt;
and `max_iterations` and other orchestrator arguments pass through
`orchestrator_kwargs` (`executor.py:358`). Section 4 says how the ladder
uses these seams.

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
  only the last. `unique_names` (`checks.py:77`) drops exact copies and
  renames the clashes before the attempt, so each distinct check is kept once.
- 33 count checks give their expected value as the string `'1'` where the
  SQL runner returns the integer `1`, which `==` never matches. `recheck`
  (`checks.py:254`) compares such a pair numerically after the attempt.
- Some checks are wrong in themselves: no correct attempt can satisfy them,
  or they name a record from another seed. `data/check_patches.json` lists
  22 as excluded and 8 as repaired, each with its evidence. `apply_patches`
  (`checks.py:131`) takes a task's list through it before the attempt, and
  `original_verdicts` (`checks.py:217`) runs each patched check's original
  query afterwards, so `checks.csv` carries the benchmark's own verdict
  (`upstream_passed`) next to ours and a `patch` column saying which.

`upstream_verdict` (`checks.py:293`) recovers what the benchmark would have
reported for the attempt, and `check_records` (`checks.py:321`) flattens
the verdicts into the rows of `checks.csv`. The example's three checks all
failed, under both conventions.

### 2.6 What changed, and why each check failed

A failed count check says only that a conjunction did not hold. To say
which condition failed, and what the agent left instead, our
`_run_verifiers` override (`runner.py:184`) does two more things while the
database exists.

First it reads back what the attempt changed. `read_changes`
(`runner.py:218`) calls `states.read_state` (`states.py:96`), which reads
every table of the attempt's database through the SQL runner a page at a
time, and `states.diff` (`states.py:143`), which compares it row by row
with a local copy of the seed: the rows inserted, the rows deleted, and for
each updated row the cells that changed with their values before and after.
The local seed copy comes from `seed_copy` (`states.py:78`), built once per
seed file by `seeds.build_seed_database` (`seeds.py:188`) from the schema
committed under `data/schemas/` (fetched from the servers once by
`mas-optimiser schema`) and the seed file's inserts. The example changed
two tables: one row inserted in `incident`, one in `notification`.

Then each failed check goes through `diagnosis.diagnose`
(`diagnosis.py:186`). A query that reads one value (`SELECT col FROM t
WHERE key`) is already wanted against actual (206-213). A count query is
taken apart into its table and its top-level AND conditions
(`parse_count_query`, 94; `split_conditions`, 63). If the count was too
high, the surplus rows are fetched (221-229). If the attempt touched no row
of the table, the kind is `no_rows` (234-236). Otherwise every condition is
tested on its own against each touched row (237-259), and for a condition
that fails, the column's actual value is fetched (254-258). Every query
runs on the attempt's own database; no model is involved, and a condition
an untouched row happens to satisfy is never credited.

The example's diagnosis:

| Check | Kind | What the diagnosis found |
|---|---|---|
| Link SLA | `no_rows` | the attempt inserted or updated nothing in `incident_sla` |
| Send notification | `cells` | row NOTIF_008: id, incident and email right; `type` is `update`, wanted `report` |
| Record Incident | `cells` | row INC_024: seven conditions right; `channel` is `email`, wanted `chat`; `impact` is `low`, wanted `high`; `priority` is `planning`, wanted `critical` |

### 2.7 From a wrong cell to the turn that set it

`report.py` makes the link back to the conversation, mechanically.
`calls` (`report.py:107`) pairs each tool call with its result, numbered by
turn, and marks whether it succeeded (`succeeded`, `report.py:89`: the
request got through and the tool did not answer with an error).
`writers_of` (`report.py:171`) finds the calls that write a table: a
writing verb in the tool's name and the table's words in the rest of it
(`create_incident` writes `incident`; `send_notification` does not), with a
successful call whose result echoes the table's key column as the fallback.
`trace` (`report.py:216`) then links those calls to the touched row through
its key, names for each wrong cell the last linked call whose arguments set
that column (244-255), and gives the check a step (256-263):

- `skipped`: no call wrote the table;
- `failed`: a call that writes the table returned an error;
- `wrong`: the row is there with wrong cells;
- `attempted`: the calls succeeded and the check still fails, as when the
  row went elsewhere.

On the example:

| Check | Step | Trace |
|---|---|---|
| Link SLA | skipped | no call wrote `incident_sla` |
| Send notification | wrong | `type` set at turn 4 by `send_notification(type='update')` |
| Record Incident | wrong | `channel` and `impact` set at turn 3 by `create_incident`; `priority` is in no call's arguments (the server derives it from impact and urgency) |

Read together with the request and the policy, the attempt failed on three
readings of the text: the request names no SLA, so the agent linked none;
it says the user "wrote to us", so the agent chose `email` over the
policy's default `chat`; it says the problem is "highly impacting", which
the agent did not turn into `impact = high`, and the low impact pulled the
priority down with it.

`outputs` (`runner.py:557`) attaches the trace to each failed check and
builds the attempt's rows: `task_record` (`runner.py:499`) for `tasks.csv`
(verdicts under both conventions, tool errors, model calls, tokens, cost in
US dollars from the price table, seconds), `check_records` for
`checks.csv`, `cell_rows` (`report.py:424`) for `cells.csv` and
`change_rows` (`report.py:461`) for `changes.csv`. `save_attempt`
(`runner.py:599`) writes the record (`attempt-N.json`), the changes
(`attempt-N.changes.json`) and the page (`attempt-N.md`, from
`attempt_text`, `report.py:345`). When every attempt is done, `write_tables`
(`runner.py:623`) writes the four CSVs and `summary.md` (`summary`,
`report.py:491`: the headline numbers, failed checks by step, skipped
steps by table, wrong cells by column, the worst tasks), and `evaluate`
writes `evaluation.json` (every setting and total) and a row in the experiment's
`index.csv` (`append_index`, `runner.py:398`).

With Langfuse configured, `tracing.task_trace` (`tracing.py:37`) wraps each
attempt in one trace, `attach` (59) routes the model's calls into it, and
`record_outcome` (64) scores the trace with the verdict and puts the checks
and changes in its output.

### 2.8 Recomputing without the agent

`rescore.py` recomputes an evaluation from its saved records: `rebuild`
(`states.py:232`) applies an attempt's saved changes to a fresh seed copy,
`check_locally` (`rescore.py:68`) runs every check on that database as the
executor would, and the same diagnosis, trace and files follow
(`rescore_attempt`, `rescore.py:128`). It exists so that a change to the
diagnosis, the trace or the patch list can be applied to every past
evaluation in seconds, with no model cost, and so that the saved changes
are enough to reproduce a verdict.


## 3. Reading an evaluation

Start at `summary.md` in the evaluation folder: the headline table, then
"What went wrong" (failed checks by step, steps skipped by table, cells
wrong by column), then the worst tasks. Open a task's `attempt-N.md` for
the request, the turns, the changes and every check with its condition
table and trace. `cells.csv` is the same information for every failed
check at once, one row per touched row and condition, with the turn and
tool that set a wrong cell; `checks.csv` has every check's verdict under
both conventions. The README's "Reading the results" section lists every
column.


## 4. Where this is going

The second figure shows the plan. The harness ladder (P1) changes one thing
per rung and compares each rung with the one below on the validation tasks:

- `default`: the loop of section 2.4 as it is; the benchmark's reference.
- `interface_repair`: the same loop plus interface repairs of the kind the
  published harness papers apply, such as argument cleaning, schema fixes
  and a finish tool, each repair traced to a failure in the training set.
  In code this is a second orchestrator class, or a wrapper around the tool
  call, passed to the executor in place of `ReactOrchestrator`.
- `planner`: a planner-executor graph built with LangGraph as another
  orchestrator class: a planner writes a plan, a checker enforces plan
  constraints in code before each tool call, an executor runs the steps,
  and a failed step triggers a bounded replan rather than ending the attempt.
  Its planner prompt, constraints and replanning rules live in
  `harnesses/<name>/harness.yaml`, as data.

The optimiser (P2) starts from `planner`. A proposer (an LLM, offline)
reads the failures of the training tasks as the measure reports them
(failed checks by step, wrong cells by column, excerpts of the failing
turns) and proposes one edit to `harness.yaml`. The candidate runs on the
validation tasks through the runner of section 2, and an acceptance gate
compares it with the current harness task by task. Accepted edits
accumulate into `evolved`; rejected ones feed the next proposal. The graph
code in `src/` is the frozen engine, so every edit is data that can be
read and audited, and nothing in the loop touches the benchmark's code.


## 5. Running, testing and debugging

Running is in the README: the servers (`docker compose -f
docker/compose.yaml up -d --wait`), then `mas-optimiser run <experiment>`.
To see one attempt without spending more than a few cents:

```
mas-optimiser run smoke --split haiku-20-40-40:training --limit 1 --attempts 1 --label poke
```

then read the page under `evaluations/smoke/`. `mas-optimiser rescore <folder>`
on that folder rewrites every file from the saved changes; nothing should
change.

The unit tests (`uv run pytest tests/unit`, 117 tests on 4 November 2026, no servers needed)
cover the measure on hand-written records. The integration tests
(`tests/integration`) need the servers and check, among other things, that
the local seed copy matches the server's database row for row.

What goes wrong, and where to look:

| Symptom | Meaning | Where |
|---|---|---|
| `error: domain server ... is not answering` | the Docker servers are down | `check_servers`, `runner.py:418`; start them and rerun |
| `ANTHROPIC_API_KEY is not set` | no `.env`, or the key missing from it | `load_model`, `runner.py:437` |
| a `tasks.csv` row with `error` filled and zero checks | the attempt raised before the agent started (a server fault, a bad config) | the record's `error`; the traceback in `evaluation.log` |
| `diagnosis_error` on the run in `attempt-N.json` | the checks ran but the diagnosis raised; verdicts are intact | `runner.py:184-216` |
| `report_error` on the run | the trace or the rows raised; the page has no trace | `run_task`, `runner.py:671` |
| a failed check with `kind` empty in `checks.csv` | a query shape the diagnosis cannot take apart (a top-level OR, a GROUP BY, a derived table) | `diagnosis.py:94-111` |
| an attempt with 50 model calls | the agent hit `max_iterations` | `react.py:34`, `base.py:23` |
| `tool_errors` above zero | a tool answered with an error; the page marks the call `ERROR` | `succeeded`, `report.py:89` |


## Appendix: file map

| File | Role |
|---|---|
| `src/mas_optimiser/cli.py` | the `mas-optimiser` command and its sub-commands |
| `src/mas_optimiser/tracing.py` | Langfuse: one trace per attempt, scored |
| `.../enterpriseops/runner.py` | experiments, tasks, the executor subclass, attempts in parallel, the results files |
| `.../enterpriseops/checks.py` | distinct checks, the patch list, the numeric recheck, the benchmark's verdict beside ours |
| `.../enterpriseops/states.py` | the final state read back, the diff against the seed, the local rebuild |
| `.../enterpriseops/diagnosis.py` | each condition of a failed check tested on the touched rows |
| `.../enterpriseops/report.py` | the trace to the turn, the attempt page, the CSV rows, the summary |
| `.../enterpriseops/rescore.py` | recompute an evaluation from its saved changes |
| `.../enterpriseops/seeds.py` | local copies of the seed databases; the `schema` command |
| `.../enterpriseops/splits.py` | the stratified, sealed split of the tasks; drawn once |
| `vendor/.../orchestrators/react.py` | the agent loop |
| `vendor/.../orchestrators/base.py` | the orchestrator interface and the tool call |
| `vendor/.../benchmark/executor.py` | seed, run, check, clean up |
| `vendor/.../benchmark/llm_client.py` | the model behind LangChain, tool schema conversion, retries |
| `vendor/.../benchmark/mcp_client.py` | JSON-RPC to the domain server; database create and delete |
| `vendor/.../benchmark/verifier.py` | the SQL checks |
| `vendor/.../benchmark/models.py` | the config dataclasses |
