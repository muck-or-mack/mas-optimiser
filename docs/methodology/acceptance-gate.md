# The acceptance gate

The optimiser proposes a changed harness, the candidate, and must decide
whether it is better than the harness it has, the current one (the papers call it the incumbent). The decision is
made on the validation tasks and is a statistical test: the candidate's gain
has to be larger than the gain that chance alone produces between two runs of
one and the same harness. This page defines the quantities, states the test
and the rules that set its level, and shows them on the two baseline runs of
the same harness, where any acceptance is a false one. The code is
`mas_optimiser.stats`; the command `mas-optimiser compare` produces every
table and figure below.

## Scores and differences

A *task* is one request to the agent, judged by several *checks*, each a row
the database must hold afterwards. An *attempt* is one run of the agent on
one task; each task is attempted $K$ times per harness ($K = 3$ in the
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
passed has the lower noise floor (below), at the price of meaning: partial
credit can rise without more tasks being finished, which is why the coarser
outcome is always shown.

## The test

The null hypothesis $H_0$ is that the candidate's true mean gain is at most
zero; the alternative $H_1$ is that it is positive. The test statistic is the
observed mean in units of its standard error,

$$\text{SE} = \frac{s_d}{\sqrt{N}}, \qquad T = \frac{\bar d}{\text{SE}},$$

with $s_d$ the sample standard deviation of the differences (Bessel's
correction). This is the one-sided paired $t$-test. The mean of many bounded
differences is close to normal by the central limit theorem, and because
$\text{SE}$ is itself estimated from the same $N$ differences, the statistic
under $H_0$ follows Student's $t$ distribution with $N - 1$ degrees of
freedom, a little wider than the standard normal. The gate keeps the
candidate when

$$T > X_0 = F_{N-1}^{-1}(1 - \alpha),$$

where $F_{N-1}$ is the cumulative distribution of Student's $t$ and
$\alpha = P(T > X_0 \mid H_0)$ is the Type-I error allowed for the decision,
the chance of accepting a candidate that is no better. At $N = 125$ and
$\alpha = 0.05$, $X_0 = 1.657$ (the reports call $X_0$ the bar on $T$); the
normal quantile is 1.645. The p-value is
$P(T \ge t_{\text{obs}} \mid H_0) = 1 - F_{N-1}(t_{\text{obs}})$, and
$\bar d - X_0\,\text{SE}$ is a one-sided lower confidence bound on the true
gain at confidence $1 - \alpha$. The test is `stats.gate.mean_test`, which
calls `scipy.stats.ttest_1samp` on the differences; the theory is textbook
(Barlow, *Statistics*, Wiley, 1989, the chapters on errors and on hypothesis
testing).

The second error is missing a real gain: $\beta = P(T < X_0 \mid H_1)$, and
$1 - \beta$ is the *power*. With a true gain $g$ the statistic follows the
noncentral $t$ distribution with $N - 1$ degrees of freedom and
noncentrality $g / \text{SE}$, which sits close to $g / \text{SE}$, and the
power at $g$ is the part of that distribution beyond $X_0$
(`stats.gate.power`). The smallest gain found with power $0.8$ is the
*noise floor* (the minimum detectable effect of the statistics literature):

$$g_{\min} = 2.50\,\text{SE} \quad \text{at } \alpha = 0.05,\ N = 125,$$

found numerically (`stats.gate.detectable`; `noise_floor_percentage_points`
in `tests.csv`). The normal approximation,
$(z_{1-\alpha} + z_{0.8})\,\text{SE} = 2.49\,\text{SE}$, is within one
percent of it at this $N$. A
real gain below the floor is missed more often than one time in five. The
standard error is a property of the data; the floor follows from it and from
the level of the decision, so a stricter level has a higher floor, and a rule
with no level has no floor and accepts noise.

:::{figure} ../figures/calibration/figures/distributions.png
:alt: Two curves of the statistic, with the bar between them

What the test assumes: the statistic $T$ when nothing changed (solid curve)
and when the true gain equals the noise floor (dashed curve), at 125 tasks.
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
rule's level for no gain and crosses 80% at that rule's noise floor, marked.
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
gain is $g$, whose curve for no gain is the standard normal density,

$$\varphi(t) = \frac{1}{\sqrt{2\pi}}\, e^{-t^2/2}.$$

The test divides by the estimated $\text{SE}$, which adds a little spread:
that is Student's $t$ distribution, whose density approaches $\varphi$ as
$N$ grows and at $N = 125$ has standard deviation 1.008. With a true gain it
becomes the noncentral $t$.

The first figure draws those two densities, for no gain and for a gain equal
to the noise floor. At $\alpha = 0.05$ and a given $N$ the second curve sits
at the same place whatever the data (2.50 at $N = 125$), so the picture is
the same for every comparison of that many tasks; only the size of the
noise floor in percentage points differs. The data itself is the figure of
per-task differences further down. The theorem is an approximation for a
finite number of tasks: the permutation test and the bootstrap, next, check
it on the data without assuming it, and the calibration section below
measures how well it holds on two runs of one harness.

Three checks are reported beside the test, none in its place (the sign test
is `stats.gate.sign_test`; the other two are SciPy's `permutation_test` and
`bootstrap`, called by `stats.compare`):

- the *exact sign test* on the tasks whose score changed: under $H_0$ each
  changed task is a fair coin, so $p = P(\text{Bin}(M, 1/2) \ge n_+)$ with
  $M$ the changed tasks and $n_+$ the candidate's wins (Sun et al., 2026,
  Lemma A2); it uses only the direction of each difference;
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
  Trust?*, arXiv:2609.33180, 2026, Eq. 4), the gate's rule: decision $t$,
  after $p$ promotions, with $K$ candidates in the round, is made at

  $$\delta_{t,p} = \frac{\alpha\, w_t\, v_p}{\binom{t-1}{p} K^{p+1}},
  \qquad w_t = \frac{1}{t(t+1)}, \quad v_p = \frac{1}{(p+1)(p+2)}.$$

  The weights sum to one over rounds and over promotion counts, and the
  binomial coefficient and the power of $K$ cover every history of
  promotions, so the levels of all decisions a run could make sum to at most
  $\alpha$: with probability at least $1 - \alpha$ every promotion in the run
  is a true improvement, however the proposer adapts. Later rounds and more
  promotions get smaller levels, so stronger evidence is demanded as the run
  goes on.
- **Equal split** (Bonferroni's inequality): $\alpha$ divided by a number of
  decisions fixed before the run. Valid, simple, and blind to how the run
  unfolds.
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

## The noise floor on the baseline runs

The two baseline runs of 27 October and 3 November are the same harness,
model and tasks, so the true gain between them is zero and every acceptance
below would be false. On the 125 validation tasks of the split
`haiku-20-40-40`, three attempts each (`docs/figures/gate/` is that
comparison as `compare` wrote it: the report and its data files):

| Outcome | $\bar d$ | SE | $T$ | $p$ | Noise floor | Sign test | Permutation $p$ | Bootstrap SE | Bernstein bound |
|---|---|---|---|---|---|---|---|---|---|
| Checks passed | +0.71 | 1.36 | +0.52 | 0.30 | 3.4 | 32 up, 27 down, $p$ 0.30 | 0.30 | 1.37 | −8.4 |
| Attempts fully passed | +2.13 | 1.96 | +1.09 | 0.14 | 4.9 | 17 up, 13 down, $p$ 0.29 | 0.17 | 1.97 | −8.4 |

$\bar d$, SE, the noise floor and the Bernstein bound are in percentage
points. No test accepts the null change. For checks passed the three
p-values agree, which is the test's distribution checked by two methods that
do not assume it, and the bootstrap reproduces the analytic standard error.
For attempts fully passed, whose differences take only five values, the
permutation p-value (0.17) sits above the $t$-test's (0.14): about 6% of the
relabellings tie with the observed mean, and ties count towards it. The
floor of checks passed is 3.4 percentage points against 4.9 for attempts
fully passed because its per-task differences take 32 distinct values rather
than 5 and so spread less. Per domain the floors are 5 to 10 percentage
points, which is why the gate pools the three
domains and logs per-domain scores without deciding on them. At the plan's
two attempts per task the pooled standard error grows by $\sqrt{3/2}$ and the
floor to about 4.2 percentage points on checks passed.

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

| Rule | False acceptances allowed | Bar on $T$ | Verdict | Noise floor (percentage points) |
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
| `detectable` | `tests.csv`: `standard_error_percentage_points`, `tasks`, `gain_percentage_points`; `rules.csv`: `false_acceptances_allowed_percent`, `noise_floor_percentage_points`; the curves from `stats.gate.power` | `stats.figures.detectable` |
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
- Each decision's level comes from the REUSE allocation with a run budget of
  $\alpha = 0.05$; the equal split and the fixed significance are shown
  beside it.
- Per-domain scores are logged at every decision and never gated on.
- The test is checked on two runs of one harness before its verdicts are
  relied on for a new model or benchmark (`mas-optimiser calibrate`).
