# The acceptance gate

The optimiser proposes a changed harness, the candidate, and must decide
whether it is better than the harness it has, the current one (the papers call it the incumbent). The decision is
made on the validation tasks and is a statistical test: the candidate's gain
has to be larger than the gain that chance alone produces between two runs of
one and the same harness. This page defines the quantities, states the test
and the rules that set how many false acceptances it allows (its level), and
shows them on the two baseline runs of
the same harness, where any acceptance is a false one. The code is
`mas_optimiser.stats`; the command `mas-optimiser compare` produces every
table and figure below.

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

## Scores and differences

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

## The test

The null hypothesis $H_0$ is that the candidate's true mean gain is at most
zero; the alternative $H_1$ is that it is positive. The test statistic is the
observed mean in units of its standard error,

$$\text{SE} = \frac{s_d}{\sqrt{N}}, \qquad T = \frac{\bar d}{\text{SE}},$$

with $s_d$ the standard deviation of the $N$ differences (computed with
$N - 1$ in the denominator, as for any sample). This is the one-sided paired
$t$-test. The mean of many bounded
differences is close to normal by the central limit theorem, and because
$\text{SE}$ is itself estimated from the same $N$ differences, the statistic
under $H_0$ follows Student's $t$ distribution with $N - 1$ degrees of
freedom (the number of differences less one, which sets the curve's width), a
little wider than the standard normal. The gate keeps the candidate when

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
gain at confidence $1 - \alpha$. The test is `stats.gate.mean_test`, which
calls `scipy.stats.ttest_1samp` on the differences; the theory is textbook
(Barlow, *Statistics*, Wiley, 1989, the chapters on errors and on hypothesis
testing).

The second error is missing a real gain: $\beta = P(T < X_0 \mid H_1)$, and
$1 - \beta$ is the *power*, the chance that a real gain is accepted. The bar
$X_0$ trades the two errors against each other: a higher bar accepts fewer
changes that do nothing and misses more real gains.

With the bar where a rule has put it, how large must a real gain be to be
caught? Three steps, on the two baseline runs of the sections below
(standard error 1.36 percentage points, bar 1.66):

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

### Where the bell curves come from

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

The first figure draws those two densities, for no gain and for a gain equal
to the minimum detectable effect. At $\alpha = 0.05$ and a given $N$ the second curve sits
at the same place whatever the data (2.50 at $N = 125$), so the picture is
the same for every comparison of that many tasks; only the size of the
minimum detectable effect in percentage points differs. The data itself is the figure of
per-task differences further down. The theorem is an approximation for a
finite number of tasks: the permutation test and the bootstrap, next, check
it on the data without assuming it, and the calibration section below
measures how well it holds on two runs of one harness.

Three checks are reported beside the test, none in its place (the sign test
is `stats.gate.sign_test`; the other two are SciPy's `permutation_test` and
`bootstrap`, called by `stats.compare`):

- the *exact sign test* on the tasks whose score changed: under $H_0$ each
  changed task is a fair coin, so its p-value is
  $P(\text{Bin}(M, 1/2) \ge n_+)$, the chance of at least $n_+$ heads in $M$
  tosses, with $M$ the changed tasks and $n_+$ the candidate's wins (Sun et
  al., 2026, Lemma A2); it uses only the direction of each difference;
- the *permutation test*: under $H_0$ the two harnesses' scores on a task are
  exchangeable, so each difference keeps its size and takes a random sign;
  the p-value is the fraction of such relabellings whose mean is at or above
  the observed one (Efron and Tibshirani, *An Introduction to the Bootstrap*,
  Chapman and Hall, 1993, the chapter on permutation tests); it assumes
  nothing about the differences' distribution;
- the *bootstrap*: the tasks resampled with replacement, giving the spread of
  the mean difference as a check on the analytic standard error (Efron and
  Tibshirani, 1993, the chapter on the bootstrap standard error).

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

## The minimum detectable effect on the baseline runs

The two baseline runs of 27 October and 3 November are the same harness,
model and tasks, so the true gain between them is zero and every acceptance
below would be false. On the 125 validation tasks of the split
`haiku-20-40-40`, three attempts each (`docs/figures/gate/` is that
comparison as `compare` wrote it: the report and its data files):

| Outcome | Gain | Standard error | T | p | Minimum detectable effect | Sign test | Permutation p | Bootstrap standard error | Bernstein bound |
|---|---|---|---|---|---|---|---|---|---|
| Checks passed | +0.71 | 1.36 | +0.52 | 0.30 | 3.4 | 32 up, 27 down, p 0.30 | 0.30 | 1.37 | −8.4 |
| Attempts fully passed | +2.13 | 1.96 | +1.09 | 0.14 | 4.9 | 17 up, 13 down, p 0.29 | 0.17 | 1.97 | −8.4 |

The gain, the standard errors, the minimum detectable effect and the
Bernstein bound are in percentage points. No test accepts the null change. For checks passed the three
p-values agree, which is the test's distribution checked by two methods that
do not assume it, and the bootstrap reproduces the analytic standard error.
For attempts fully passed, whose differences take only five values, the
permutation p-value (0.17) sits above the $t$-test's (0.14): about 6% of the
relabellings tie with the observed mean, and ties count towards it. The
minimum detectable effect of checks passed is 3.4 percentage points against 4.9 for attempts
fully passed because its per-task differences take 32 distinct values rather
than 5 and so spread less. Per domain it is 5 to 10 percentage
points, which is why the gate pools the three
domains and logs per-domain scores without deciding on them. At the plan's
two attempts per task the pooled standard error grows by $\sqrt{3/2}$ and the
minimum detectable effect to about 4.2 percentage points on checks passed.

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

| Rule | False acceptances allowed | Bar on $T$ | Verdict | Minimum detectable effect (percentage points) |
|---|---|---|---|---|
| one decision at 5% | 5% | 1.66 | reject | 3.4 |
| 5% shared over a run (REUSE), decision 1 | 1.25% | 2.27 | reject | 4.2 |
| 5% shared equally over 20 decisions | 0.25% | 2.86 | reject | 5.0 |
| 3 sigma | 0.135% | 3.06 | reject | 5.3 |

:::{figure} ../figures/gate/figures/decisions.png
:alt: The observed statistic against the bar of each decision rule

The $T$ observed between the two baseline runs, the vertical line, against
the bar of each rule; it lies to the left of every bar.
:::

Each figure of a comparison is drawn from the comparison's data files by
`stats.figures`, and `stats.figures.draw` redraws all four from a folder:

| Figure | Drawn from | By |
|---|---|---|
| `gains` | `tests.csv`: `set`, `tasks`, `gain_percentage_points`, `lower_bound_percentage_points` | `stats.figures.gains` |
| `differences` | `differences.csv`: `checks_passed_difference_percentage_points` | `stats.figures.differences` |
| `detectable` | `tests.csv`: `standard_error_percentage_points`, `tasks`, `gain_percentage_points`; `rules.csv`: `false_acceptances_allowed_percent`, `minimum_detectable_effect_percentage_points`; the curves from `stats.gate.power` | `stats.figures.detectable` |
| `decisions` | `tests.csv`: `t`; `rules.csv`: `bar`, `verdict` | `stats.figures.decisions` |

Earlier work on acceptance rules for harness edits showed what happens
without a noise term: on the same two runs, a rule that keeps any candidate
whose mean is higher accepted the null change in three comparisons out of
eight, and a rule with a band calibrated from the runs it judged in four
(the project's gate evidence). Every rule with a noise term accepted none.

## Calibration on the baseline runs

The bars rest on one claim: that when nothing has changed, $T$ follows
Student's $t$. The two baseline runs allow a direct check
(`mas-optimiser calibrate`, `stats.calibrate`). Each of the 125 validation
tasks has six attempts of the same harness, three from each run.

:::{figure} ../figures/calibration/figures/scatter.png
:alt: Each task's score in the first run against its score in the second

Each task's checks passed in the first baseline run against the second; a
larger dot is more tasks. On the diagonal a task scored the same both times.
The spread around it is the noise between two runs of one harness, which
any gain has to stand out from.
:::

The six attempts of each task are split at random into two halves, 200,000
times (SciPy's `permutation_test`, exchanging each task's attempts between
the six attempt columns), and each split is compared as the gate compares a candidate with the
current harness. Every split is a comparison in which nothing changed, so
every acceptance is a false one (`docs/figures/calibration/` holds the
command's report and its table):

| Quantity | Measured | Expected |
|---|---|---|
| Mean of $T$ | −0.002 ± 0.002 | 0 |
| Width of $T$ | 1.009 ± 0.002 | 1.008 |
| Splits accepted at one decision at 5% | 4.97 ± 0.05% | 5% |
| Splits accepted at 5% shared over a run (REUSE), decision 1 | 1.20 ± 0.02% | 1.25% |
| Splits accepted at 5% shared equally over 20 decisions | 0.21 ± 0.01% | 0.25% |
| Splits accepted at 3 sigma | 0.11 ± 0.01% | 0.135% |
| Splits accepted by any gain | 50.0 ± 0.1% | |

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

The check has limits:

- It measures false acceptance, not how often a real gain is found.
- It treats a task's six attempts as interchangeable, so any difference
  between the days of the two runs counts as noise.
- It covers one decision at a time, not what a loop adds by reusing one run
  of the current harness across many candidates.
- The uncertainties in the table are those of the random splitting alone.
  Every split draws on the same 750 attempts, so another pair of runs would
  give slightly different numbers.

## Decisions recorded here

- The gate's outcome is checks passed; attempts fully passed is reported
  beside it.
- The gate's test is the one-sided paired $t$-test (Student's $t$), pooled
  over domains;
  the sign test, the permutation test and the bootstrap are reported beside
  it, and the Bernstein bound for comparison.
- Each decision's level comes from the REUSE allocation, with a level of
  0.05 for the whole run; the equal split and the fixed significance are
  shown beside it.
- Per-domain scores are logged at every decision and never gated on.
- The test is checked on two runs of one harness before its verdicts are
  relied on for a new model or benchmark (`mas-optimiser calibrate`).
