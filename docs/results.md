# Reading the results

Every run writes its results as files under `evaluations/`. This page says
what each file is, what every column in it means, and how to read one
attempt from its verdict down to the single value that was wrong. Its last
section does the same for a comparison of two runs.

One small run is followed throughout. To have it on screen, start the
benchmark servers (README, "Benchmark servers") and run:

```
mas-optimiser run smoke --limit 1 --no-trace
```

It runs one ITSM task once, in about 20 seconds and for under a cent, and
prints the folder it wrote. In that task a user reports that a Wi-Fi
device, given by its serial number and cost, keeps dropping, with high
urgency. The excerpts below come from one such run. The agent does not act
the same way twice, so another run of the same task can get different
values wrong.


## 1. The folder

```
evaluations/<experiment>/
  index.csv                       one row per evaluation of the experiment
  <run>/                          one run, named by its start: 2026-11-17-0817
    summary.md                    the run as a whole, then a row per domain
    run.json                      the run's settings and totals
    <domain>/                     one evaluation: itsm, csm or hr
      summary.md                  how the evaluation went and what went wrong
      tasks.csv                   one row per attempt
      tables.csv                  one row per attempt and table
      checks.csv                  one row per attempt and check
      cells.csv                   one row per failed check, row and condition
      failures.csv                one row per table, column and kind of failure
      tools.json                  every tool the domain's server offers
      evaluation.json             every setting, the task list and the totals
      evaluation.log              the benchmark's own log
      tasks/<task_id>/
        attempt-<n>.md            the attempt as a page
        attempt-<n>.json          the complete record of the attempt
        attempt-<n>.changes.json  every row the attempt inserted, updated or deleted
```

The files are of three kinds:

| ends in   | what it is                                             | when to open it                      |
|-----------|--------------------------------------------------------|--------------------------------------|
| `.md`     | a page to read, best in a Markdown preview             | first                                |
| `.csv`    | a table of data, one row per thing                     | to count or filter across attempts   |
| `.json`   | the record that the pages and the tables are made from | rarely: the pages show what it holds |

Read from the whole to the detail: the run's `summary.md`, then the
domain's `summary.md`, then the page of one attempt.


## 2. The words for a database

Each task is carried out on a database, and every result is stated in its
terms.

| word           | meaning                                                   | example                       |
|----------------|-----------------------------------------------------------|-------------------------------|
| table          | one kind of record                                        | `incident`                    |
| row            | one record of a table                                     | the incident `INC_024`        |
| column         | one field that every row of a table has                   | `priority`                    |
| cell           | one row's value in one column                             | `planning`                    |
| `table.column` | a column named with its table                             | `incident.priority`           |
| key            | the column whose value identifies a row                   | `incident.incident_id`        |
| reference      | a column whose value is the key of a row in another table | `incident.configuration_item` |

A reference is how two records are linked. It is often named after the
table it points at, so one name can mean two things. `configuration_item`
is a table of devices, and `incident.configuration_item` is the column of
an incident that says which device it concerns:

```
table configuration_item                      table incident
| configuration_item_id | serial_number |     | incident_id | configuration_item | priority |
| CI_005                | WIFI-UK-005   | <-- | INC_024     | CI_005             | moderate |
```

Every table of each domain, with its key, its columns and its references,
is on the data-model pages: [ITSM](data-model/itsm.md),
[CSM](data-model/csm.md) and [HR](data-model/hr.md).


## 3. What is judged

| word    | meaning                                                                                              |
|---------|------------------------------------------------------------------------------------------------------|
| task    | one request to the agent, with the tools it is given for it                                          |
| attempt | one run of the agent on one task; an experiment makes one or more attempts per task, numbered from 1 |
| check   | one query on the database as the attempt left it, with the answer it must give                       |

A check is almost always a count: the number of rows of one table that meet
a list of conditions, compared with the number expected, most often 1. The
example task has two:

| check           | counts the rows of   | that have                                                                         |
|-----------------|----------------------|-----------------------------------------------------------------------------------|
| Add CI          | `configuration_item` | the device's serial number, owner, location, status and cost                      |
| Report incident | `incident`           | the caller, the channel, the category, the priority and a reference to the device |

So the task has two steps, one per table: register the device, then report
an incident that refers to it. A check belongs to the table whose rows it
counts, and an attempt fully passes when every check passes.

The benchmark's own code counts some checks differently, and a few checks
are excluded or repaired here; the README ("Reading the results") gives the
three differences. Wherever a file has a `benchmark_` column, it holds the
benchmark's own count beside this package's.


## 4. The summaries

### The run

`<run>/summary.md` has one row for the run as a whole and one per domain:

| column                        | meaning                                                                             |
|-------------------------------|-------------------------------------------------------------------------------------|
| set                           | `all`, then each domain                                                             |
| tasks                         | tasks run                                                                           |
| attempts fully passed         | attempts in which every check passed, of all attempts                               |
| tasks passed on every attempt | tasks all of whose attempts fully passed                                            |
| errors                        | attempts that raised an error before they finished; they count as failed            |
| checks passed                 | checks passed, over all attempts                                                    |
| cost (USD)                    | the model's tokens, priced with the table in the model file                         |
| hours                         | the attempts' durations added up; several run at once, so the run itself takes less |

### The evaluation

`<domain>/summary.md` opens with a headline table that has a row per
measure and says what each one counts. Then "What went wrong", in two
tables. The first is the tables the attempts got wrong or missed:

```
| table                | attempts with a check on it   | correct   | wrong   | missed   |
|----------------------|-------------------------------|-----------|---------|----------|
| `configuration_item` | 1                             | 0         | 0       | 1        |
| `incident`           | 1                             | 0         | 1       | 0        |
```

The second is the most frequent failures, each counted once per attempt it
occurs in:

```
| table                | column               | failure           | how the value came about   | attempts   | tasks   |
|----------------------|----------------------|-------------------|----------------------------|------------|---------|
| `configuration_item` |                      | table not written |                            | 1          | 1       |
| `incident`           | `category`           | wrong value       | passed                     | 1          | 1       |
| `incident`           | `configuration_item` | wrong value       | left out                   | 1          | 1       |
| `incident`           | `priority`           | wrong value       | left out                   | 1          | 1       |
```

In words: the attempt never registered the device, and the incident it
reported had three wrong values, one that it passed wrong and two that it
never passed. The summary ends with the tasks that passed least and a table
of the files.

### The words the results use

How an attempt did on a table:

| result          | meaning                                                              |
|-----------------|----------------------------------------------------------------------|
| correct         | every check on the table passed                                      |
| wrong           | a check on the table failed, and the attempt changed the table       |
| missed          | a check on the table failed, and the attempt changed nothing in it   |
| not asked for   | the attempt changed a table that no check reads                      |
| read by a check | the attempt changed a table that only a check on another table reads |

Why a check failed:

| failure           | meaning                                                            |
|-------------------|--------------------------------------------------------------------|
| wrong values      | the row is there, and one or more of its cells are wrong           |
| too many rows     | more rows match than the check allows                              |
| table not written | no call wrote the table                                            |
| call failed       | a call that writes the table answered with an error                |
| no row written    | the calls that write the table succeeded, and no row of it changed |

Where a cell's value came from:

| how         | meaning                                                                               |
|-------------|---------------------------------------------------------------------------------------|
| passed      | a call passed that value for the column                                               |
| left out    | no call passed the column, though the tool that wrote the row takes it as an argument |
| no argument | the tool takes no such argument, so the server set the value                          |
| not passed  | no call passed the column, and the evaluation has no `tools.json` to say more         |

A wrong value that was `passed` is a wrong choice by the agent. One that was
`left out` could have been set and was not: here the task's instructions say
the system works out an incident's priority, the agent passed none, and the
server filled in its default.


## 5. The page of one attempt

`tasks/<task_id>/attempt-<n>.md` goes from the whole to the detail:

| section                   | what it shows                                                                              |
|---------------------------|--------------------------------------------------------------------------------------------|
| the heading               | PASS or FAIL, and how many checks passed                                                   |
| Request                   | what the agent was asked                                                                   |
| Tables the checks look at | one row per table: the checks on it, the rows the attempt changed, the result              |
| Tools the task was given  | each tool, the turns it was called at, and the arguments it takes                          |
| Checks                    | each check's query and, under a failure, a table of what was wanted against what was found |
| What the agent did        | turn by turn: what the model wrote, each tool call with its arguments, and what came back  |
| Rows the attempt changed  | every row inserted, with all its columns, updated or deleted                               |

The first table answers what the task needed and what the attempt did:

```
| table                | checks on it    | rows changed   | result   |
|----------------------|-----------------|----------------|----------|
| `configuration_item` | Add CI          | none           | missed   |
| `incident`           | Report incident | 1 inserted     | wrong    |
```

Under a failed check there is one of two tables. When the row is missing,
the page says whether the task was given a tool to write it, and lists what
the check wanted with the tool that takes each column:

```
No call wrote `configuration_item`. The task was given `register_configuration_item` to write it.

| check wants                      | found   | tool given that takes it      |
|----------------------------------|---------|-------------------------------|
| configuration_item_id = "CI_005" | no row  | none                          |
| serial_number = "WIFI-UK-005"    | no row  | `register_configuration_item` |
```

When the row is there, each column the check tests has a line: what was
wanted, what was found, whether that is right, and where the value came
from:

```
Row `incident_id INC_024`: inserted at turn 2 by `create_incident`.

| column               | wanted       | found    | ok    | where the value came from                                        |
|----------------------|--------------|----------|-------|------------------------------------------------------------------|
| `configuration_item` | = "CI_005"   | (empty)  | WRONG | left out at turn 2: `create_incident` takes `configuration_item` |
| `category`           | = "hardware" | network  | WRONG | passed at turn 2: `create_incident(category='network')`          |
| `priority`           | = "moderate" | planning | WRONG | left out at turn 2: `create_incident` takes `priority`           |
```

Three questions can be answered from the page alone:

1. What did the task need? The first table: one row per table the checks
   look at.
2. What did the attempt do about each? The same table's last two columns,
   then the turn named beside each value.
3. Could the agent have done otherwise? The tools section says what each
   tool takes; `left out` means the value could have been passed, and
   "the task was given ... to write it" means the missing row could have
   been written.

A check marked EXCLUDED is one that no correct attempt can satisfy. It is
listed with its query and is not counted. A lookup that found no record
reads "nothing found" in the turns: the server reports it as an error, and
it is the lookup doing its work.


## 6. The data files, column by column

Every file has `task_id` and `attempt` (the attempt's number, from 1) where
a row belongs to one attempt, so the files can be joined on them.

### `tasks.csv`: one row per attempt

| column                                                                     | meaning                                                          |
|----------------------------------------------------------------------------|------------------------------------------------------------------|
| `success`                                                                  | whether every check passed                                       |
| `checks_passed`, `checks_total`                                            | the checks passed, of the checks that are counted                |
| `tables_checked`                                                           | the tables with a check on them                                  |
| `tables_correct`, `tables_wrong`, `tables_missed`                          | those tables by result                                           |
| `benchmark_success`, `benchmark_checks_passed`, `benchmark_checks_total`   | the same verdicts as the benchmark's own code counts them        |
| `error`                                                                    | the error, when the attempt raised one before it finished        |
| `tool_errors`                                                              | tool calls that answered with an error                           |
| `lookups_empty`                                                            | lookups that found no record; they are not among the tool errors |
| `model_calls`                                                              | calls to the model                                               |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens` | the tokens the model read and wrote                              |
| `cost_usd`                                                                 | those tokens priced in US dollars                                |
| `seconds`                                                                  | how long the attempt took                                        |

### `tables.csv`: one row per attempt and table

A table has a row when a check counts its rows or the attempt changed it.

| column                           | meaning                                                            |
|----------------------------------|--------------------------------------------------------------------|
| `table`                          | the table, in lower case                                           |
| `checks`, `checks_passed`        | the counted checks on the table, and how many passed               |
| `inserted`, `updated`, `deleted` | the rows of the table the attempt inserted, updated and deleted    |
| `result`                         | `correct`, `wrong`, `missed`, `not asked for` or `read by a check` |

### `checks.csv`: one row per attempt and check

| column               | meaning                                                                              |
|----------------------|--------------------------------------------------------------------------------------|
| `position`           | the check's place in the task's list, from 1                                         |
| `check`              | the check's name; a second check of the same name is numbered: `Report incident (2)` |
| `passed`             | the verdict; empty for an excluded check                                             |
| `benchmark_passed`   | the benchmark's own verdict on the check                                             |
| `comparison`         | how the query's answer is compared with the expected one, `equals` in most checks    |
| `expected`, `actual` | the answer the check wants, and the one the query gave                               |
| `diagnosis`          | one line on a failure: the conditions that did not hold and what was found           |
| `kind`               | the form of that explanation, below; empty for a check that passed                   |
| `table`              | the table a failed check belongs to                                                  |
| `failure`            | why it failed, in the words of section 4                                             |
| `patch`              | `excluded` or `repaired` for a check this package changed; empty otherwise           |

| kind            | meaning                                                                |
|-----------------|------------------------------------------------------------------------|
| `cells`         | the row is there, and each condition was tested on it                  |
| `value`         | the check reads one value, not a count                                 |
| `no_rows`       | no row of the table was inserted or updated                            |
| `too_many_rows` | more rows match than the check allows                                  |
| `unsupported`   | a query that cannot be taken apart; only expected and actual are known |

### `cells.csv`: one row per failed check, row and condition

For a failed check whose row is there, every condition of the check on
every row the attempt touched, three rows at most.

| column         | meaning                                                          |
|----------------|------------------------------------------------------------------|
| `check`        | the check's name, as in `checks.csv`                             |
| `table`        | the table the check belongs to                                   |
| `row`          | the key of the database row: `incident_id=INC_024`               |
| `column`       | the column the condition tests; empty for a condition on several |
| `condition`    | the condition as the check writes it                             |
| `wanted`       | the condition without its column: `= "CI_005"`                   |
| `found`        | the value found in the row                                       |
| `ok`           | whether the condition holds                                      |
| `how`          | where the value came from, in the words of section 4             |
| `turn`, `tool` | the call it came from                                            |

### `failures.csv`: one row per table, column and kind of failure

The failures of the whole evaluation, most frequent first.

| column     | meaning                                                                 |
|------------|-------------------------------------------------------------------------|
| `table`    | the table                                                               |
| `column`   | the column with the wrong value; empty for a failure of the whole table |
| `failure`  | `wrong value` for a column, or the check's failure for a table          |
| `how`      | for a wrong value, where it came from                                   |
| `attempts` | the attempts the failure occurs in                                      |
| `tasks`    | the tasks those attempts belong to                                      |

### `index.csv`: one row per evaluation

At the experiment's level: the settings that tell evaluations apart (run,
domain, mode, model, harness, label, split) and each evaluation's totals.
`mas-optimiser list <experiment>` prints it as a table.

### The records

These hold everything the pages and tables are made from, and seldom need
opening.

| file                          | what it holds                                                                         |
|-------------------------------|---------------------------------------------------------------------------------------|
| `attempt-<n>.json`            | the whole attempt: the conversation, every tool call and result, each check's verdict |
| `attempt-<n>.changes.json`    | every row inserted, updated (each changed cell before and after) or deleted           |
| `tools.json`                  | every tool the domain's server offers, with its description and arguments             |
| `evaluation.json`, `run.json` | the settings and totals of the evaluation and of the run                              |

`tools.json` is the same for every task of a domain. The tools one task was
given are on its attempt's page.


## 7. After a change to the code

When a new version of the package writes these files differently, the old
runs do not have to be made again:

```
mas-optimiser rescore evaluations/<experiment>/<run>/<domain>
```

rewrites every file above from the attempts' saved records, without running
the agent or spending anything. A verdict changes only where the check
itself has been changed since the run (README, "Recomputing an evaluation").


## 8. Which tasks a summary counts

A summary counts every task its evaluation ran. The tasks are divided into
a training, a validation and a test set (README, "Splitting the tasks"),
and a change to the harness is chosen by looking at training tasks only. To
see what goes wrong without looking at the other two sets, run the training
set on its own:

```
mas-optimiser run <experiment> --split haiku-20-40-40:training
```

## 9. Reading a comparison

`mas-optimiser compare` sets a candidate beside the current harness on the
same tasks and decides whether its gain is more than chance. The command is
in the README ("Comparing two evaluations"), and the method, from a worked
example onwards, is on [The acceptance gate](methodology/acceptance-gate.md).
It writes one folder:

```
<folder>/
  comparison.md      the report, to read
  tests.csv          one row per outcome and set of tasks: every number of the test
  rules.csv          one row per decision rule
  sides.csv          one row per side: its run, settings, cost and time
  differences.csv    one row per task: its score on each side and their difference
  figures/           the report's four figures
```

The example here is the comparison of the two baseline runs, one harness run
twice, whose report and data files are in `docs/figures/gate/`, all but the
per-task file `differences.csv`. That file is not published: the optimiser
must not learn from the tasks its changes are judged on.

### Why the two sides are compared task by task

The test does not weigh one side's average against the other's. It takes
the difference on each task first:

| how the gain is taken                            | its standard error     |
|--------------------------------------------------|------------------------|
| each side's average first, then their difference | 4.22 percentage points |
| each task's difference first, then their average | 1.36 percentage points |

Tasks differ far more from one another than the two sides differ on any one
task. A task's score spreads 33 percentage points across the tasks, and the
two sides' scores on a task go together (a correlation of 0.90). Taking the
difference on each task removes the task's own difficulty and leaves what
changed between the sides, which spreads 15 percentage points. Compared as
two separate averages, a real gain of a few percentage points would be lost
in the spread between tasks (the numbers are from the comparison's
`differences.csv`).

### The report, part by part

| part of `comparison.md`   | what it answers                                                      | figure                    |
|---------------------------|----------------------------------------------------------------------|---------------------------|
| What is compared          | which runs, which tasks, how many attempts                           |                           |
| Result                    | each side's score, the gain, the verdict at the comparison's rule    |                           |
| Cost and time             | what each side cost, per attempt and in all                          |                           |
| By domain                 | the gain in each domain, for information                             | `gains`                   |
| The test in full          | every quantity of the test with what it is, and the checks beside it | `differences`             |
| Decision rules            | the verdict under each of four rules                                 | `decisions`, `detectable` |
| Data                      | which file holds which numbers                                       |                           |

In "By domain" only the row for all tasks decides. Each domain has a third
of the tasks, and looking at three domains gives three more chances for one
of them to clear the bar by chance.

### The four figures

In the order that builds up the result, which is not the report's:

| figure        | what it shows                                          | how to read it                                                                    |
|---------------|--------------------------------------------------------|-----------------------------------------------------------------------------------|
| `differences` | every task's difference, as a histogram                | how many tasks rose, fell and stayed the same; the line is their mean, the gain   |
| `gains`       | the gain with its interval, on all tasks and by domain | a row is accepted when its whole bar lies right of the no-gain line               |
| `decisions`   | what chance alone would give, and what was measured    | accepted when the measured gain lies right of the grey line; the shaded area is p |
| `detectable`  | the chance that a real gain of each size is accepted   | each curve crosses 80% at that rule's minimum detectable effect                   |

In `decisions` both curves are as wide as the standard error. The lower axis
is the gain in percentage points and the upper axis the same positions in
units of T, so the grey line stands at the bar and the measured gain at the T
observed.

On the example: 32 tasks rose, 27 fell and 66 stayed the same. The gain is
+0.71 percentage points with a standard error of 1.36, so T is 0.52 and p is
0.30. The two curves of `decisions` nearly coincide and the measured gain is
far short of the 2.3 percentage points needed, which is the right answer for
one harness run twice.

A gain accepted at the comparison's own rule can still be rejected by the
stricter rules of the "Decision rules" table. Those are for a run that makes
many decisions, where false acceptances would otherwise add up (the method
page, "The level of each decision").
