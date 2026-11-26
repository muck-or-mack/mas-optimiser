# The improvement loop

`mas-optimiser evolve <experiment>` improves a harness without a person in
the loop. It starts from a harness folder and a split of the tasks, and
repeats one cycle: read what went wrong on the training tasks, propose a
change, score the changed harness on the validation tasks, and keep the
change only if the acceptance gate says its gain is more than chance. This
page says what each step does, who may read what, how the gate is used
inside the loop, and which parts are this package's and which are taken
from released code.

One *experiment* is one settings file run on one split. Everything in it is
fixed at the start except the harness.

## One experiment, step by step

1. **The start.** The split's three sets, the settings and the starting
   harness are fixed. The harness folder is copied into a small git
   repository of the experiment's own, so that every later version of it is
   a commit.
2. **The baseline.** The starting harness is run on the training tasks,
   whose results are kept in full, and on the validation tasks, whose
   results only the gate reads.
3. **The analysis.** A model, the analyst, is given the score of every
   training task and may have the attempts of a share of them, the round's
   pool, read in detail ("What the search roles read"). It writes down the
   ways the attempts went wrong.
4. **The proposal.** A second model, the proposer, works in a copy of the
   harness folder. It can list and read the folder's files, read the pages
   of the pool's attempts, and edit the files. It must finish by naming
   each change it made and why. It is told what earlier proposals changed
   and whether each was accepted, and nothing else about them.
5. **Two checks before any scoring.** The change is reviewed for anything
   taken from a particular task and for any reference to the checks, and
   the changed harness is run on one training task to see that it still
   works and still writes its record in the same form.
6. **The score.** The changed harness is run on the validation tasks: on a
   share of them first, the filter, and on the rest only if it does better
   there than the current harness ("The filter before the full test").
7. **The decision.** The gate compares it with the current harness task by
   task. If it is accepted it becomes the current harness and is run on the
   training tasks, so that the next round reads its traces. If not, it is
   dropped and recorded. Steps 3 to 7 repeat for the number of rounds set.
8. **The test.** Every harness that was the current one at some point, the
   starting one included, is run once on the test tasks. That gives the
   test score after each accepted change, and no test result exists while
   any decision is still open.

The current harness is never run again on the validation tasks: its scores
there date from when it was the candidate.

## What a proposal can change

A harness is a folder of two files:

| file        | what it is                                                                               | a change to it                                                                      |
|-------------|------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|
| `agent.py`  | code: the program that calls the model, runs the tools it asks for and repeats           | try a failed tool call again; shorten a tool's over-long answer; plan before acting |
| `prompt.md` | text: words added to the end of every task's instructions; empty in the starting harness | "check every field before you finish"                                               |

A proposal may also add a file of an editable type to the folder, and
`agent.py` can import a file of code that is added beside it.

The proposer does not describe a change, it makes one. It works step by
step in a copy of the folder, with actions for files only: list the folder,
read a file, read a training attempt, replace one passage of a file with
another, create a file, and finish by naming each change and why. The
experiment's repository keeps every candidate as a commit on a branch of
its own, so the difference between a candidate and the current harness can
be read exactly, with `git diff`.

Its instructions ask for an order of work: open the trace of at least one
task the change is expected to move, and check there that the failure is
what the analyst's report says, before any edit; put a change where its
kind belongs, words for the agent in `prompt.md` and not in a string of
`agent.py`; and read each changed file back once before saying it is done.
None of this is enforced. The report says what the proposer did in each
round, under "How each proposal was made": the actions it took, the traces
it opened and the files it read.

In a trial of the loop the proposer's whole change was one paragraph added
to `prompt.md`, which began: "Before you finish a request, check every field
that the request or a policy table determines."

### Text and code

| `editable`       | a proposal may change         | what that means                                                 |
|------------------|-------------------------------|-----------------------------------------------------------------|
| `[".md"]`        | the text the agent is given   | nothing new runs: the model reads other words                   |
| `[".md", ".py"]` | the text and the agent's code | code written by a model runs on the machine that runs the tasks |

### The kind of a change

With each edit the proposer names its kind: the part of a harness the edit
changes. The names are RRSI's nine.

| kind              | what an edit of this kind changes                                  |
|-------------------|--------------------------------------------------------------------|
| `prompt`          | what the agent is told                                             |
| `context_mgmt`    | what the agent is shown: which tools, how much of the conversation |
| `output_plumbing` | how a tool's answer reaches the agent                              |
| `control_flow`    | when the agent goes on, stops or takes another turn                |
| `config`          | limits and constants                                               |
| `client_tool`     | a tool the harness runs by itself                                  |
| `skill`           | instructions loaded when they are needed                           |
| `memory`          | what is kept from one task to the next                             |
| `subagent`        | an extra call to a model, such as a planner or a checker           |

The kind says what a change does, not which file it is in: an instruction
written into `agent.py` is still `prompt`. The loop records the kind the
proposer names whenever the change edits the agent's code, and records
`prompt` for a change to `prompt.md` alone, whatever was named. The kind is
the proposer's own word: nothing checks that a change named `memory` keeps
anything. The record of kinds is read in two places: the proposer is shown
each earlier proposal's kind and hypothesis with whether it was accepted,
and the report gives the kind in its column `changed`.

### How many edits a candidate may hold

An edit is one independent change: it works by itself and has a kind and a
hypothesis of its own, and parts that depend on each other are one edit.
When the proposer says it is done it lists its edits, and a list longer
than the round allows is sent back to be merged or cut. The number allowed
is `edits_first` in the first round and `edits_last` in the last, and
follows RRSI's schedule between them, a half cosine rounded up; both are 1
unless set. The released code divides the round by the number of rounds,
which leaves the last round one step short of the lower number (3 and 1
over 10 rounds end at 2); here the last round is the schedule's end.

A candidate is run and judged as a whole. With several edits in it, an
accepted candidate shows that they helped together, and each edit is
recorded with the candidate's outcome.

### What stands between a change and its first run

| check, in order                 | what it is                                        | what it stops                                                                                                                                                                                                                                                                                                      |
|---------------------------------|---------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| the screen                      | a fixed list of patterns                          | a change to a file of a type that is not `editable`; a task's id or a record number; a reference to the checks; code that reads the environment, imports a module that reaches files, other programs or the network, runs text as code, reaches files outside the folder, or calls the model by a route of its own |
| the reviewer                    | a model that reads the change against the rules   | anything taken from one task, anything aimed at the checks, and code that reaches outside the folder or the clients it is given                                                                                                                                                                                    |
| a person, with `approval: each` | the change shown at the terminal, and a yes or no | whatever the person declines; a no ends the candidate and is recorded                                                                                                                                                                                                                                              |
| the one-task run                | the changed harness run on one training task      | a harness that no longer runs, or no longer writes its record in the form the package reads                                                                                                                                                                                                                        |

A change stopped by the screen or the reviewer goes back to the proposer
with the reasons.

An attempt can end in an error by chance: the model names a tool that does
not exist, or a service drops a call. That says nothing of the change, so
when the one-task run ends in an error it is given once more, and the second
try decides. The first try stays in the experiment's folder as the job
`r<round><candidate>_smoke`, and the second is `r<round><candidate>_smoke_again`.

The screen is what holds a proposal to the types of file in `editable`.
RRSI's tool for creating a file checks its type; its tool for editing one
does not, so with `.md` alone an edit of `agent.py` would otherwise go
through and be run.

### What code edits ask of the machine

Code the proposer writes runs as the user who started the experiment, and
can reach what that user can: files, and the keys in `.env`. The screen and
the reviewer catch accidents and plain misuse. They do not catch code set on
getting round them, and the package is not a sandbox.

So an experiment that edits code with `approval: none`, in which nobody
reads a change before it runs, starts only when
`--accept-unreviewed-code` is given, and prints what is being accepted
either way. With `approval: each` a person reads every change, and with
`editable: [".md"]` no code is written at all.

Two limits hold whatever the harness becomes, because the runner keeps them
and not the agent: an attempt is ended after `attempt_timeout` seconds
(3,600 unless changed) or `attempt_calls` calls to the model (100), and
recorded as an error; an attempt past its seconds is recorded so only once
the model service has been found to answer and the attempt has had its
further tries (`attempt_retries`, 2), since an outage runs an attempt past
its seconds too. A change that makes the agent loop without end therefore
costs a few attempts' limits and not the experiment.

A failure of a service is kept apart from a failure of the harness. An
attempt that ends because a call to the model failed is run again once the
model service answers a one-word question, and one that ends in an error
while the domain's server is down is run again once the server answers its
health check. An attempt the service never answered is no score at all: the
experiment stops there, without running the job's other attempts and
domains first, and carries on from that job when the command is given
again. Without this a run that sets the yardstick, hit by an outage,
would score too low, and the next candidate would look better than it is.
The analyst, the proposer and the reviewer are treated alike: a call of
theirs that fails while their model service is silent is not counted
against its six tries and is made again once the service answers, and a
service silent for `service_wait` seconds in all stops the experiment at
that round with a message that says so.

An attempt that ends in an error while its services answer is the harness's
failure, and a result like any other: it scores zero. An agent that asks for
a tool the server does not have ends this way, because the benchmark ends
such an attempt, and a weaker model does it more often. Its page gives the
conversation up to the error, from the runner's own record, so the search
roles can read what led to it. These attempts do not stop an experiment,
however many they are, and a change after which fewer attempts end in an
error gains by it at the gate. The released loop counts every attempt that
ended in an error as one with no result, refuses a job with more than 15% of
them, and scores the rest zero. Here an attempt that left no result at all
(the run of its domain ended before it wrote its results) is never scored:
one such attempt stops the experiment in the baseline, since a zero among
the scores a candidate is compared with would be a gain for every candidate
after it, and sets a candidate aside when a second run leaves it too. A
kept harness's run on the training tasks and the test at the end stop the
experiment likewise, and their job is run again when the command is next
given.

A person's no is a decision made outside the gate. An experiment run with
`approval: each` is the loop with a person in it, and is reported as such.

## Who may read what

| what                                                                     | the analyst and the proposer | the gate | a person, while the experiment runs |
|--------------------------------------------------------------------------|------------------------------|----------|-------------------------------------|
| the score of every training task                                         | yes                          | no       | yes                                 |
| how the training attempts went wrong, counted                            | yes                          | no       | yes                                 |
| the training attempts, in full                                           | those of the round's pool    | no       | yes                                 |
| the validation tasks and attempts                                        | no                           | scores   | the headline only                   |
| whether an earlier proposal was accepted                                 | yes                          | made it  | yes                                 |
| by how much a proposal changed them on the training tasks                | with `feedback: training`    | no       | yes                                 |
| by how much a proposal changed the score or cost on the validation tasks | no                           | yes      | yes, in the record                  |
| the test tasks and attempts                                              | no                           | no       | the headline only, at the end       |

The analyst has each trace of the pool examined by a digester, a call of
the model with three tools: listing the files of a folder, reading a file
and searching it, all held to the folder of that round's traces. RRSI's
digester is also offered a shell, whose commands are checked by the name of
the program alone, so that one could have named any file of the machine,
the validation results and the keys among them. That shell is switched off
here and is not offered. RRSI's listing follows `..` out of the folder and
would give the names of files anywhere, though not what they hold; here it
gives only what lies inside.

The proposer learns one thing from the validation tasks, the outcome of
each decision. The unit tests check it: the traces handed to the proposer
are of training tasks, and what it is told of an earlier proposal holds no
number from the validation tasks (`tests/unit/test_evolve.py`).

## What the search roles read

The analyst and the proposer, the search roles, work from the current
harness's run on the training tasks. A trace is the page of one attempt of
that run. Each round the two roles are given three things.

- **The score of every training task.** The tasks and their scores, lowest
  first, are put into both roles' instructions, so that neither takes the
  part it reads for the whole.
- **A count of how the attempts went wrong.** Over every training attempt
  of the current harness: how many passed every check, how many ended in
  an error and how, and what the failed checks found, most frequent first
  (the table and the column, what was wrong, and in how many attempts and
  tasks). It is added up from the table the runner writes for every
  evaluation, `failures.csv`, in the same words. A trace shows one attempt;
  the count shows which failures are common, so that a change is aimed at
  what happens often.
- **The traces of the round's pool.** The pool is a share of the training
  tasks, `pool_share`. Only a task of the pool has a trace that can be
  read: the analyst can have it examined, and the proposer can open it.

### What the proposer is told of its earlier proposals

RRSI's proposer is written to learn from its own record: its instructions
speak of the gain each earlier edit produced and of which predicted tasks
moved. RRSI can show it those numbers because it judges a candidate on the
tasks the proposer reads. Here a candidate is judged on the validation
tasks, and a number from them would let the proposer fit its changes to
the tasks that decide. `feedback` sets what it is told instead.

| `feedback` | the proposer is told of an earlier proposal                                      | what is run for it                                |
|------------|----------------------------------------------------------------------------------|---------------------------------------------------|
| `outcome`  | its kind, its hypothesis and whether it was accepted                             | nothing more                                      |
| `training` | that, and how it did on the training tasks, with RRSI's record of its predictions | every candidate, once more, on the training tasks |

With `training` a candidate that passes its one-task trial is run on the
training tasks before the filter. Three numbers are kept from that run
(`r<round>/<candidate>/training.json`) and given to the proposer in later
rounds:

- the difference between the candidate's score on the training tasks and
  the current harness's at the time, as a fraction of checks passed;
- the standard error of that difference over the training tasks, so that
  the proposer can tell a difference from chance;
- the relative change in the tokens an attempt used.

RRSI's own record of each edit's predictions is kept from the same two
runs: of the training tasks the edit said it would move, which did, and
which other tasks fell. No decision uses any of this. A candidate that is
kept is run on the training tasks in any case, for the next round's
traces, so the cost is one more run of them for each candidate that is
not kept. The report gives those runs as a part of their own, "the
candidates on the training tasks, for the proposer", beside the runs of the
harnesses that were kept.

### Why not every trace

Reading a trace is not one call to the model. The analyst reads none
itself. It sends a digester, a small agent that searches one trace with
read-only tools and reports back, and it may send several to one task with
different questions. In twelve trial rounds of this package the analysis
cost about 59,000 input tokens and 10 calls to the model for each trace
read. That is the median: the rounds ranged from 24,000 to 113,000, the
search model was a small one, and another will differ. At that rate, for
the 51 training tasks of the default split of the three domains:

| `pool_share`   | tasks in the pool   | rounds until every task has been in it   | input tokens a round, each task read once   |
|----------------|---------------------|------------------------------------------|---------------------------------------------|
| 0.1            | 5                   | 11                                       | 295,000                                     |
| 0.25           | 13                  | 4                                        | 767,000                                     |
| 0.5            | 26                  | 2                                        | 1,534,000                                   |
| 1              | 51                  | 1                                        | 3,009,000                                   |

The figures are in `docs/data/pool.csv`. The pool is a ceiling and not a
quota: the analyst's instructions, which are RRSI's, tell it to cover each
kind of failure and not every failure, and to read a few successes. With
the default share of 0.5 and the pick described next, every one of these
51 tasks has been in the pool after two rounds.

### How the pool is picked

The training tasks are ranked by their score with the current harness,
lowest first, and `pool_pick` says which of them make the pool.

- `spread` takes tasks at even steps along the ranking, and starts one
  place further each round. The pool then holds failures, part passes and
  full passes in about the proportions of the training set, and over the
  rounds every task comes into it. The attempt read is a task's
  lowest-scoring one.
- `ends` takes the lowest of the ranking and the highest, in the proportion
  22 to 6. RRSI's released code reads the 22 lowest and the 6 highest
  whatever the size of the set; this is that pick as a share. The attempt
  read is the lowest-scoring of a low task and the highest-scoring of a
  high one.

Which pick leads to better proposals has not been measured. A round in
which nothing is accepted leaves the ranking as it was: `ends` then has the
same traces read again, and `spread` has other tasks read.

### Limits kept from RRSI's code

The search roles run under limits that are constants of the released code.
Its paper does not give them, and they are not settings here.

| limit                                                                               | value            |
|-------------------------------------------------------------------------------------|------------------|
| turns the analyst may take in a round                                               | 30               |
| digesters the analyst may send in one turn                                          | 8                |
| calls to the model by one digester                                                  | 15               |
| length of one digest                                                                | 6,000 characters |
| turns the proposer may take on one candidate                                        | 40               |
| file operations (a passage replaced, a file created) in one drafting of a candidate | 80               |
| characters of a trace given to the proposer by one read                             | 60,000           |
| times a proposal is sent back after the reviewer objects                            | 5                |

## The gate inside the loop

The test is the one described on
[The acceptance gate](acceptance-gate.md): for each validation task, the
candidate's score minus the current harness's, and a one-sided test of
whether the mean of those differences is above zero. A task's score is its
fraction of checks passed, averaged over its attempts.

What changes inside a loop is the level of each test. One experiment makes
many decisions on the same validation tasks, and each one is another chance
to keep a change that does nothing. So the experiment has one error budget,
`alpha`, the chance over the whole experiment of keeping any such change,
and each decision gets a share of it. The share is that of Sun et al.
(arXiv:2609.33180, Eq. 4), as `stats.budget.reuse_level` computes it:

```
level = alpha * w_t * v_p / (C(t - 1, p) * K^(p + 1))
w_t = 1 / (t (t + 1))        v_p = 1 / ((p + 1)(p + 2))
```

with `t` the round, counted from 1, `p` the number of changes accepted in
earlier rounds, `K` the candidates per round and `C` the binomial
coefficient. The shares of every decision the experiment could come to make
add up to at most `alpha`, whatever order the decisions come in.

The price is that later decisions need larger gains. With `alpha` 0.05, one
candidate a round and 125 validation tasks:

| round   | changes accepted before   | level of the decision   | bar, in standard errors   |
|---------|---------------------------|-------------------------|---------------------------|
| 1       | 0                         | 0.0125                  | 2.27                      |
| 2       | 0                         | 0.0042                  | 2.68                      |
| 2       | 1                         | 0.0014                  | 3.05                      |
| 5       | 0                         | 0.00083                 | 3.21                      |
| 10      | 0                         | 0.00023                 | 3.60                      |

A single test at 5% has a bar of 1.66 standard errors, and the setting
`alpha_per: decision` gives every decision that: `alpha` is then the level
of each decision by itself, split equally between a round's candidates
(`stats.budget.bonferroni_level` for one decision), and the bar is the same
in every round. Nothing is then promised over the experiment as a whole.
With every change useless, the chance that at least one is kept is up to
1 - (1 - alpha)^n over n decisions: 23% for five at 0.05, and 40% for ten.
What carries the claim in that case is the test at the end, on tasks that
took part in no decision. An equal share for each of a number of decisions
fixed in advance is the same setting with `alpha` divided by that number.

### A second look at a near miss

A candidate that falls a little short of the gain it needs is dropped, and
it may be a real gain that this number of tasks and attempts could not
confirm. The setting `second_look` gives such a candidate a second test.
When the gate turns a candidate down and its p-value is at or under
`second_look`, the candidate and the current harness are each run for as
many further attempts at every validation task as the gate gave them, and
the test is made once more on all the attempts of both.

Two looks are two chances for a change that does nothing to be kept, so
with the setting on each look is tested at half the decision's level. Such
a change is kept if it passes the first look or the second, and the chance
of that is at most the sum of the two chances: at most the decision's
level, whichever candidates are sent to a second look and however the two
looks depend on each other. It is the reasoning by which a round's level is
split between its candidates (Bonferroni's inequality). Boundaries that
spend the level over the looks less evenly exist (Jennison and Turnbull,
*Group Sequential Methods with Applications to Clinical Trials*, Chapman
and Hall, 2000); they suppose more about the looks, and the even split
supposes nothing.

| | the first look | the second look |
|---|---|---|
| who | every candidate that reaches the gate | one turned down with a p-value at or under `second_look` |
| attempts at a task | the gate's, both harnesses | twice the gate's, both harnesses |
| level | half the decision's | half the decision's |
| bar at 125 tasks and 5% for the decision | 1.98 standard errors | 1.98 standard errors |

What the second look buys is a smaller standard error. Where the spread of
the differences is all in the attempts, twice the attempts divide it by
1.41, so a gain that stood at 1.6 standard errors stands at about 2.3 if it
holds. A change that helps some tasks and not others has a spread that
attempts do not reduce, and gains less. What it costs is the further
attempts, for a candidate inside the band only: two harnesses on every
validation task, or one when the current harness has had its further
attempts already, since it keeps them and later candidates are compared
with all of them. Its validation score in the report is from then on the
mean of all its attempts, so the line of scores can move in a round where
no change was kept.

The record of a decision that took a second look holds the second look's
numbers, and the first look's under `first`.

## The filter before the full test

Most proposals do not help, and each costs a run on every validation task
before the gate can say so. The filter spends less on those. A candidate is
run on a share of the validation tasks first. If its mean score there is
not above the current harness's by more than a threshold, it is dropped,
and the other tasks are never run. If it is, the other tasks are run and
the gate's test is made on all the validation tasks, the filter's among
them, so no attempt is run twice.

### Attempts at each stage

An experiment's `attempts` is the number of times a task is attempted at
every stage. Each stage can be given its own number.

| stage              | setting             | who is run                                                                                | what the attempts are for                                                                        |
|--------------------|---------------------|-------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| the training tasks | `attempts_training` | the starting harness, and each harness kept                                               | the scores, the count of failures and the one trace of each task that the search roles are given |
| the filter         | `attempts_filter`   | each candidate, on the filter's tasks                                                     | dropping a weak candidate cheaply                                                                |
| the gate           | `attempts_gate`     | the starting harness, and each candidate that passes the filter, on every validation task | the decision to keep a change                                                                    |
| the test           | `attempts_test`     | each harness kept, at the end                                                             | the gain that is reported                                                                        |

The gate's attempts are the ones that let it find a smaller gain, and the
dearest. The filter can have fewer. Its attempts are then the first of the
gate's: a candidate that passes is run for the attempts its filter tasks
still lack, and on the other tasks in full, so every validation task ends
with the gate's number and no attempt is run twice. The filter compares a
candidate's mean over its own attempts with the current harness's mean over
the gate's attempts, on the same tasks.

### Why it cannot make the gate less strict

The filter can only drop a candidate. One that is kept has still passed the
gate's test on every validation task, with all of the gate's attempts,
whatever number the filter used, so the candidates kept with a filter
are among those the gate would have kept without one. The chance of keeping
a change that does nothing is therefore at most what it was: the level of
the decision. This is the reasoning of stopping a trial early for futility
in sequential testing (Jennison and Turnbull, *Group Sequential Methods
with Applications to Clinical Trials*, Chapman and Hall, 2000).

### What it costs

A change that would have passed the gate can be dropped at the filter,
because on a part of the tasks a real gain can look like none. A larger
share loses fewer of them and saves less:

| `filter_share`   | validation cost of a change that does nothing   | a gain of the smallest findable size is kept   | a change that does nothing is kept   | a 5-point loss passes the filter, 3 attempts a task   | the same, 1 attempt a task   |
|------------------|-------------------------------------------------|------------------------------------------------|--------------------------------------|-------------------------------------------------------|------------------------------|
| none             | 100%                                            | 80%                                            | 5.0%                                 | -                                                     | -                            |
| 0.25             | 62%                                             | 75%                                            | 4.3%                                 | 3.3%                                                  | 13.0%                        |
| 0.5              | 75%                                             | 79%                                            | 4.8%                                 | 0.5%                                                  | 5.5%                         |
| 0.75             | 88%                                             | 80%                                            | 4.9%                                 | 0.1%                                                  | 2.5%                         |

The figures are computed and not measured (`docs/data/filter.csv`). They
suppose what the gate's test supposes, that the mean difference over the
tasks is close to normal, with a threshold of 0, a single test at 5% and
125 validation tasks. "The smallest findable size" is the gain that the
gate by itself keeps four times in five, the minimum detectable effect of
[The acceptance gate](acceptance-gate.md). The first three columns hold
whatever the spread of the scores. The last two need it, and use the spread
between the two baseline runs of that page: a task's difference has a
standard deviation of 15.2 percentage points at three attempts a
task and 24.8 at one.

### The tasks of the filter

They are drawn afresh for each round: the same share of each domain's
validation tasks, from the split's seed and the number of the round. An
experiment given again therefore draws the same tasks, and no fixed part of
the validation tasks carries every filter. All the candidates of a round
are filtered on the same tasks.

### What the proposer learns

No more than before. A candidate dropped at the filter is told to it as
rejected, like one the gate turned down. With `feedback: training` it is
told how that candidate did on the training tasks, as for any other.

### What the filter does not do

Every decision of an experiment still rests on the same validation tasks.
What keeps a harness from being fitted to them is unchanged: the proposer
never reads them, it learns only "accepted" or "rejected", each decision
has a smaller share of the error budget than the last, and the test tasks
take no part until the end.

## The settings

An experiment file holds the settings of `mas-optimiser run` (the model, the
domains, the attempts and so on) and those only `evolve` reads: `split`,
`harness`, `proposer_model`, `rounds`, `candidates`, `edits_first`,
`edits_last`, the four `attempts_` settings of the stages, `feedback`,
`alpha`, `filter_share`, `filter_threshold`, `pool_share`, `pool_pick`,
`editable` and `approval`. [Experiment settings](../settings.md) gives each
one's meaning, its default and what changing it does, and
`configs/experiments/template.yaml` lists them all to copy.

The agent's model and the proposer's are two settings, so they can be the
same model or different ones, hosted or self-hosted. `editable` and
`approval` are described under "What a proposal can change", and the two
settings of the filter under "The filter before the full test", and the
two of the pool under "What the search roles read". Each can
also be given for one command, as `--filter-share 0` for instance.
`configs/experiments/evolve-smoke.yaml` is a trial with three tasks of each
set and one round.

## What an experiment writes

```
evolutions/<experiment>/<split>/
  experiment.json             every setting as used, those not at their defaults, the proposer's model file, the code's version,
                              and how many tasks each set holds
  search_usage.jsonl          one line per call of the analyst, proposer and reviewer
  repository/                 the experiment's git repository
    harness/                  the current harness
  enterpriseops/
    frontier.json             the current harness and the score after each round
    history.jsonl             every proposal: what it changed, its outcome, its numbers
    global_analysis.json      the names the analyst last gave to failure modes and
                              habits, handed to it again in the next round
    test.csv                  one row per harness kept: its validation and test scores
    jobs/<job>/job.json       the tasks and the attempts the job is to have,
                              written when it starts (read by `status`)
    jobs/<job>/<domain>/      one evaluation folder per run of a harness; with
                              a filter a candidate's validation run is the
                              jobs <job>_filter and <job>_rest, and
                              <job>_more where the filter has fewer
                              attempts than the gate; <job>_again is a
                              harness's further attempts for a second
                              look, and <job>_both the result of all its
                              attempts
    r<round>/
      analysis/               the traces of the round's pool as the roles
                              read them, and every digest
      analysis_report.json    what the analyst found in the training traces
      decisions.json          the gate's numbers for each candidate, and
                              the filter's: its tasks, the gain there, the
                              threshold and whether the candidate passed
      <candidate>/            the proposal, its diff, its review, its trial run,
                              and with `feedback: training` how it did on
                              the training tasks (training.json)
  report/                     the report, written after each round and at the end, see below
```

Each job folder is an evaluation as described in
[Reading the results](../results.md); the summary of a job on validation or
test tasks gives the headline only. Giving the command again carries the
experiment on from the last step that left its result.

## Reading an experiment

```
mas-optimiser report evolutions/<experiment>/<split>
```

reads what an experiment wrote, finished or still going, and writes a
`report/` folder inside it. Nothing is run and nothing is measured again.
`evolve` does the same by itself after each round and when the experiment
ends, so the command is for writing a report again: for an experiment made
by an earlier version, one that stopped part-way through a round, or one
whose report a newer version would write better. A report that cannot be
written after a round is said in a warning and the experiment carries on.

| file                    | what it holds                                                                                                                                                                                                                                                                                                    |
|-------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `report.md`             | the tables below and the figures, as a page to read                                                                                                                                                                                                                                                              |
| `harnesses.csv`         | each harness the experiment kept, the starting one first: its validation and test scores, what an attempt at a test task used, and its gain on the test tasks over the starting harness                                                                                                                          |
| `rounds.csv`            | each candidate: the kind of its change, the proposer's hypothesis, the files and lines its change edited, how the proposal was made (the proposer's actions, the traces it opened, how often it was sent back), where it ended (review, one-task trial, filter or gate), and the filter's and the gate's numbers |
| `errors.csv`            | each job with attempts that ended in an error, which are scored zero: its attempts, those in error, and how many ran past their seconds or their calls                                                                                                                                                           |
| `parts.csv`             | the runs, tokens, dollars and hours of each part of the experiment, the agent's and the search roles'                                                                                                                                                                                                            |
| `figures/scores.png`    | the current harness's score after each round, on the validation tasks and, once the test step has run, on the test tasks                                                                                                                                                                                         |
| `figures/test_gain.png` | the gain of each harness kept over the starting harness on the test tasks, with its 95% confidence interval; drawn once the test step has run                                                                                                                                                                    |
| `figures/gate.png`      | each round's candidate at the gate: the gain it found, with its standard error, against the gain its decision needed; a candidate dropped at the filter is marked at its gain there                                                                                                                              |

Every figure is written as PDF and PNG, with the numbers it draws in a file
of the same name ending `.csv`. None has a title drawn on it: the page gives
each its caption.

`errors.csv` is the table to read before a figure is quoted. An attempt
that ends in an error is scored zero, however many there are, so a filter
of 80 tasks may hold any number of such zeros and be scored as it stands.
An attempt that left no result at all is another matter and is never
scored: its job is run again.

The page's table of rounds gives each candidate's gain on the validation
tasks beside the gain its decision needed, the bar times the standard
error: a candidate is kept when the first is above the second. The gain
needed grows from round to round, because each decision is allowed a
smaller share of the experiment's false acceptances than the one before
([The acceptance gate](acceptance-gate.md), "The level of each decision").

The gain on the test tasks is the mean, over the test tasks, of a task's
score with the harness less its score with the starting harness. It comes
with the gate's one-sided test and with a 95% confidence interval, 1.98
standard errors either way at about a hundred tasks
([Sizing an experiment](sizing.md)). The test tasks took no part in any
decision, so this gain is not flattered by selection as the validation
gain of an accepted change is. A search role's call is priced from the
tokens it used at the rates of its model file; the hours are the runs' own
times added up, not time on the clock.

## What is taken from RRSI and what is replaced

The loop is built on the released code of RRSI (Xia et al.,
arXiv:2609.24972; Apache License 2.0), kept unchanged in `vendor/rrsi`.

| part                                                           | whose                                                       |
|----------------------------------------------------------------|-------------------------------------------------------------|
| the analysis of traces, the proposer, the review of a proposal | RRSI's, as released, but for the digester's shell           |
| the shell RRSI offers a digester                               | switched off by this package (`evolve.loop`)                |
| a digester's listing of files                                  | held to the traces folder (`evolve.loop.TracesFolder`)      |
| the sentence that tells the proposer how a candidate is judged | this package's (`evolve.loop`): tasks it cannot read decide |
| the record of proposals, a git checkout per candidate          | RRSI's, as released                                         |
| what a benchmark must provide (its `Domain` interface)         | RRSI's; `domain.EnterpriseOps` fills it                     |
| the nine kinds of change and the rule that records one         | RRSI's; `domain.EnterpriseOps` gives its patterns           |
| the round and the baseline                                     | this package's (`evolve.loop`)                              |
| the pick of the training traces the roles may read             | this package's (`evolve.loop`)                              |
| the scoring of a candidate                                     | this package's when there is a filter                       |
| the rule that keeps a change                                   | this package's: the gate                                    |
| the model call of the search roles                             | this package's (`evolve.models`)                            |
| what the search roles are told about the benchmark             | this package's (`prompts`)                                  |

The round is replaced because RRSI reads its traces and judges its
candidates on one and the same set of tasks, and controls the resulting
over-fitting by limiting what a proposal may be. Here the two jobs have two
sets, and a proposal is judged on tasks its author never saw. RRSI's rule
keeps a candidate whose score is within a noise band of the best so far;
the gate asks for a gain that a test at a stated error level supports.

## Not built yet

- A rule on cost. The record holds every candidate's change in tokens, and
  no decision uses it.
- Carrying a job on from where it stopped. A job that was interrupted, or
  that holds an attempt a service never answered, is run again as a whole.
  So is a job with an attempt left without a result: a candidate's once,
  after which the candidate is set aside, and any other when the command is
  next given, the experiment having stopped.
- A second benchmark. The loop holds nothing of this benchmark's; another
  one needs a class like `domain.EnterpriseOps`.

The loop has been run from end to end in full experiments of ten rounds on
two models, and in shorter trials on a sample of the tasks.
