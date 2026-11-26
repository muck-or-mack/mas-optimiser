# Reading a decision

Every table the package writes about two harnesses answers one of three
questions. This page says which number answers which, in what unit, what
sets the bar a candidate must clear, and which numbers to look at first. It
is a guide to reading them. How they are derived, what the method supposes
and how it was checked are on
[The acceptance gate](methodology/acceptance-gate.md).

## Three questions and the number for each

| question                          | the number that answers it                                                            | unit              |
|-----------------------------------|---------------------------------------------------------------------------------------|-------------------|
| How good is a harness?            | its score: the mean over the tasks of the share of checks passed                      | percent           |
| How much better is the candidate? | the gain: the mean over the tasks of the candidate's score less the current harness's | percentage points |
| Is that gain more than chance?    | the gain set against the gain needed, the smallest gain the decision would have kept  | percentage points |

The first two are measured. The third is a decision, and every other number
on a report (the standard error, T, the bar, p) is a step towards it.

## From the gain to the decision

The example is the one worked through on the gate's page: one harness run
twice on 125 validation tasks, the second run standing as the candidate. The
right decision is known in advance, since nothing changed between the runs.

| step | number         | value        | what it says                                                           |
|------|----------------|--------------|------------------------------------------------------------------------|
| 1    | gain           | +0.71 points | the second run scored 0.71 points higher, on average over the tasks    |
| 2    | standard error | 1.36 points  | how far the gain would move by chance if the comparison were run again |
| 3    | T              | 0.52         | the gain counted in its own standard errors: 0.71 / 1.36               |
| 4    | bar            | 1.66         | how many standard errors the rule asks for                             |
| 5    | gain needed    | 2.3 points   | the bar in points: 1.66 × 1.36                                         |
| 6    | decision       | not kept     | 0.71 is below 2.3                                                      |

Steps 3 and 4 say on one scale what step 5 says on another: T is above the
bar exactly when the gain is above the gain needed. The gain needed is the
one to read, because it is in the unit of the gain itself. T has no unit of
score: it is the gain measured in standard errors.

The report gives one more number, p: 0.30 here. It is the chance that a
change which does nothing would show a gain at least this large. A candidate
is kept when p is below the level of its decision, 5% in this example, which
is again the same decision as steps 4 to 6.

## What sets the bar

Nothing in the results. The bar comes from one setting, `alpha`: the chance,
over a whole experiment, of keeping at least one change that does nothing
(0.05 unless set). An experiment decides many times on the same validation
tasks, so that chance is shared out among its decisions, and a decision's
share is its level. The rule that shares it out (REUSE, under "The level of
each decision" on the gate's page) gives a smaller share to each later
decision, and a smaller one again once a change has been kept. A smaller
level puts the bar higher.

That is with the setting `alpha_per` at `experiment`, its default. At
`decision`, `alpha` is the level of every decision by itself: the bar stays
at 1.65 standard errors for 161 tasks and 0.05, round after round, and what
is given up is the promise over the whole experiment (over five decisions,
up to a 23% chance that at least one useless change is kept). The table
below is for `experiment`.

| decision | changes kept before it | level   | bar  | gain needed | a real gain of 5 points is kept |
|----------|------------------------|---------|------|-------------|---------------------------------|
| 1        | 0                      | 1.25%   | 2.27 | 3.1 points  | 92 times in 100                 |
| 2        | 0                      | 0.4167% | 2.68 | 3.6 points  | 84 times in 100                 |
| 3        | 0                      | 0.2083% | 2.92 | 4.0 points  | 77 times in 100                 |
| 5        | 0                      | 0.0833% | 3.21 | 4.4 points  | 68 times in 100                 |
| 10       | 0                      | 0.0227% | 3.6  | 4.9 points  | 53 times in 100                 |
| 2        | 1                      | 0.1389% | 3.05 | 4.2 points  | 73 times in 100                 |
| 3        | 1                      | 0.0347% | 3.48 | 4.7 points  | 58 times in 100                 |
| 5        | 1                      | 0.0069% | 3.93 | 5.3 points  | 40 times in 100                 |
| 10       | 1                      | 0.0008% | 4.48 | 6.1 points  | 22 times in 100                 |

The table is computed, not measured: for 125 validation tasks, one candidate
a round and the example's standard error of 1.36 points, with
`stats.budget.reuse_level`, `stats.gate.bar` and `stats.gate.power`. Its
numbers are in `docs/data/levels.csv`.

The first decision needs a gain of 3.1 points and the tenth, after one
change has been kept, 6.1. The last column is what that strictness costs: a
change that truly gains 5 points is kept 92 times in 100 at the first
decision and 22 times in 100 at that tenth.

Two things therefore move the gain needed, and both are settings:

- **The bar**, through `alpha`, through the candidates compared in a round
  (`candidates`), and through how far into the experiment a decision comes:
  a longer experiment has later decisions, with higher bars.
- **The standard error**, through the number of validation tasks and the
  attempts at each (`attempts_gate`). On this benchmark most of the chance
  in a gain comes from one attempt differing from the next, so more
  attempts at the gate lower the gain needed at every decision
  ([Sizing an experiment](methodology/sizing.md)).

## Before an experiment: what could it find

The last column of the table is the power of a decision: the chance that a
real gain of a given size is kept. Power belongs to a design and is asked
before any result exists. The minimum detectable effect is the same thing
turned round: the true gain that is kept four times in five. The
[planner](planner.md) shows both for a design.

## A decision in two looks

With the setting `second_look`, a candidate that the gate turns down
narrowly is run for as many attempts again, with the current harness, and
tested once more. In the report's table of rounds such a candidate has 2
under "looks": its gain, standard error, T and p are the second look's, on
all the attempts, and the first look's gain and p stand beside them. Each
look is tested at half the decision's level, so the bar is higher than the
level alone would give: read the gain against the gain needed, as for any
other decision.

## What the numbers are not

- **p is not the chance that the change is good**, nor the chance that it
  does nothing. It is worked out supposing that it does nothing.
- **p is not the power.** p comes from one result. Power is the chance of
  keeping a real gain, asked before the result.
- **"Not kept" is not "no gain".** It means the gain was not shown to be
  more than chance at that decision's level. A candidate with a real gain
  below the gain needed is turned down more often than not.
- **A kept change's gain on the validation tasks is flattered.** It was
  kept because it came out high there. The gain to quote is the one on the
  test tasks, which took no part in any decision.

## What to look at first

In an experiment's report (`report/report.md` in its folder, which the experiment writes after each round and at its end):

1. **The harnesses kept.** The gain on the test tasks with its 95%
   confidence interval is the result of the experiment. The interval is
   made by a rule that catches the true gain 95 times in 100, so one that
   lies wholly above zero is evidence of a real gain, and its width says
   how well the gain is known.
2. **The rounds.** For each candidate, the gain on the validation tasks
   beside the gain its decision needed.
3. **Attempts that ended in an error**, before any figure is quoted: an
   attempt that ends in an error is scored zero.
4. **What each part cost.**

In a comparison of two evaluations (`comparison.md`, written by
`mas-optimiser compare`), "Result" gives the gain and the verdict, and "The
test in full" gives every number of this page with what it is.

## Where each number is

| number         | an experiment's `rounds.csv`        | a comparison's `tests.csv`          |
|----------------|-------------------------------------|-------------------------------------|
| gain           | `gain_points`                       | `gain_percentage_points`            |
| standard error | `standard_error_points`             | `standard_error_percentage_points`  |
| T              | `t`                                 | `t`                                 |
| bar            | `bar`                               | `bar`                               |
| gain needed    | `gain_needed_points`                | the bar times the standard error    |
| level          | `false_acceptances_allowed_percent` | `false_acceptances_allowed_percent` |
| p              | `p`                                 | `p`                                 |

A harness's scores and its gain on the test tasks, with the interval, are in
the experiment's `harnesses.csv`.
