# The acceptance gate

The optimiser proposes a changed harness, the candidate, and must decide
whether it is better than the harness it has, the current one (the papers call it the incumbent). The decision is
made on the validation tasks and is a statistical test: the candidate's gain
has to be larger than the gain that chance alone produces between two runs of
one and the same harness. This page goes through one comparison by hand,
states the method's formulas and what each supposes, shows how each
supposition was checked on this benchmark and why the formula is what
decides, and then gives the rules for a run of many decisions (how many false
acceptances each allows, its level) and the results on the two baseline runs
of the same harness, where any acceptance is a false one. The code is
`mas_optimiser.stats`; the command `mas-optimiser compare` produces every
table and figure below.

## From attempts to a decision: a worked example

The rest of this page is formal. This section goes through the same steps in
plain arithmetic, on the two baseline runs of 27 October and 3 November (the
comparison in `docs/figures/gate/`). Both runs are the same harness, so the
right answer is known in advance: the second run is no better than the first.

First the words. There are two harnesses, the *current* one and the
*candidate*. There are also two hypotheses, and they are not the harnesses:
they are the two possible answers to one question about them.

- $H_0$: the candidate is no better. Its true gain is zero.
- $H_1$: the candidate is better. Its true gain is above zero.

### From the results to one number

The task in the table is a real one, one of the 125 validation tasks of the
two runs. It was picked because its attempts differ within both runs and its
difference is not zero, so that every step of the arithmetic shows, and it
is not a typical task: 66 of the 125 have a difference of exactly zero. It
is not named, and the comparison's per-task file, `differences.csv`, is not
published: the optimiser must not learn from the tasks its changes are
judged on. With the two runs at hand, the task is the first in that file to
meet the two conditions. The first run stands as the current harness and the
second as the candidate.

1. Score each attempt: the share of its checks that passed (`checks_passed`
   out of `checks_total`, in the run's `tasks.csv`). This task has five
   checks.
2. Average the attempts of each task, for each harness.

   |                        | attempt 1   | attempt 2   | attempt 3   | task's score   |
   |------------------------|-------------|-------------|-------------|----------------|
   | current (27 October)   | 3 of 5, 60% | 2 of 5, 40% | 3 of 5, 60% | 53.3%          |
   | candidate (3 November) | 1 of 5, 20% | 2 of 5, 40% | 2 of 5, 40% | 33.3%          |

3. Take the difference for each task, candidate minus current: here
   $33.3 - 53.3 = -20.0$ percentage points, though the harness is the same
   in both runs. Every task gives one difference, 125 in all (the rows of
   `differences.csv`).
4. Average the differences. That is the gain: +0.71 percentage points.
5. Measure how far that average moves by chance. That is the standard error:
   the spread of the differences divided by the square root of their number,
   $15.2 / 11.18 = 1.36$ percentage points.
6. Divide the gain by its standard error. That is $T$: $0.71 / 1.36 = 0.52$.

The data gives one number. $T$ is measured in standard errors: here the gain
is about half of its own noise.

### The curve

The curve in the figures below is not drawn from these results. It answers a
question: suppose $H_0$ were true; if the whole comparison were repeated again
and again, what values of $T$ would chance alone give? Theory answers that
they would scatter around zero in a bell shape about one unit wide, between
$-1$ and $+1$ two times in three and above 1.66 one time in twenty. Three
things follow.

- The curve is centred on zero, not on the $T$ observed, because it describes
  what would happen if there were no gain. The $T$ observed is one point
  placed on it.
- It is the same curve for every comparison of 125 tasks, whatever the
  harnesses, because $T$ is already divided by that comparison's own noise.
  What differs from one comparison to the next is where its $T$ falls.
- It is theory. "How each supposition was checked", below, tests it against
  real attempts.

### The decision

Place the $T$ observed on the curve and ask how often a $T$ at least as large
would come up if $H_0$ were true. That chance is $p$: 0.30 here, so a gain
of this size turns up by chance three times in ten. The rule, fixed before
the comparison, is to accept the candidate when that chance is below 5%,
which is when $T$ lies beyond the bar at 1.66. Here 0.52 falls short of it,
so $H_0$ stands and the candidate is not accepted: the right answer for two
runs of one harness. With this much noise a candidate would have needed a
gain above $1.66 \times 1.36 = 2.3$ percentage points.

$H_1$ needs no curve of its own for the decision: to reject $H_0$ is to
accept $H_1$. A second curve appears in the figures only to answer another
question, how often a real gain of a given size would be caught. It is the
first curve moved to the right by that gain divided by the standard error.

## Symbols

The page writes its quantities as symbols; the reports and data files of
`compare` name them in words. Each symbol is defined again where it first
appears.

| symbol       | in the reports            | meaning                                            | column of `tests.csv`                         |
|--------------|---------------------------|----------------------------------------------------|-----------------------------------------------|
| $N$          | tasks                     | the tasks both sides were run on                   | `tasks`                                       |
| $d_i$        | a task's difference       | task $i$'s score: candidate minus current          | in `differences.csv`                          |
| $\bar d$     | gain                      | the mean of the differences: the estimated gain    | `gain_percentage_points`                      |
| $s_d$        |                           | the standard deviation of the differences          |                                               |
| $\text{SE}$  | standard error            | how far the gain moves by chance between repeats   | `standard_error_percentage_points`            |
| $T$          | T                         | the gain in units of its standard error            | `t`                                           |
| $X_0$        | bar on T                  | the value $T$ must exceed for acceptance           | `bar`                                         |
| $\alpha$     | false acceptances allowed | the level of one decision                          | `false_acceptances_allowed_percent`           |
| $p$          | p                         | the chance of a $T$ this large from no change      | `p`                                           |
| $g$          | true gain                 | the gain the candidate really has                  |                                               |
| $\beta$      |                           | the chance of missing a real gain                  |                                               |
| $1 - \beta$  | chance of acceptance      | the power: the chance a real gain is accepted      |                                               |
| $g_{\min}$   | minimum detectable effect | the smallest true gain accepted four times in five | `minimum_detectable_effect_percentage_points` |
| $H_0$, $H_1$ |                           | the hypotheses: no gain; a gain                    |                                               |

The formula of the REUSE allocation, in "The level of each decision", keeps
the symbols of its paper, which are explained beside it.

## The method and its formulas

### Scores and differences

A *task* is one request to the agent, judged by several *checks*, each a row
the database must hold afterwards. An *attempt* is one run of the agent on
one task; each task is attempted several times per harness (three in the
baselines). Two outcomes score an attempt:

- *checks passed*: the share of the attempt's checks that passed;
- *attempts fully passed*: 1 if every check passed, else 0.

A task's score under a harness is the mean of its attempts' outcomes, written
in the reports as a percentage. With attempts fully passed and three attempts
it is one of 0, 33, 67 or 100%; with checks passed it takes many values. For
a candidate $B$ and the current harness $A$ run on the same $N$ tasks, the
per-task difference is

$$d_i = \text{score}_i(B) - \text{score}_i(A), \qquad i = 1, \dots, N,$$

and the estimated gain is $\bar d$, the mean of the $d_i$, in percentage
points, a difference of two percentages. Pairing on the
task removes the task's own difficulty from the comparison, so the sides must
have scored the same tasks (`stats.outcomes.paired`).

The gate uses checks passed, declared as the primary outcome before any
comparison was run, and reports attempts fully passed beside it. Checks
passed has the smaller minimum detectable effect (below), at the price of
meaning: partial
credit can rise without more tasks being finished, which is why the coarser
outcome is always shown.

### The three formulas

The null hypothesis $H_0$ is that the candidate's true mean gain is at most
zero; the alternative $H_1$ is that it is positive. Three formulas take the
differences to a decision. Together they are the one-sided paired $t$-test
(Barlow, *Statistics*, Wiley, 1989, the chapters on errors and on hypothesis
testing), computed by `stats.gate.mean_test`, which calls
`scipy.stats.ttest_1samp` on the differences.

The first is the standard error,

$$\text{SE} = \frac{s_d}{\sqrt{N}},$$

with $s_d$ the standard deviation of the $N$ differences (computed with
$N - 1$ in the denominator, as for any sample). It says how far the gain
$\bar d$ would move by chance if the comparison were repeated. It is worked
out afresh in every comparison, from that comparison's own differences: no
noise figure is carried over from other runs. It supposes
that the tasks' differences are independent: what chance does to one task
says nothing about another.

The second is the statistic,

$$T = \frac{\bar d}{\text{SE}},$$

the gain in units of its standard error. It supposes nothing further.

The third is the curve of $T$ when nothing has changed. Under $H_0$, $T$
follows Student's $t$ distribution with $N - 1$ degrees of freedom (the
number of differences less one, which sets the curve's width), a little wider
than the standard normal. The gate keeps the candidate when

$$T > X_0 = F_{N-1}^{-1}(1 - \alpha),$$

where $F_{N-1}(x)$ is the share of Student's $t$ curve that lies below $x$,
$F_{N-1}^{-1}(q)$ is the point below which a share $q$ of it lies, and
$\alpha = P(T > X_0 \mid H_0)$ is the Type-I error allowed for the decision,
the chance of accepting a candidate that is no better. $\alpha$ is the
decision's *level*; the reports call it the false acceptances allowed, and
call $X_0$ the bar on $T$. At $N = 125$ and $\alpha = 0.05$, $X_0 = 1.657$;
the same point of the normal curve is 1.645. With $t_{\text{obs}}$ the value
of $T$ that a comparison gave, the p-value is
$P(T \ge t_{\text{obs}} \mid H_0) = 1 - F_{N-1}(t_{\text{obs}})$, the
chance that a change that does nothing gives a $T$ at least that large, and
$\bar d - X_0\,\text{SE}$ is a one-sided lower confidence bound on the true
gain at confidence $1 - \alpha$. This third formula supposes that the mean of
the $N$ differences is close to normally distributed. The central limit
theorem promises that for the mean of many bounded differences, and it is
exact only as $N$ grows.

### Where the curve comes from

The central limit theorem states that the mean of many independent values,
none of which dominates the rest, is close to normally distributed whatever
the distribution of the single values (Barlow, 1989, the chapter on errors).
The single values here are the per-task differences $d_i$, which are far
from normal: most are exactly zero and the rest are small fractions. Their
mean $\bar d$ averages 125 of them, so it is close to normal, with standard
deviation $\text{SE}$. Dividing by the true $\text{SE}$ would give a
variable with standard deviation one, centred on $g/\text{SE}$ when the true
gain is $g$, whose curve for no gain is the standard normal density at a
value $t$,

$$\varphi(t) = \frac{1}{\sqrt{2\pi}}\, e^{-t^2/2}.$$

The test divides by the estimated $\text{SE}$, which adds a little spread:
that is Student's $t$ distribution, whose density approaches $\varphi$ as
$N$ grows and at $N = 125$ has standard deviation 1.008. With a true gain it
becomes the noncentral $t$.

The figure under "Missing a real gain" draws those two densities, for no gain
and for a gain equal to the minimum detectable effect. At $\alpha = 0.05$ and
a given $N$ the second curve sits at the same place whatever the data (2.50
at $N = 125$), so the picture is the same for every comparison of that many
tasks; only the size of the minimum detectable effect in percentage points
differs. The data itself is the figure of per-task differences further down.
The theorem is an approximation for a finite number of tasks, which is why it
is checked on this benchmark's own data in the sections that follow.

## What the method supposes

| supposition                                     | where it enters                   | why it could fail on this benchmark                                                     |
|-------------------------------------------------|-----------------------------------|-----------------------------------------------------------------------------------------|
| The tasks' differences are independent          | the standard error                | one run's attempts share a server and a day, so one fault could move many tasks at once |
| The mean of 125 differences is close to normal  | the curve, and so $p$ and the bar | the differences are uneven: between the baseline runs 66 of 125 are exactly zero        |
| The spread of the differences is estimated well | the standard error                | a few large differences among many zeros carry most of it                               |
| The numbers are computed as stated              | everything                        | an error in the code                                                                    |

The last three are checked in the next section. The first is not, and is
taken up under "What is not checked".

## How each supposition was checked

Each check redoes one part of the method without the formula it tests. All
of them are run on the two baseline runs, the same harness twice, where the
truth is known: nothing changed. The first two are also computed by `compare`
for every comparison and printed in its report beside the formula's numbers
(the columns named below, in `tests.csv`).

### The standard error, by bootstrap

1. Draw 125 tasks at random from the 125, allowing a task to be drawn more
   than once.
2. Work out the gain on that draw.
3. Repeat 9,999 times.
4. The spread of those gains is the standard error, found without its
   formula.

| outcome               | formula   | bootstrap   |
|-----------------------|-----------|-------------|
| checks passed         | 1.36      | 1.37        |
| attempts fully passed | 1.96      | 1.97        |

Both columns are in percentage points (`standard_error_percentage_points` and
`bootstrap_standard_error_percentage_points`). The method is that of Efron and
Tibshirani, *An Introduction to the Bootstrap*, Chapman and Hall, 1993, the
chapter on the bootstrap standard error; the code is SciPy's `bootstrap`,
called by `stats.compare`.

### The p-value, by permutation

1. If nothing changed, which harness is called the candidate is arbitrary on
   every task. So give each task's difference a random sign, keeping its
   size.
2. Work out the gain of the re-signed differences.
3. Repeat 9,999 times.
4. The p-value is the share of those gains at or above the gain observed,
   found without the curve.

| outcome               | formula   | permutation   | sign test              |
|-----------------------|-----------|---------------|------------------------|
| checks passed         | 0.300     | 0.300         | 0.301 (32 up, 27 down) |
| attempts fully passed | 0.139     | 0.169         | 0.292 (17 up, 13 down) |

For checks passed the three agree. A share of 0.30 seen in 9,999 draws is
itself uncertain by about 0.005. For attempts fully passed, whose differences
take only five values, the permutation's p-value sits above the formula's:
about 6% of the relabellings tie with the observed mean, and ties count
towards it. The method is that of Efron and Tibshirani, 1993, the chapter on
permutation tests; the code is SciPy's `permutation_test` (`permutation_p`).

The last column is a third look, which uses only the direction of each
difference. The *exact sign test* takes the tasks whose score changed: under
$H_0$ each is a fair coin, so its p-value is
$P(\text{Bin}(M, 1/2) \ge n_+)$, the chance of at least $n_+$ heads in $M$
tosses, with $M$ the changed tasks and $n_+$ the candidate's wins (Sun et
al., 2026, Lemma A2; `stats.gate.sign_test`, `sign_test_p`).

### The test as a whole, by calibration

The first two checks look at one comparison. The third asks the question the
bars rest on: when nothing has changed, does $T$ really follow Student's
$t$? The two baseline runs allow a direct answer (`mas-optimiser calibrate`,
`stats.calibrate`).

1. Each of the 125 validation tasks has six attempts of the same harness,
   three from each run.
2. Deal each task's six attempts at random into two halves of three.
3. Compare the two halves as the gate compares a candidate with the current
   harness. That is one $T$ from a comparison in which nothing changed.
4. Repeat 200,000 times (SciPy's `permutation_test`, exchanging each task's
   attempts between the six attempt columns).

:::{figure} ../figures/calibration/figures/scatter.png
:alt: Each task's score in the first run against its score in the second

Each task's checks passed in the first baseline run against the second; a
larger dot is more tasks. On the diagonal a task scored the same both times.
The spread around it is the noise between two runs of one harness, which
any gain has to stand out from.
:::

Every acceptance among those comparisons is a false one, so the share
accepted at each bar can be set against the share the rule allows
(`docs/figures/calibration/` holds the command's report and its table):

| Quantity                                                    | Measured       | Expected   |
|-------------------------------------------------------------|----------------|------------|
| Mean of $T$                                                 | −0.002 ± 0.002 | 0          |
| Width of $T$                                                | 1.009 ± 0.002  | 1.008      |
| Splits accepted at one decision at 5%                       | 4.97 ± 0.05%   | 5%         |
| Splits accepted at 5% shared over a run (REUSE), decision 1 | 1.20 ± 0.02%   | 1.25%      |
| Splits accepted at 5% shared equally over 20 decisions      | 0.21 ± 0.01%   | 0.25%      |
| Splits accepted at 3 sigma                                  | 0.11 ± 0.01%   | 0.135%     |
| Splits accepted by any gain                                 | 50.0 ± 0.1%    |            |

The mean and the width are those of Student's $t$, whose standard deviation
at 124 degrees of freedom is 1.008 where a normal curve has 1, and at 5% the
test accepts the share it should. At the stricter bars it accepts slightly
fewer no-change splits than the rule allows, 1.20% against 1.25%: the tails
of $T$ are a little thinner than those of Student's $t$, most likely because
the scores are bounded, and the error is on the safe side. A rule that keeps
any gain accepts half of the no-change splits.

:::{figure} ../figures/calibration/figures/calibration.png
:alt: The statistic of 200,000 no-change splits against Student's t, with the pull of each bin

Top: the $T$ of every random split (points, with their counting errors)
against Student's $t$ (the line), on a logarithmic scale so that the tails
show; the short marks are the bars of the decision rules and the long line
is the $T$ of the two runs as they were run. Bottom: the pull of each bin,
its count minus the expected one in units of the expected count's error.
:::

### The arithmetic

- The test, the permutation and the bootstrap are SciPy's own functions
  (`scipy.stats.ttest_1samp`, `permutation_test`, `bootstrap`), not code of
  this package.
- The package's own steps (a task's score, the pairing of the two sides, the
  test's numbers, the bar, the sign test, the minimum detectable effect) are
  tested against values from tables and values worked by hand
  (`tests/unit/test_stats.py`).
- `mas-optimiser compare` run again on the two baseline runs reproduces the
  data files of `docs/figures/gate/` exactly.

## Why the formula decides and the resampling checks

The permutation gives a p-value without the curve, so it could have been the
method. The formula decides, for three reasons.

- It gives the same answer every time. A resampled p-value is a share seen
  in a number of random draws, and changes with the draws.
- The strict rules sit far out in the tail, where a share is hard to measure
  by counting. A share $q$ seen in $R$ draws is uncertain by
  $\sqrt{q(1 - q)/R}$, which as a fraction of $q$ is:

  | level of the rule   | 9,999 draws   | 200,000 draws   |
  |---------------------|---------------|-----------------|
  | 5%                  | 4%            | 1%              |
  | 1.25%               | 9%            | 2%              |
  | 0.25%               | 20%           | 4%              |
  | 0.135%              | 27%           | 6%              |

  At 9,999 draws a rule that allows 0.135% cannot be told from one that
  allows 0.10% or 0.17%.
- The minimum detectable effect and the chance of catching a real gain
  (below) come from the curve. Resampling the data at hand says nothing
  about a gain that is not in it.

Time is not a reason. For 125 differences the formula takes under a
millisecond and 10,000 relabellings about a tenth of a second, both nothing
beside the runs being compared.

So the formula decides, the bootstrap and the permutation are computed in
every comparison and printed beside it, and the calibration is repeated for
a new model or benchmark. A gap between the formula and a check that is
larger than the check's own uncertainty would mean that the curve does not
hold for the data at hand.

## What is not checked

- That the tasks' differences are independent. The bootstrap and the
  permutation both treat the tasks as separate draws, as the formula does.
- How often a real gain is found. The checks measure false acceptance; the
  minimum detectable effect comes from the curve alone, and no candidate
  with a gain of known size has been put through the gate.
- Differences between days. The calibration treats a task's six attempts as
  interchangeable, so any difference between the days of the two runs counts
  as noise.
- A run of many decisions. The checks cover one decision at a time, not what
  a loop adds by reusing one run of the current harness across many
  candidates.
- Other models and benchmarks. Every number here is for one model on this
  benchmark, and the calibration's uncertainties are those of the random
  splitting alone: every split draws on the same 750 attempts, so another
  pair of runs would give slightly different numbers.

## Missing a real gain: power and the minimum detectable effect

The second error is missing a real gain: $\beta = P(T < X_0 \mid H_1)$, and
$1 - \beta$ is the *power*, the chance that a real gain is accepted. The bar
$X_0$ trades the two errors against each other: a higher bar accepts fewer
changes that do nothing and misses more real gains.

With the bar where a rule has put it, how large must a real gain be to be
caught? Three steps, on the two baseline runs (standard error 1.36
percentage points, bar 1.66):

1. The standard error is the noise: how far the measured gain moves by
   chance when the comparison is repeated. It belongs to the data and is the
   same under every decision rule.
2. A gain is accepted when it exceeds the bar times the standard error,
   $1.66 \times 1.36 = 2.3$ percentage points.
3. The measured gain is the true gain plus noise, and falls above and below
   the true gain equally often, so a true gain of exactly 2.3 is accepted
   only half the time. To be accepted four times in five the true gain must
   lie higher, by the distance that leaves 80% of its bell curve beyond the
   bar: 0.84 standard errors.

$$g_{\min} = (X_0 + 0.84)\,\text{SE} = 2.50\,\text{SE} = 3.4
\text{ percentage points} \quad \text{at } \alpha = 0.05,\ N = 125.$$

This is the *minimum detectable effect*, the term of the statistics
literature: the smallest true gain a rule accepts four times in five. The
bar does not move in these steps; the true gain does. The 0.84 is the
convention of a power of 0.8, and a power of 0.9 would put 1.28 in its
place. Exactly, with a true gain $g$ the statistic follows the noncentral $t$
distribution with $N - 1$ degrees of freedom and noncentrality
$g / \text{SE}$ (where the curve is centred, in standard errors), the power
at $g$ is the part of that distribution beyond
$X_0$ (`stats.gate.power`), and $g_{\min}$ is found numerically
(`stats.gate.detectable`; `minimum_detectable_effect_percentage_points` in
`tests.csv`). The normal approximation,
$(z_{1-\alpha} + z_{0.8})\,\text{SE} = 2.49\,\text{SE}$, with $z_q$ the
point below which a share $q$ of the standard normal curve lies
($z_{0.95} = 1.645$, $z_{0.8} = 0.84$), is within one percent of it at this
$N$.

A real gain below the minimum detectable effect is missed more often than one
time in five. The standard error does not depend on the rule. The minimum
detectable effect does, through the bar: a stricter rule has a higher bar
and so a larger minimum detectable effect, and a rule with no level has none
and accepts noise.

:::{figure} ../figures/calibration/figures/distributions.png
:alt: Two curves of the statistic, with the bar between them

What the test assumes: the statistic $T$ when nothing changed (solid curve)
and when the true gain equals the minimum detectable effect (dashed curve),
at 125 tasks.
Both are theory, not data: Student's $t$ and its noncentral form. The
vertical line is the bar $X_0$: the shaded tail of the first curve beyond it
is $\alpha$, the false acceptances allowed, and the shaded part of the
second below it is $\beta$, the real gains missed.
:::

:::{figure} ../figures/gate/figures/detectable.png
:alt: The chance of acceptance against the true gain, one curve per decision rule

The chance that a real gain of each size would be accepted, its power, under
each decision rule of the next section, at the standard error of the two
baseline runs: theory, from `stats.gate.power`. Each curve starts at the
rule's level for no gain and crosses 80% at that rule's minimum detectable
effect, marked.
The vertical line is the gain observed between the two runs.
:::

## The level of each decision

One decision at $\alpha = 0.05$ is one thing; a run that decides twenty times
on the same tasks is another. If each decision had a 5% chance of a false
acceptance, the chance of at least one in twenty would be up to
$20 \times 5\%$ and is $1 - 0.95^{20} = 64\%$ when the decisions are
independent, by the rule that the probability of "A or B" is at most the sum
of the two probabilities. Later candidates also depend on which earlier ones
were accepted, so the tests are not fixed in advance. `stats.budget` offers
three ways to set each decision's level, shown side by side by `compare`:

- **REUSE allocation** (Sun et al., *Which Self-Improvements Should We
  Trust?*, arXiv:2609.33180, 2026, Eq. 4), the gate's rule. The formula is
  written as in the paper, and inside it four symbols are the paper's, not
  this page's: $\alpha$ is the level of a whole run (0.05 here), not of one
  decision; $t$ numbers the decision, 1 for the first; $p$ counts the
  candidates promoted so far; and $K$ is the number of candidates compared
  in the round. The level of that one decision, the $\alpha$ of the test
  above, is

  $$\delta_{t,p} = \frac{\alpha\, w_t\, v_p}{\binom{t-1}{p} K^{p+1}},
  \qquad w_t = \frac{1}{t(t+1)}, \quad v_p = \frac{1}{(p+1)(p+2)}.$$

  For the first decision of a run with one candidate ($t = 1$, $p = 0$,
  $K = 1$) this is $0.05 \times \tfrac{1}{2} \times \tfrac{1}{2} = 0.0125$,
  the 1.25% of the reports.
  The weights sum to one over rounds and over promotion counts, and the
  binomial coefficient and the power of $K$ cover every history of
  promotions, so the levels of all decisions a run could make sum to at most
  $\alpha$: with probability at least $1 - \alpha$ every promotion in the run
  is a true improvement, however the proposer adapts. Later rounds and more
  promotions get smaller levels, so stronger evidence is demanded as the run
  goes on.
- **Equal split** (Bonferroni's inequality): the level of the whole run
  divided by a number of decisions fixed before the run. Valid, simple, and
  blind to how the run unfolds.
- **Fixed significance**: the same threshold in standard deviations at every
  decision, three for evidence and five for discovery by the conventions of
  particle physics ($\alpha = 0.00135$ and $2.9 \times 10^{-7}$ one-sided).
  The level is what is fixed: with Student's $t$ at $N = 125$ the three-sigma
  level puts the threshold at 3.06. It controls each decision, not the run.

The test statistic under the REUSE allocation is the $T$ above.
The paper proves its guarantee for its own exact sign test on pass/fail
scores and, for bounded scores, for a distribution-free lower confidence
bound (its appendix A.1, `stats.gate.bernstein_bound`). That bound is
reported for comparison and not used, because on 125 tasks it needs a gain
about three times larger than $T$ does before it clears zero (table below);
with $T$ the guarantee holds in the large-sample sense that the central limit
theorem provides.

## Results on the baseline runs

The two baseline runs of 27 October and 3 November are the same harness,
model and tasks. They serve three purposes, and supplying the noise for
later comparisons is not one of them, since every comparison works out its
own standard error.

- They show that a gate is needed. With nothing changed, the second run
  scored 0.71 percentage points above the first on checks passed and 2.13 on
  attempts fully passed. A rule that kept every candidate that scored higher
  would accept a change that does nothing half of the time.
- They show how large a gain this benchmark can detect, before any candidate
  is tested: about 3.4 percentage points on checks passed, with 125 tasks and
  three attempts each.
- They are a case with a known answer, on which the test itself is checked
  ("How each supposition was checked", above).

The true gain between the two runs is zero, so every acceptance below would
be false. On the 125 validation tasks of the split `haiku-20-40-40`, three
attempts each (`docs/figures/gate/` is that comparison as `compare` wrote
it: the report and its data files, without the per-task file
`differences.csv`):

| outcome               | gain   | standard error   | T     | p    | minimum detectable effect   | Bernstein bound   |
|-----------------------|--------|------------------|-------|------|-----------------------------|-------------------|
| checks passed         | +0.71  | 1.36             | +0.52 | 0.30 | 3.4                         | −8.4              |
| attempts fully passed | +2.13  | 1.96             | +1.09 | 0.14 | 4.9                         | −8.4              |

The gain, the standard error, the minimum detectable effect and the Bernstein
bound are in percentage points. No test accepts the null change. The minimum
detectable effect of checks passed is 3.4 percentage points against 4.9 for
attempts fully passed because its per-task differences take 32 distinct
values rather than 5 and so spread less. Per domain it is 5 to 10 percentage
points, which is why the gate pools the three domains and logs per-domain
scores without deciding on them. At the plan's two attempts per task the
pooled standard error grows by $\sqrt{3/2}$ and the minimum detectable effect
to about 4.2 percentage points on checks passed.

:::{figure} ../figures/gate/figures/differences.png
:alt: How many tasks rose, fell and stayed the same between the two baseline runs

The data: how many tasks rose, fell and stayed the same in checks passed,
second baseline run minus first, in bins ten percentage points wide. The
grey bar is the tasks that did not change; the line is the mean, the
estimated gain $\bar d$.
:::

:::{figure} ../figures/gate/figures/gains.png
:alt: The gain on all tasks and in each domain, each with its interval

The gain in checks passed on all 125 tasks and in each domain. Each bar
spans the gain plus and minus $X_0\,\text{SE}$; every bar crosses the
no-gain line, so no set of tasks would be accepted.
:::

The decision rules on checks passed over all 125 tasks, same data, the
strictest last:

| Rule                                     | False acceptances allowed   | Bar on $T$   | Verdict   | Minimum detectable effect (percentage points)   |
|------------------------------------------|-----------------------------|--------------|-----------|-------------------------------------------------|
| one decision at 5%                       | 5%                          | 1.66         | reject    | 3.4                                             |
| 5% shared over a run (REUSE), decision 1 | 1.25%                       | 2.27         | reject    | 4.2                                             |
| 5% shared equally over 20 decisions      | 0.25%                       | 2.86         | reject    | 5.0                                             |
| 3 sigma                                  | 0.135%                      | 3.06         | reject    | 5.3                                             |

:::{figure} ../figures/gate/figures/decisions.png
:alt: The gain measured between the two baseline runs, with its uncertainty, against the gains chance alone would give

How the gain would be spread if nothing had changed (blue, centred on zero)
and what the comparison of the two baseline runs measured, with its
uncertainty (orange, centred on +0.7). Both curves are theory and are as
wide as the standard error. The lower axis is the gain $\bar d$ in
percentage points and the upper axis the same positions in units of $T$, so
the grey line, the gain needed at 5%, stands at the bar $X_0$ and the orange
line at the $T$ observed. The measured gain falls well short of the 2.3
percentage points needed, and the shaded part of the blue curve beyond it is
$p$. The two curves nearly coincide, as they should for two runs of one
harness.
:::

Each figure of a comparison is drawn from the comparison's data files by
`stats.figures`, and `stats.figures.draw` redraws all four from a
comparison's folder (the copy in `docs/figures/gate/` has no
`differences.csv`, so the `differences` figure cannot be redrawn from it):

| Figure | Drawn from | By |
|---|---|---|
| `gains` | `tests.csv`: `set`, `tasks`, `gain_percentage_points`, `lower_bound_percentage_points` | `stats.figures.gains` |
| `differences` | `differences.csv`: `checks_passed_difference_percentage_points` | `stats.figures.differences` |
| `detectable` | `tests.csv`: `standard_error_percentage_points`, `tasks`, `gain_percentage_points`; `rules.csv`: `false_acceptances_allowed_percent`, `minimum_detectable_effect_percentage_points`; the curves from `stats.gate.power` | `stats.figures.detectable` |
| `decisions` | `tests.csv`: `gain_percentage_points`, `standard_error_percentage_points`, `bar`, `p`, `tasks`; the curves from Student's $t$ | `stats.figures.decisions` |

Earlier work on acceptance rules for harness edits showed what happens
without a noise term: on the same two runs, a rule that keeps any candidate
whose mean is higher accepted the null change in three comparisons out of
eight, and a rule with a band calibrated from the runs it judged in four
(the project's gate evidence). Every rule with a noise term accepted none.

## Decisions recorded here

- The gate's outcome is checks passed; attempts fully passed is reported
  beside it.
- The gate's test is the one-sided paired $t$-test (Student's $t$), pooled
  over domains. Its formula decides; the bootstrap, the permutation test and
  the sign test are computed in every comparison as checks on it, and the
  Bernstein bound is reported for comparison.
- Each decision's level comes from the REUSE allocation, with a level of
  0.05 for the whole run; the equal split and the fixed significance are
  shown beside it.
- Per-domain scores are logged at every decision and never gated on.
- The test is checked on two runs of one harness before its verdicts are
  relied on for a new model or benchmark (`mas-optimiser calibrate`).
