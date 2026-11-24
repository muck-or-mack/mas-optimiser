# Sizing an experiment

How many tasks should each of the three sets hold, and how many attempts
should a task be given? This page works the answer backwards: from what an
experiment must be able to show, to the sizes that show it.

Its numbers come from the two baseline runs of
[The acceptance gate](acceptance-gate.md): one harness and one model, run
twice on the same 307 tasks with three attempts a task, the agent given
each task's own tools. Nothing changed between the two runs, so whatever
differs between them is chance. Another model has other numbers, and "Doing
this for another model" says how to get them.

## Two kinds of spread

Take two tasks from those runs. A score is the percentage of a task's
checks that an attempt passed.

| task          | first run, three attempts   | mean   | second run, three attempts   | mean   | difference   |
|---------------|-----------------------------|--------|------------------------------|--------|--------------|
| one task      | 75, 75, 50                  | 66.7   | 75, 100, 75                  | 83.3   | +16.7        |
| a harder task | 0, 0, 0                     | 0.0    | 0, 67, 0                     | 22.2   | +22.2        |

Two things vary in this table, and they are different in kind.

- **The spread between tasks.** The two tasks lie 61 to 67 points apart,
  whichever run is read, because one is harder than the other. Over the 307
  tasks the standard deviation of this spread is 32.6 points.
- **The spread between attempts.** One task, attempted again by the same
  harness, does not score the same. Over the 307 tasks its standard
  deviation is 16.1 points. It is uneven: 62% of the tasks scored the same
  on all three attempts, and the rest carry it.

Read down a column of means and the first kind is what shows. Read the last
column and it has gone: a task's difficulty is in both of its runs, so the
subtraction removes it, and the two differences are alike. What is left in
a difference is the spread between attempts.

## What each number is uncertain by

An experiment reports two kinds of number, and the two spreads enter them
differently. Write $N$ for the tasks, $K$ for the attempts a task is given,
$\sigma_\text{tasks}$ and $\sigma_\text{att}$ for the two spreads, and SE
for a standard error, how far a number moves by chance between repeats.

A harness's own score is a mean over tasks of a mean over attempts. Both
spreads are in it, and the attempts divide only the second:

$$\text{SE}^2 = \frac{\sigma_\text{tasks}^2 + \sigma_\text{att}^2 / K}{N}.$$

A difference between two harnesses on the same tasks, which is what the
gate tests and what the final gain is, has lost the spread between tasks.
In its place is $\sigma_\text{change}$, how unevenly the change helps from
task to task, and the spread between attempts counts twice, once for each
harness:

$$\text{SE}^2 = \frac{\sigma_\text{change}^2 + 2\,\sigma_\text{att}^2 / K}{N}.$$

Both follow from two rules: the variance of a mean of $n$ independent
values is the variance of one divided by $n$, and the variances of
independent parts add (Barlow, *Statistics*, Wiley, 1989, the chapter on
errors). Between the two baseline runs $\sigma_\text{change}$ is zero, and
the second formula can be checked against what was measured
(`docs/data/spread.csv`):

| attempts a task   | SD of a harness's task scores   | SD of a task's difference   | the same, from attempts alone   | SE of the harness's score, 123 tasks   | SE of the difference, 123 tasks   |
|-------------------|---------------------------------|-----------------------------|---------------------------------|----------------------------------------|-----------------------------------|
| 1                 | 36.5                            | 24.4                        | 22.8                            | 3.29                                   | 2.20                              |
| 2                 | 34.3                            | 15.8                        | 16.1                            | 3.09                                   | 1.42                              |
| 3                 | 33.9                            | 12.8                        | 13.2                            | 3.06                                   | 1.15                              |

"From attempts alone" is $\sqrt{2\,\sigma_\text{att}^2 / K}$, and it lies
within 7% of the measured column. Three things follow.

- **A harness's own score gains almost nothing from attempts.** Three
  attempts in place of one move its standard error from 3.29 to 3.06
  points, for three times the cost. Only more tasks make it more exact.
- **A difference gains as much from an attempt as from a task.** Its
  standard error nearly halves from one attempt to three. As long as nothing but
  the spread between attempts is in it, what counts is tasks times
  attempts.
- **A real change sets a floor that attempts cannot lower.** A change that
  helps some tasks and not others has a $\sigma_\text{change}$ above zero,
  and only tasks divide it. No candidate has yet been run on a full set, so
  its size is not known. Each decision's record holds the standard
  deviation of the differences (`sd` in `decisions.json`), from which it
  can be read after a first experiment.

There are 307 tasks and no more. Attempts are how precision is bought once
the tasks are used up, and they buy it for differences only.

## Working backwards

### Step 1. The test set, from the confidence interval wanted on the final gain

The final gain is the mean difference on the test tasks between the last
harness kept and the starting one. It is reported with its 95% confidence
interval, a range built so that over repeats of the experiment it holds the
true gain 95 times in 100:

$$\text{gain} \pm t_{0.975,\,N-1} \times \text{SE},$$

the gain plus or minus about 1.98 standard errors. With an interval of ±4
points, a measured gain of 9 points is reported as "9, with a 95%
confidence interval of 5 to 13". Choose how many points the ± may be, and
the table gives the test tasks that reach it (`docs/data/sizing.csv`):

| 95% confidence interval of the final gain, points   | tasks at 1 attempt   | at 2 attempts   | at 3 attempts   |
|-----------------------------------------------------|----------------------|-----------------|-----------------|
| ±2                                                  | 575                  | 243             | 159             |
| ±3                                                  | 257                  | 109             | 72              |
| ±4                                                  | 146                  | 63              | 42              |
| ±5                                                  | 94                   | 41              | 28              |
| ±6                                                  | 67                   | 30              | 20              |

The `compare` command reports the one-sided form of the same interval: a
lower confidence bound 1.66 standard errors below the gain, which says "at
least this much" with 95% confidence.

### Step 2. The validation set, from the smallest gain the gate must find

The gate keeps a change whose gain is too large to be chance at the level
of its decision. The smallest true gain it then finds four times in five is
the minimum detectable effect of [The acceptance gate](acceptance-gate.md).
Choose that gain for the first decision of an experiment, whose level is
1.25% when the experiment's budget is 5% and a round has one candidate:

| smallest gain found at the first decision, points   | tasks at 1 attempt   | at 2 attempts   | at 3 attempts   |
|-----------------------------------------------------|----------------------|-----------------|-----------------|
| 3                                                   | 632                  | 267             | 175             |
| 4                                                   | 357                  | 151             | 100             |
| 5                                                   | 230                  | 98              | 65              |
| 6                                                   | 160                  | 69              | 46              |
| 7                                                   | 119                  | 51              | 35              |

Later decisions have smaller levels, so the same tasks find only larger
gains. With 123 tasks and one attempt the smallest gain found is 6.9 points
at the first decision, 9.0 at the fifth and 9.8 at the tenth, if nothing
was accepted before; with three attempts, 3.6, 4.7 and 5.1.

### Step 3. The training set, from what is left

The training tasks are the rest. No formula sizes them, since their job is
to show the analyst and the proposer how the harness fails, and how many
tasks that takes has not been measured. Two facts bear on it: every
training task's score reaches both roles, and the traces of a share of them
are read each round ("What the search roles read" in
[The improvement loop](improvement-loop.md)).

### Step 4. The cost

Every task of a set is run more than once in an experiment, and how often
depends on the set. With $R$ rounds of which $a$ end in an accepted change:

| set        | runs of each task                                                       | in symbols              | with 10 rounds, 3 accepted   |
|------------|-------------------------------------------------------------------------|-------------------------|------------------------------|
| training   | once at the start, and once for each change kept                        | $1 + a$                 | 4                            |
| validation | the same, and once for each change not kept, less what the filter saves | $1 + a + 0.75\,(R - a)$ | 9.25                         |
| test       | once for each harness kept, the starting one included                   | $1 + a$                 | 4                            |

The 0.75 is the filter at its default share, for a change that does nothing
("The filter before the full test"). The 3 accepted is supposed and not
measured. The attempts of an experiment are these runs times the tasks of
each set times $K$.

### A worked example

Suppose the final gain is wanted with a confidence interval of ±4 points, and the
gate should find a gain of 5 points at its first decision. Steps 1 and 2
give the test and validation tasks, step 3 the rest of the 307, step 4 the
cost:

| attempts a task   | test tasks   | validation tasks   | training tasks   | attempts in the experiment   |
|-------------------|--------------|--------------------|------------------|------------------------------|
| 1                 | 146          | 230                | none left        | -                            |
| 2                 | 63           | 98                 | 146              | 3,485                        |
| 3                 | 42           | 65                 | 200              | 4,708                        |

- With one attempt a task the two targets need 376 tasks, and there are
  307. Tasks alone cannot meet them.
- With two attempts they are met with tasks to spare.
- The training set is not free. At two attempts its 146 tasks cost 1,168 of
  the 3,485 attempts. Tasks left over once the two targets are met can go
  to the validation set instead, where each lowers the smallest gain found,
  or be left out of the experiment.

For comparison, the default split of 20%, 40% and 40% at one attempt a task
is 61, 123 and 123 tasks and 1,874 attempts, with a confidence interval of
±4.4 points on the final gain and a smallest gain found of 6.9.

## What an experiment costs, part by part

Step 4 counts attempts. In tokens an experiment has two sides: the agent's
attempts, and the search roles, which read the training results and
propose. What one unit of each takes has been measured, with Claude Haiku
5.5 as the agent and as the search model:

| unit                                                    | input tokens   | measured on                            |
|---------------------------------------------------------|----------------|----------------------------------------|
| one attempt, the agent given every tool of its server   | 283,000        | 372 attempts, the run of 13 November   |
| one attempt, the agent given the task's own tools       | 74,000         | 1,840 attempts, the two baseline runs  |
| the analysis, for each trace read                       | 59,000         | twelve trial rounds of the loop        |
| the proposer and the reviewer, for one candidate        | 52,000         | the same rounds                        |

The two figures of the search roles are medians of rounds that differed
widely: a trace read took from 24,000 to 113,000 input tokens. With those,
the default experiment (61, 123 and 123 tasks, one attempt a
task, 10 rounds of which 3 end in a change kept, the filter and the pool at
their default shares, every tool given) comes to (`docs/data/cost.csv`):

| part                                         | how many   | input tokens each   | input tokens, millions   | share   |
|----------------------------------------------|------------|---------------------|--------------------------|---------|
| the starting harness on the training tasks   | 61         | 283,000             | 17.3                     | 3.1%    |
| the starting harness on the validation tasks | 123        | 283,000             | 34.8                     | 6.3%    |
| the one-task trial of each candidate         | 10         | 283,000             | 2.8                      | 0.5%    |
| the candidates on the validation tasks       | 1,015      | 283,000             | 287.2                    | 52.1%   |
| each harness kept, on the training tasks     | 183        | 283,000             | 51.8                     | 9.4%    |
| each harness kept, on the test tasks         | 492        | 283,000             | 139.2                    | 25.3%   |
| the analysis: traces read                    | 300        | 59,000              | 17.7                     | 3.2%    |
| the proposer and the reviewer: candidates    | 10         | 52,000              | 0.5                      | 0.1%    |
| all                                          |            |                     | 551.3                    | 100%    |

- **Half of it is the candidates' validation runs, and a quarter the test
  runs.** The settings that move the cost most are therefore the rounds,
  the validation tasks, the filter and the attempts.
- **The search roles are about 3%.** Reading more traces each round costs
  little beside one more attempt on every validation task.
- **The figures are input tokens.** An attempt's output is about 3,000
  tokens. Tokens are not money: a provider bills repeated input at a lower
  rate when prompt caching is on, and the search model may be another than
  the agent's.
- **Another model has other figures.** Ten attempts by GPT-5.1 on one
  domain, with every tool, took 115,000 input tokens each.

For an experiment that has run, the real figures are in its files: each
job's `tasks.csv` has the input tokens, output tokens and dollars of every
attempt, and `search_usage.jsonl` has a line for every call of the search
roles.

## Doing this for another model

1. Run the baseline twice with the same settings, as two runs of
   `mas-optimiser run <experiment>`.
2. Compare them with `mas-optimiser compare --experiment <experiment> --a
   <first run> --b <second run>`. The standard error it reports, times the
   square root of the number of tasks, is the standard deviation of a
   task's difference at the attempts those runs used.
3. For another number of attempts $K'$, multiply that standard deviation by
   $\sqrt{K / K'}$, and read the sizes as in steps 1 and 2: the ± of
   the confidence interval is 1.98 standard errors, and the smallest gain found at the first decision
   about 3.1.

## What the figures suppose

- **Nothing changed between the two runs.** The sizes are therefore the
  least that the targets need. A real change adds $\sigma_\text{change}$,
  which more tasks reduce and more attempts do not.
- **One model and one tool set.** The runs used the task's own tools; with
  every tool of the server the scores, and with them the spreads, differ.
- **All 307 tasks.** A set drawn from them has its own spread: the 125
  validation tasks of the gate page's split have a standard deviation of
  15.2 points for a task's difference at three attempts, against 12.8 here.
- **One setting for the attempts.** `attempts` applies to every run of an
  experiment, so the three sets cannot yet be given different numbers.
