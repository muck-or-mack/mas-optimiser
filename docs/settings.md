# Experiment settings

An experiment is one YAML file in `configs/experiments/`. This page explains
every setting such a file can hold: what it is, what it does, and what
changing it does to the experiment. `configs/experiments/template.yaml`
lists the same settings at their defaults with a few lines each, to copy;
the <a href="_static/planner.html">experiment planner</a> shows what a
design costs and can show before anything is run.

A value comes from three places, the later winning: the default, the
experiment file, then an option on the command line (`--attempts 3`
overrides `attempts: 1`). A file with a setting that does not exist is
refused, with the list of those that do, and every value is checked before
anything is run or spent.

## All settings at a glance

"Read by" says which command uses the setting: `run` scores a harness on
tasks, and `evolve` is the improvement loop, which also uses every setting
of `run`. "Planner" marks the settings the planner has a control for.

| setting                                 | default           | read by | planner |
|-----------------------------------------|-------------------|---------|---------|
| [`model`](#model)                       | none, must be set | both    |         |
| [`proposer_model`](#proposer-model)     | the agent's model | evolve  |         |
| [`harness`](#harness)                   | `default`         | both    |         |
| [`domain`](#domain)                     | none, must be set | both    |         |
| [`mode`](#mode)                         | `all_tools`       | both    |         |
| [`split`](#split)                       | none              | both    |         |
| [`limit`](#limit)                       | none (all)        | both    |         |
| [`task`](#task)                         | none              | run     |         |
| [`attempts`](#attempts)                 | 1                 | both    | yes     |
| [`concurrency`](#concurrency)           | 4                 | both    |         |
| [`model_timeout`](#model-timeout)       | 300               | both    |         |
| [`attempt_timeout`](#attempt-timeout)   | 3600              | both    |         |
| [`attempt_calls`](#attempt-calls)       | 100               | both    |         |
| [`attempt_retries`](#attempt-retries)   | 2                 | both    |         |
| [`service_wait`](#service-wait)         | 1800              | both    |         |
| [`trace`](#trace)                       | true              | both    |         |
| [`thinking_summary`](#thinking-summary) | false             | both    |         |
| [`prompt_caching`](#prompt-caching)     | true              | both    |         |
| [`label`](#label)                       | none              | both    |         |
| [`out`](#out)                           | none              | run     |         |
| [`rounds`](#rounds)                     | 10                | evolve  | yes     |
| [`candidates`](#candidates)             | 1                 | evolve  | yes     |
| [`alpha`](#alpha)                       | 0.05              | evolve  | yes     |
| [`filter_share`](#filter-share)         | 0.5               | evolve  | yes     |
| [`filter_threshold`](#filter-threshold) | 0                 | evolve  |         |
| [`pool_share`](#pool-share)             | 0.5               | evolve  | yes     |
| [`pool_pick`](#pool-pick)               | `spread`          | evolve  |         |
| [`editable`](#editable)                 | `.md` and `.py`   | evolve  |         |
| [`approval`](#approval)                 | `none`            | evolve  |         |

## What is run

### `model`

The model the agent uses, named by its file in `configs/models/` (`model:
haiku-5-5` is `configs/models/haiku-5-5.yaml`) or given as a path. It has no
default and must be set. The model decides more than any other setting: the
scores, the tokens and dollars of an attempt, and how many attempts finish
in an hour. Two experiments that differ in the model are two experiments,
each with its own baseline; their scores are compared, never pooled. The
model's own values (its provider, temperature, the length of an answer, its
prices) are in the model file, not here: see
[what is set elsewhere](#what-is-set-elsewhere).

### `proposer_model`

The model of the roles that search for a better harness: the analyst, which
reads how the current harness did on the training tasks, the proposer, which
writes a change, and the reviewer, which checks it. Left out, it is the
agent's model, so an experiment uses one model throughout unless it says
otherwise. Naming another model makes a different experiment: a stronger
proposer may write changes that pass the gate more often, at that model's
price for each round, and the result then belongs to the pair of models and
not to the agent's alone. The roles and what each may read are on
[The improvement loop](methodology/improvement-loop.md).

### `harness`

The harness the agent runs: a folder under `harnesses/` by name, or a path
to such a folder. The folder holds the agent's code (`agent.py`) and the
text it adds to every task's system message (`prompt.md`). `default` is the
benchmark's own agent with nothing added. For `evolve` it is the harness
the experiment starts from: the folder is copied into the experiment's own
git repository, and every change the loop keeps is a commit there, so the
named folder itself is never changed.

### `domain`

The benchmark's domain: `itsm` (IT service management), `csm` (customer
service) or `hr`, or a list such as `[itsm, csm, hr]`. It has no default and
must be set. Each domain has its own tasks and its own server, which must be
answering. More domains mean more tasks: a `run` makes one evaluation per
domain in turn, and `evolve` pools the tasks of every domain named into its
three sets, so the gate decides on more tasks and can find smaller gains,
at more cost.

### `mode`

Which tools the agent is offered for a task. `all_tools` offers every tool
the domain's server has, and is what every arm of an experiment is given.
`oracle` offers only the tools the benchmark lists with the task, the
setting published results use. `plus_5_tools`, `plus_10_tools` and
`plus_15_tools` add that many distractor tools to the oracle list. The mode
changes the task itself, so scores under different modes are not compared;
the reasons for `all_tools` are on
[What is fixed and what is optimised](methodology/fixed-and-optimised.md).

### `split`

A split of the tasks into training, validation and test sets, by the name
of its file in `configs/splits/`. A split is drawn once, with `mas-optimiser
split --name <name> --seed <seed>`, and another seed puts other tasks in
each set. For `run`, `split: <name>:<set>` runs one set and no split runs
every task. `evolve` needs the name alone and uses all three: the search
roles read results on the training tasks only, the gate decides on the
validation tasks, and the test tasks are run once at the end, on each
harness that was kept. One split is used throughout an experiment. The sizes
of the three sets are chosen when the split is drawn:
[Sizing an experiment](methodology/sizing.md) says what each size buys.

### `limit`

For `run`, only the first N tasks of the domain; for `evolve`, at most N
tasks of each set. Left out, every task is used. It is for trials: fewer
tasks cost less and show less, and a result on a handful of tasks decides
nothing.

### `task`

For `run` only: the tasks to run, by id, as one id or a list (`--task` on
the command line, repeated for several). It is for looking at single tasks,
such as one that failed.

## How it is run

### `attempts`

How many times each task is attempted. One attempt cannot tell a task the
agent always fails from one it fails half the time. More attempts average
that chance out: the noise of every score falls roughly with the square root
of the number of attempts, so the gate can find smaller gains and the final
gain has a narrower confidence interval, while every part of the cost that
runs the agent is multiplied by it. The trade between attempts and tasks is
worked through on [Sizing an experiment](methodology/sizing.md), and the
planner shows it for a design.

### `concurrency`

How many attempts are in flight at once. Each attempt has its own copy of
the database, so attempts never touch each other and the scores do not
depend on this setting. Higher finishes sooner, until the model service's
rate limit or the domain's server becomes the limit; past that point calls
are refused and sent again, and nothing is gained.

### `model_timeout`

The seconds one call to the model may wait for its answer. A call that runs
out is given up and sent again, so a connection that goes silent costs this
long and not the whole run. Real calls answer in seconds; a value too low
cuts off slow answers, which are then lost to the attempt. The limit is set
on the provider's own client for the providers `anthropic`, `openai`,
`azureopenai`, `vllm` and `openrouter`; with any other provider no limit is
set, and the run says so.

### `attempt_timeout`

The most seconds one attempt may take. It is kept by the runner and not by
the harness, so a change to the harness cannot lift it. An attempt that
passes it is ended. It may have spent its seconds waiting for a model
service that was down, so the service is asked first, as under
[`attempt_retries`](#attempt-retries); the attempt is recorded as an error
only when the service answers and no further try is left. An attempt of the
starting harness uses a small part of the limit; it exists for a candidate
that loops, which costs this many seconds for each try it is given.

### `attempt_calls`

The most calls to the model one attempt may make, kept by the runner like
`attempt_timeout`. An attempt that passes it is ended and recorded as an
error. Lower stops a runaway attempt sooner and may cut off a task that
needs many steps.

### `attempt_retries`

An attempt that ends in an error says nothing about the harness while a
service it needs is not answering. After such an error the runner asks the
service: the model service a one-word question, after a call that failed
for good or an attempt cut off at its seconds, and the domain's server for
its health, after any error. Once the service answers, the attempt is run
again, at most this many times. With 0 no attempt is run again. More gives
a harness that itself runs past its seconds more tries, each as long as
[`attempt_timeout`](#attempt-timeout).

### `service_wait`

The most seconds a service that is not answering is waited for; the model
service is asked about once a minute, each ask given a minute. An attempt
whose service never answered in that time is marked as the service's
failure, is left out of every score, and the command ends with an error
that says so; giving the command again carries on. A long wait rides out a
short outage, and a short one ends sooner when the service will not return,
as when a key's usage cap is reached. The roles that read and propose wait
the same way: a call of theirs that fails while their model service is
silent is tried again once it answers, and a service silent for this long
in all ends the experiment with a message that says so.

## What is recorded

### `trace`

Send each attempt to Langfuse as one trace, when its keys are in `.env`.
Tracing changes no score. It is useful for reading single attempts and can
be switched off for long experiments.

### `thinking_summary`

Ask the model for a summary of its reasoning on every call, shown in the
trace. It changes what the agent does and what it costs, so it is for
demonstrations only and never for an evaluation whose numbers are reported.

### `prompt_caching`

Have the provider store the input that repeats from call to call (the tool
descriptions, the policy, the conversation so far) and bill it at its
cheaper cache rate when it is sent again. The model receives the same input
either way; only the cost changes. This is Anthropic's prompt caching and is
built for Anthropic models only: with any other provider the setting does
nothing, and the run says so.

### `label`

A word added to the name of the results folder: letters, digits, `-` and
`_`. A `run` writes to `evaluations/<experiment>/<start time>-<label>`, and
`evolve` to `evolutions/<experiment>/<split>-<label>`. Use it to give a
variant of an experiment, such as one started with an option on the command
line, a folder of its own beside the original.

### `out`

For `run` only: the results folder, in place of
`evaluations/<experiment>/<start time>`.

## The improvement loop

These settings are read by `evolve` only. The method they belong to is on
[The improvement loop](methodology/improvement-loop.md), and the test that
decides is on [The acceptance gate](methodology/acceptance-gate.md).

### `rounds`

How many rounds the loop runs. In each round the search roles read the
training results, propose `candidates` changes, and each is tested against
the current harness. More rounds give more chances to find a change worth
keeping and cost more. They also make each decision stricter: every
decision takes a share of the experiment's error budget ([`alpha`](#alpha))
and later rounds get smaller shares, so the bar a candidate must clear
rises round by round. The formula is under "The level of each decision" on
[The acceptance gate](methodology/acceptance-gate.md).

### `candidates`

How many changes are proposed in each round. More candidates search wider
in a round. Each is run on the validation tasks, so the cost of a round
grows with them, and each decision's share of the error budget is divided
among them, so the bar is higher for every one.

### `alpha`

The chance, over the whole experiment, that at least one change is kept
that does nothing. It is a number above 0 and below 1. Higher keeps more
changes, more of them useless; lower keeps fewer and needs larger gains. It
is shared out over the decisions of the experiment, so the level of a single
decision is far below it. See
[The acceptance gate](methodology/acceptance-gate.md) for the test and its
level, and [Sizing an experiment](methodology/sizing.md) for the smallest
gain a design can find at a given `alpha`.

### `filter_share`

The filter is a first look at a candidate before the full test. The
candidate is run on this share of the validation tasks, and dropped there,
before the rest are run, unless its score is above the current harness's by
more than [`filter_threshold`](#filter-threshold). A candidate that passes
is run on the remaining tasks and the gate decides on all of them. The
filter saves the cost of the rest for candidates that do no better, and it
cannot make the gate less strict. A larger share spends more on each weak
candidate and drops fewer good ones by chance; 0 is no filter. See "The
filter before the full test" on
[The improvement loop](methodology/improvement-loop.md).

### `filter_threshold`

How far above the current harness a candidate's score must be on the
filter's tasks to go on, in percentage points. At 0 a candidate goes on
when it does better at all. Higher drops more candidates early, good ones
among them; below 0 lets through candidates that do slightly worse on the
filter's tasks.

### `pool_share`

The pool is the set of training tasks whose traces the analyst and the
proposer may read in a round. This setting is its share of the training
set: at least one task, and 1 for every task. Both roles are given every
training task's score whatever the share. A larger pool shows the proposer
more evidence and costs more reading in every round. See "What the search
roles read" on [The improvement loop](methodology/improvement-loop.md).

### `pool_pick`

How the pool is picked from the training tasks ranked by score. `spread`
takes tasks evenly over the ranking and moves one place further each round,
so that later rounds read other tasks. `ends` takes the lowest and the
highest, which shows the clearest failures and successes and leaves the
middle unread.

### `editable`

The types of file in the harness folder a proposal may edit. `.md` is the
agent's text and `.py` its code. With `.md` alone no code written by a
model is ever run. With `.py` the loop can change how the agent works, not
only what it is told, and the code it writes runs on the machine: see "What
a proposal can change" on
[The improvement loop](methodology/improvement-loop.md).

### `approval`

Whether a person approves each change before it is first run. With `each`,
every change is shown at the terminal and run only after a yes. With `none`
no one is asked; if `.py` is editable the command then refuses to start
without `--accept-unreviewed-code`, which says that running code no person
has read is accepted.

## What is set elsewhere

Three things an experiment names are set in files of their own.

| what      | where                        | holds                                                                      |
|-----------|------------------------------|----------------------------------------------------------------------------|
| a model   | `configs/models/<name>.yaml` | its provider, temperature, answer length, prices and the names of its keys |
| a split   | `configs/splits/<name>.json` | the seed, the shares of the three sets and the tasks of each               |
| a harness | `harnesses/<name>/`          | the agent's code and the text added to every task                          |

## What the results keep

`run` and `evolve` begin by printing every setting that is not at its
default, so a setting that was changed without being meant to shows on the
first lines:

```
Settings that differ from the defaults:
  model            haiku-5-5
  domain           ['itsm']
  limit            1
  trace            False
```

A proposer named as the agent's model is the default by another name and is
not listed.

Every result also says which settings made it. An evaluation's folder holds
`evaluation.json`: the experiment's name and file, the options given on the
command line, the settings not at their defaults, the model's values, the
prices, the tasks and the version of the code. An experiment of the loop
holds `experiment.json`: every setting as used, the options given on the
command line, the settings not at their defaults, the proposer's model file
as written and the version of the code. A model file names the variables
that hold a key or an address and never their values, so neither is stored.
