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

- *task success*: 1 if every check passed, else 0;
- *fraction of checks passed*: checks passed over checks total.

A task's score under a harness is the mean of its attempts' outcomes. With
task success and three attempts it is one of $0, 1/3, 2/3, 1$; with the
fraction it takes many values. For a candidate $B$ and the current harness $A$ run
on the same $N$ tasks, the per-task difference is

$$d_i = \text{score}_i(B) - \text{score}_i(A), \qquad i = 1, \dots, N,$$

and the estimated gain is $\bar d$, the mean of the $d_i$. Pairing on the
task removes the task's own difficulty from the comparison, so the sides must
have scored the same tasks (`stats.outcomes.paired`).

The gate uses the fraction of checks passed, declared as the primary outcome
before any comparison was run, and reports task success beside it. The
fraction has the lower noise floor (below), at the price of meaning: partial
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
$\alpha = 0.05$, $X_0 = 1.657$; the normal quantile is 1.645. The p-value is
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

found numerically (`stats.gate.detectable`; the column `detectable (points)`
of the report). The normal approximation,
$(z_{1-\alpha} + z_{0.8})\,\text{SE} = 2.49\,\text{SE}$, is within one
percent of it at this $N$. A
real gain below the floor is missed more often than one time in five. The
standard error is a property of the data; the floor follows from it and from
the level of the decision, so a stricter level has a higher floor, and a rule
with no level has no floor and accepts noise.

:::{figure} ../figures/gate/threshold.png
:alt: Two curves of the statistic with the threshold and the observed value

The statistic $T$ when there is no real gain (first curve) and when the true
gain equals the noise floor (second curve). Both are theory, not data:
Student's $t$ and its noncentral form. The vertical line at $X_0$ is the threshold: the
shaded area of the first curve to its right is $\alpha$, the shaded area of
the second to its left is $\beta$. The other vertical line is the value of
$T$ observed on the two baseline runs.
:::

:::{figure} ../figures/gate/power.png
:alt: Power of the test against the true gain

The chance that the test accepts a candidate, its power, against the size of
the candidate's true gain, at the standard error of the two baseline runs and
$\alpha = 0.05$. The curve starts at $\alpha$ for no gain and crosses $0.8$,
the dashed line, at the noise floor, the vertical line.
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
the same for every comparison except for
the observed $T$ and the size of the noise floor in points. The data itself
is the figure of per-task differences further down. The theorem is an
approximation for a finite number of tasks; the permutation test and the
bootstrap, next, check it on the data without assuming it.

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
`haiku-20-40-40`, three attempts each (`figures/gate/comparison.md` holds
the full report):

| Outcome | $\bar d$ (points) | SE (points) | $T$ | $p$ | Noise floor (points) | Sign test | Permutation $p$ | Bootstrap SE | Bernstein bound |
|---|---|---|---|---|---|---|---|---|---|
| Fraction of checks passed | +0.71 | 1.36 | +0.52 | 0.30 | 3.4 | 32 up, 27 down, $p$ 0.30 | 0.30 | 1.37 | −8.4 |
| Task success | +2.13 | 1.96 | +1.09 | 0.14 | 4.9 | 17 up, 13 down, $p$ 0.29 | 0.17 | 1.97 | −8.4 |

No test accepts the null change. For the fraction the three p-values agree,
which is the test's distribution checked by two methods that do not assume
it, and the bootstrap reproduces the analytic standard error. For task
success, whose differences take only five values, the permutation p-value
(0.17) sits above the $t$-test's (0.14): about 6% of the relabellings tie
with the observed mean, and ties count towards it. The fraction's
floor is 3.4 points against 4.9 for task success because its per-task
differences take 32 distinct values rather than 5 and so spread less. Per
domain the floors are 5 to 10 points, which is why the gate pools the three
domains and logs per-domain scores without deciding on them. At the plan's
two attempts per task the pooled standard error grows by $\sqrt{3/2}$ and the
floor to about 4.2 points on the fraction.

:::{figure} ../figures/gate/differences.png
:alt: The number of tasks at each value of the per-task difference

The data: the number of tasks at each value of the per-task difference in
the fraction of checks passed, second baseline run minus first. Most tasks
do not move and the rest spread either side of zero; the marked mean is the
estimated gain $\bar d$.
:::

:::{figure} ../figures/gate/domains.png
:alt: Mean score of each run per domain and pooled

The mean fraction of checks passed in each domain and over all 125 tasks,
for the first baseline run ("current") and the second ("candidate").
:::

The decision rules on the pooled fraction, same data:

| Rule | Level | $X_0$ | Verdict | Noise floor (points) |
|---|---|---|---|---|
| level as given, 0.05 | 0.05 | 1.66 | reject | 3.4 |
| fixed 3 sigma | 0.0013 | 3.06 | reject | 5.3 |
| equal split over 20 decisions | 0.0025 | 2.86 | reject | 5.0 |
| REUSE allocation, round 1, no promotions, 1 candidate | 0.013 | 2.27 | reject | 4.2 |

Earlier work on acceptance rules for harness edits showed what happens
without a noise term: on the same two runs, a rule that keeps any candidate
whose mean is higher accepted the null change in three comparisons out of
eight, and a rule with a band calibrated from the runs it judged in four
(the project's gate evidence). Every rule with a noise term accepted none.

## Decisions recorded here

- The gate's outcome is the fraction of checks passed; task success is
  reported beside it.
- The gate's test is the one-sided paired $t$-test (Student's $t$), pooled
  over domains;
  the sign test, the permutation test and the bootstrap are reported beside
  it, and the Bernstein bound for comparison.
- Each decision's level comes from the REUSE allocation with a run budget of
  $\alpha = 0.05$; the equal split and the fixed significance are shown
  beside it.
- Per-domain scores are logged at every decision and never gated on.
