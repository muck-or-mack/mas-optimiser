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
3. **The analysis.** A model reads the pages of the training attempts, the
   failed ones first, and writes down the ways the attempts went wrong.
4. **The proposal.** A second model, the proposer, works in a copy of the
   harness folder. It can list and read the folder's files, read the
   training attempts' pages, and edit the files. It must finish by naming
   each change it made and why. It is told what earlier proposals changed
   and whether each was accepted, and nothing else about them.
5. **Two checks before any scoring.** The change is reviewed for anything
   taken from a particular task and for any reference to the checks, and
   the changed harness is run on one training task to see that it still
   works and still writes its record in the same form.
6. **The score.** The changed harness is run on the validation tasks.
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

The proposer does not describe a change, it makes one. It works step by
step in a copy of the folder, with actions for files only: list the folder,
read a file, read a training attempt, replace one passage of a file with
another, create a file, and finish by naming each change and why. The
experiment's repository keeps every candidate as a commit on a branch of
its own, so the difference between a candidate and the current harness can
be read exactly, with `git diff`.

In a trial of the loop the proposer's whole change was one paragraph added
to `prompt.md`, which began: "Before you finish a request, check every field
that the request or a policy table determines."

### Text and code

| `editable`       | a proposal may change         | what that means                                                 |
|------------------|-------------------------------|-----------------------------------------------------------------|
| `[".md"]`        | the text the agent is given   | nothing new runs: the model reads other words                   |
| `[".md", ".py"]` | the text and the agent's code | code written by a model runs on the machine that runs the tasks |

### What stands between a change and its first run

| check, in order                 | what it is                                        | what it stops                                                                                                                                                                                                                                                 |
|---------------------------------|---------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| the screen                      | a fixed list of patterns                          | a task's id or a record number; a reference to the checks; code that reads the environment, imports a module that reaches files, other programs or the network, runs text as code, reaches files outside the folder, or calls the model by a route of its own |
| the reviewer                    | a model that reads the change against the rules   | anything taken from one task, anything aimed at the checks, and code that reaches outside the folder or the clients it is given                                                                                                                               |
| a person, with `approval: each` | the change shown at the terminal, and a yes or no | whatever the person declines; a no ends the candidate and is recorded                                                                                                                                                                                         |
| the one-task run                | the changed harness run on one training task      | a harness that no longer runs, or no longer writes its record in the form the package reads                                                                                                                                                                   |

A change stopped by the screen or the reviewer goes back to the proposer
with the reasons.

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
recorded as an error. A change that makes the agent loop without end
therefore costs one attempt's limit and not the experiment.

A person's no is a decision made outside the gate. An experiment run with
`approval: each` is the loop with a person in it, and is reported as such.

## Who may read what

| what                                             | the analyst and the proposer   | the gate   | a person, while the experiment runs   |
|--------------------------------------------------|--------------------------------|------------|---------------------------------------|
| the training attempts, in full                   | yes                            | no         | yes                                   |
| the validation tasks and attempts                | no                             | scores     | the headline only                     |
| whether an earlier proposal was accepted         | yes                            | made it    | yes                                   |
| by how much a proposal changed the score or cost | no                             | yes        | yes, in the record                    |
| the test tasks and attempts                      | no                             | no         | the headline only, at the end         |

The proposer learns one thing from the validation tasks, the outcome of
each decision. The unit tests check it: the traces handed to the proposer
are of training tasks, and what it is told of an earlier proposal holds no
number (`tests/unit/test_evolve.py`).

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

A single test at 5% has a bar of 1.66 standard errors. `stats.budget` also
holds an equal split over a fixed number of decisions; the loop does not yet
offer it as a setting.

## The settings

An experiment file holds the settings of `mas-optimiser run` (the model,
the domains, the attempts and so on) and these, which only `evolve` reads:

| setting          | what it sets                                                                 | default           |
|------------------|------------------------------------------------------------------------------|-------------------|
| `split`          | the split whose three sets are used, by its name alone                       | none, must be set |
| `harness`        | the folder the experiment starts from                                        | `default`         |
| `proposer_model` | the model file of the analyst, the proposer and the reviewer                 | the agent's model |
| `rounds`         | rounds of the loop                                                           | 10                |
| `candidates`     | candidates proposed in each round                                            | 1                 |
| `alpha`          | the error budget of the whole experiment                                     | 0.05              |
| `editable`       | the types of file in the harness folder a proposal may edit                  | `.md` and `.py`   |
| `approval`       | whether a person approves each change before it first runs: `each` or `none` | `none`            |
| `limit`          | at most this many tasks of each set, for a trial of the loop itself          | all               |

The agent's model and the proposer's are two settings, so they can be the
same model or different ones, hosted or self-hosted. `editable` and
`approval` are described under "What a proposal can change".
`configs/experiments/evolve-smoke.yaml` is a trial with three tasks of each
set and one round.

## What an experiment writes

```
evolutions/<experiment>/<split>/
  experiment.json             every setting and the code's version
  search_usage.jsonl          one line per call of the analyst, proposer and reviewer
  repository/                 the experiment's git repository
    harness/                  the current harness
  enterpriseops/
    frontier.json             the current harness and the score after each round
    history.jsonl             every proposal: what it changed, its outcome, its numbers
    test.csv                  one row per harness kept: its validation and test scores
    jobs/<job>/<domain>/      one evaluation folder per run of a harness
    r<round>/
      analysis_report.json    what the analyst found in the training traces
      decisions.json          the gate's numbers for each candidate
      <candidate>/            the proposal, its diff, its review, its trial run
```

Each job folder is an evaluation as described in
[Reading the results](../results.md); the summary of a job on validation or
test tasks gives the headline only. Giving the command again carries the
experiment on from the last step that left its result.

## What is taken from RRSI and what is replaced

The loop is built on the released code of RRSI (Xia et al.,
arXiv:2609.24972; Apache License 2.0), kept unchanged in `vendor/rrsi`.

| part                                                           | whose                                   |
|----------------------------------------------------------------|-----------------------------------------|
| the analysis of traces, the proposer, the review of a proposal | RRSI's, as released                     |
| the record of proposals, a git checkout per candidate          | RRSI's, as released                     |
| what a benchmark must provide (its `Domain` interface)         | RRSI's; `domain.EnterpriseOps` fills it |
| the round and the baseline                                     | this package's (`evolve.loop`)          |
| the rule that keeps a change                                   | this package's: the gate                |
| the model call of the search roles                             | this package's (`evolve.models`)        |
| what the search roles are told about the benchmark             | this package's (`prompts`)              |

The round is replaced because RRSI reads its traces and judges its
candidates on one and the same set of tasks, and controls the resulting
over-fitting by limiting what a proposal may be. Here the two jobs have two
sets, and a proposal is judged on tasks its author never saw. RRSI's rule
keeps a candidate whose score is within a noise band of the best so far;
the gate asks for a gain that a test at a stated error level supports.

## Not built yet

- A rule on cost. The record holds every candidate's change in tokens, and
  no decision uses it.
- A repeat of single attempts. A job with an attempt that ended in an error
  is run again as a whole.
- A figure of the test score against the round.
- A second benchmark. The loop holds nothing of this benchmark's; another
  one needs a class like `domain.EnterpriseOps`.

The loop has been run from end to end in a trial of three tasks a set. No
full experiment has been run with it.
