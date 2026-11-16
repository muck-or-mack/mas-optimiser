"""The acceptance gate's test, its companion, and what the data can detect.

The null hypothesis is that the candidate's true mean per-task gain over the
current is at most zero. ``mean_test`` is the one-sided paired t-test, run
by ``scipy.stats.ttest_1samp`` on the per-task differences: the observed
mean, in units of its standard error, against the quantile of Student's t
at the decision's Type-I error level ``alpha``; the candidate is kept when
the statistic crosses it. ``power`` is the chance of finding a given true
gain, from the noncentral t distribution, and ``detectable`` the smallest
gain found with a chosen power. Beside the main test, and not in its place:
``sign_test``, the exact test on the tasks whose score changed, and
``bernstein_bound``, the distribution-free lower confidence bound of Sun et
al. (arXiv 2609.33180). The permutation test and the bootstrap reported
with them are SciPy's own, called where the report is written.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``stats``.
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy import optimize, special, stats


@dataclass(frozen=True)
class MeanTest:
    """The one-sided test of the mean paired difference.

    Attributes:
        n: The number of tasks.
        mean: The mean difference, the estimated gain.
        sd: The sample standard deviation of the differences.
        se: The standard error of the mean, ``sd / sqrt(n)``.
        t: The test statistic ``mean / se``.
        x0: The threshold on ``t`` at the level ``alpha``, the quantile of
            Student's t with ``n - 1`` degrees of freedom.
        p: The p-value, ``P(T >= t | H0)`` under that distribution.
        alpha: The Type-I error level the decision was made at.
        accept: Whether ``t`` crossed ``x0``.
        lower: The one-sided lower confidence bound on the true mean at
            confidence ``1 - alpha``, ``mean - x0 * se``.
    """

    n: int
    mean: float
    sd: float
    se: float
    t: float
    x0: float
    p: float
    alpha: float
    accept: bool
    lower: float


def mean_test(d: np.ndarray, alpha: float = 0.05) -> MeanTest:
    """Test whether the mean paired difference is above zero.

    The one-sided, one-sample t-test of the differences against zero
    (``scipy.stats.ttest_1samp``). The standard error is the sample standard
    deviation (with Bessel's correction) over the square root of the task
    count, and the statistic ``t = mean / se`` is compared with ``x0``, the
    ``1 - alpha`` quantile of Student's t with ``n - 1`` degrees of freedom:
    the mean of many bounded differences is close to normal by the central
    limit theorem, and Student's t allows for the standard error being
    estimated from the same differences. At 125 tasks ``x0`` is 1.657 at
    ``alpha = 0.05``, against 1.645 for the normal quantile.

    Args:
        d: The per-task differences, candidate minus current.
        alpha: The Type-I error allowed for this decision.

    Returns:
        The test's numbers and verdict.

    Raises:
        ValueError: If there are fewer than two tasks, or every difference is
            the same so that the standard error is zero.
    """
    d = np.asarray(d, dtype=float)
    n = len(d)
    if n < 2:
        raise ValueError("a paired test needs at least two tasks")
    sd = float(d.std(ddof=1))
    if sd == 0.0:
        raise ValueError("every difference is equal, so the standard error is zero")
    result = stats.ttest_1samp(d, 0.0, alternative="greater")
    x0 = float(stats.t.ppf(1 - alpha, result.df))
    return MeanTest(
        n=n,
        mean=float(d.mean()),
        sd=sd,
        se=sd / math.sqrt(n),
        t=float(result.statistic),
        x0=x0,
        p=float(result.pvalue),
        alpha=alpha,
        accept=bool(result.statistic > x0),
        lower=float(result.confidence_interval(1 - alpha).low),
    )


@dataclass(frozen=True)
class SignTest:
    """The exact one-sided sign test on the tasks whose score changed.

    Attributes:
        n_plus: Tasks where the candidate scored higher.
        n_minus: Tasks where the current harness scored higher.
        p: ``P(Bin(n_plus + n_minus, 1/2) >= n_plus)``; 1 when no task changed.
        alpha: The Type-I error level the decision was made at.
        accept: Whether ``p`` is at most ``alpha``.
    """

    n_plus: int
    n_minus: int
    p: float
    alpha: float
    accept: bool


def sign_test(d: np.ndarray, alpha: float = 0.05) -> SignTest:
    """The exact sign test: did the candidate win more of the changed tasks than chance?

    Under the null hypothesis each changed task is a fair coin (Sun et al.,
    arXiv 2609.33180, Lemma A2), so the p-value is the upper tail of
    ``Bin(M, 1/2)`` at the candidate's wins, with ``M`` the changed tasks. The
    test uses only the direction of each difference, so it does not depend on
    their distribution, at the price of some power.

    Args:
        d: The per-task differences, candidate minus current.
        alpha: The Type-I error allowed for this decision.

    Returns:
        The counts, the p-value and the verdict.
    """
    d = np.asarray(d, dtype=float)
    n_plus = int((d > 0).sum())
    n_minus = int((d < 0).sum())
    changed = n_plus + n_minus
    if changed == 0:
        p = 1.0
    else:
        p = float(stats.binomtest(n_plus, changed, 0.5, alternative="greater").pvalue)
    return SignTest(n_plus=n_plus, n_minus=n_minus, p=p, alpha=alpha, accept=p <= alpha)


def bernstein_bound(d: np.ndarray, delta: float) -> float:
    """REUSE's lower confidence bound on the mean of bounded paired differences.

    Sun et al., arXiv 2609.33180, appendix A.1, Eqs. 5 to 7, for differences
    in ``[-1, 1]``: an upper confidence bound ``s+`` on the mean square
    (Eq. 5) stands in for the variance in a Bernstein deviation ``eps``
    (Eq. 6), and the bound is ``mean - eps`` (Eq. 7). With probability at
    least ``1 - delta`` the true mean is above it, whatever the differences'
    distribution and for any task count. On 125 tasks it needs a gain about
    three times larger than ``mean_test`` does before it clears zero, which is
    why it is reported for comparison rather than used as the gate.

    Args:
        d: The per-task differences, each in ``[-1, 1]``.
        delta: The error level, ``L = ln(2 / delta)`` in the paper's notation.

    Returns:
        The lower confidence bound on the true mean difference.

    Raises:
        ValueError: If any difference lies outside ``[-1, 1]``.
    """
    d = np.asarray(d, dtype=float)
    if d.min() < -1 or d.max() > 1:
        raise ValueError("the bound needs differences in [-1, 1]")
    n = len(d)
    big_l = math.log(2 / delta)
    mean_square = float((d**2).mean())

    # s+ is the largest q in [mean_square, 1] with n * kl(mean_square || q) <= L,
    # kl being the relative entropy of two Bernoulli variables; it grows with q
    # on that interval, so a root find on the excess suffices.
    def excess(q: float) -> float:
        kl = special.rel_entr(mean_square, q) + special.rel_entr(1 - mean_square, 1 - q)
        return n * float(kl) - big_l

    top = 1 - 1e-12
    if mean_square >= top or excess(top) <= 0:
        s_plus = 1.0
    else:
        s_plus = float(optimize.brentq(excess, mean_square, top))
    eps = 2 * big_l / (3 * n) + math.sqrt(
        (2 * big_l / (3 * n)) ** 2 + 2 * s_plus * big_l / n
    )
    return float(d.mean()) - eps


def detectable(se: float, n: int, alpha: float = 0.05, power: float = 0.8) -> float:
    """The smallest true gain the test finds with the given power: the noise floor.

    The gain at which ``power`` (the function below) reaches the wanted
    value, found by a root search. At 125 tasks and the defaults it is 2.50
    standard errors; the normal approximation
    ``(z_{1-alpha} + z_{power}) * se`` gives 2.49.

    Args:
        se: The standard error of the mean difference.
        n: The number of tasks.
        alpha: The Type-I error allowed for the decision.
        power: The probability of detecting the gain, ``1 - beta``.

    Returns:
        The minimum detectable gain, in the score's units.
    """
    df = n - 1
    x0 = stats.t.ppf(1 - alpha, df)
    shift = optimize.brentq(lambda c: stats.nct.sf(x0, df, c) - power, 0.0, 50.0)
    return float(shift * se)


def power(se: float, n: int, gain: float, alpha: float = 0.05) -> float:
    """The probability that the test detects a given true gain.

    With a true mean gain the statistic follows the noncentral t
    distribution with ``n - 1`` degrees of freedom and noncentrality
    ``gain / se`` (``scipy.stats.nct``); the power is the part of it beyond
    the threshold. With no gain it is ``alpha``.

    Args:
        se: The standard error of the mean difference.
        n: The number of tasks.
        gain: The true mean gain, in the score's units.
        alpha: The Type-I error allowed for the decision.

    Returns:
        The power, between 0 and 1.
    """
    df = n - 1
    return float(stats.nct.sf(stats.t.ppf(1 - alpha, df), df, gain / se))
