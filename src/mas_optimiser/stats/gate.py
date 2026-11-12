"""The acceptance gate's test, its companion, and what the data can detect.

The null hypothesis is that the candidate's true mean per-task gain over the
current is at most zero. ``mean_test`` compares the observed mean, in units
of its standard error, with the normal quantile at the decision's Type-I
error level ``alpha``; the candidate is kept when the statistic crosses it.
``detectable`` gives the smallest true gain the test finds with a chosen
power and ``power`` the chance of finding a given gain. Beside the main test,
and not in its place: ``sign_test``, the exact test on the tasks whose score
changed; ``permutation_test``, the same mean difference judged against random
relabellings of the two sides; ``bootstrap``, the spread of the mean
difference from resampled tasks; and ``bernstein_bound``, the
distribution-free lower confidence bound of Sun et al. (arXiv 2609.33180).

Sources: ``docs/how-it-works.md``, section 6, the rows for ``stats``.
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy import optimize, stats


@dataclass(frozen=True)
class MeanTest:
    """The one-sided test of the mean paired difference.

    Attributes:
        n: The number of tasks.
        mean: The mean difference, the estimated gain.
        sd: The sample standard deviation of the differences.
        se: The standard error of the mean, ``sd / sqrt(n)``.
        t: The test statistic ``mean / se``.
        x0: The threshold on ``t`` at the level ``alpha``.
        p: The p-value, ``P(T >= t | H0)`` under the normal distribution.
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

    The standard error is the sample standard deviation (with Bessel's
    correction) over the square root of the task count, and the statistic
    ``t = mean / se`` is compared with the normal quantile
    ``x0 = Phi^-1(1 - alpha)``, the mean of many bounded differences being
    normal by the central limit theorem. The normal quantile is used rather
    than Student's t; at 125 tasks the two thresholds differ by 0.01.

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
    mean = float(d.mean())
    se = sd / math.sqrt(n)
    t = mean / se
    x0 = float(stats.norm.ppf(1 - alpha))
    return MeanTest(
        n=n,
        mean=mean,
        sd=sd,
        se=se,
        t=t,
        x0=x0,
        p=float(stats.norm.sf(t)),
        alpha=alpha,
        accept=t > x0,
        lower=mean - x0 * se,
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


def _kl(a: float, b: float) -> float:
    """The Bernoulli relative entropy ``kl(a || b)`` with its endpoint conventions.

    Args:
        a: A probability.
        b: A probability.

    Returns:
        ``a ln(a/b) + (1 - a) ln((1 - a)/(1 - b))``, with ``0 ln 0 = 0``.
    """
    total = 0.0
    if a > 0:
        total += a * math.log(a / b)
    if a < 1:
        total += (1 - a) * math.log((1 - a) / (1 - b))
    return total


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
    # s+ is the largest q in [mean_square, 1] with n * kl(mean_square || q) <= L;
    # kl grows with q on that interval, so a root find on the excess suffices.
    excess = lambda q: n * _kl(mean_square, q) - big_l  # noqa: E731
    top = 1 - 1e-12
    if mean_square >= top or excess(top) <= 0:
        s_plus = 1.0
    else:
        s_plus = float(optimize.brentq(excess, mean_square, top))
    eps = 2 * big_l / (3 * n) + math.sqrt(
        (2 * big_l / (3 * n)) ** 2 + 2 * s_plus * big_l / n
    )
    return float(d.mean()) - eps


def detectable(se: float, alpha: float = 0.05, power: float = 0.8) -> float:
    """The smallest true gain the test finds with the given power: the noise floor.

    With a true gain ``g`` the statistic is centred on ``g / se`` and crosses
    ``x0`` with probability ``power`` when ``g / se - x0`` equals the normal
    quantile at ``power``, so ``g = (z_{1-alpha} + z_{power}) * se``, 2.49
    standard errors at the defaults.

    Args:
        se: The standard error of the mean difference.
        alpha: The Type-I error allowed for the decision.
        power: The probability of detecting the gain, ``1 - beta``.

    Returns:
        The minimum detectable gain, in the score's units.
    """
    return float((stats.norm.ppf(1 - alpha) + stats.norm.ppf(power)) * se)


def power(se: float, gain: float, alpha: float = 0.05) -> float:
    """The probability that the test detects a given true gain.

    ``P(T > x0 | mean = gain) = 1 - Phi(x0 - gain / se)``, the part of the
    shifted normal distribution beyond the threshold.

    Args:
        se: The standard error of the mean difference.
        gain: The true mean gain, in the score's units.
        alpha: The Type-I error allowed for the decision.

    Returns:
        The power, between 0 and 1.
    """
    x0 = stats.norm.ppf(1 - alpha)
    return float(stats.norm.sf(x0 - gain / se))


@dataclass(frozen=True)
class PermutationTest:
    """The sign-flip permutation test of the mean paired difference.

    Attributes:
        draws: The number of random relabellings drawn.
        p: The fraction of relabellings whose mean is at or above the
            observed one, counting the observed labelling itself.
        alpha: The Type-I error level the decision was made at.
        accept: Whether ``p`` is at most ``alpha``.
    """

    draws: int
    p: float
    alpha: float
    accept: bool


def permutation_test(
    d: np.ndarray, alpha: float = 0.05, draws: int = 10_000, seed: int = 0
) -> PermutationTest:
    """Judge the mean paired difference against random relabellings of the sides.

    Under the null hypothesis the two harnesses' scores on a task are
    exchangeable, so each difference keeps its size and takes a random sign.
    The mean is recomputed over ``draws`` such relabellings and the p-value is
    the fraction at or above the observed mean, with the observed labelling
    counted among them so that the p-value is never zero. The test assumes
    nothing about the differences' distribution; with a fixed ``seed`` it is
    reproducible.

    Args:
        d: The per-task differences, candidate minus current.
        alpha: The Type-I error allowed for this decision.
        draws: The number of random relabellings.
        seed: The seed of the random relabellings.

    Returns:
        The p-value and verdict.
    """
    d = np.asarray(d, dtype=float)
    signs = np.random.default_rng(seed).choice([-1.0, 1.0], size=(draws, len(d)))
    means = (signs * d).mean(axis=1)
    p = (int(np.count_nonzero(means >= d.mean())) + 1) / (draws + 1)
    return PermutationTest(draws=draws, p=p, alpha=alpha, accept=p <= alpha)


@dataclass(frozen=True)
class Bootstrap:
    """The bootstrap spread of the mean paired difference.

    Attributes:
        draws: The number of resamples.
        se: The standard deviation of the resampled means.
        lower: The ``alpha`` quantile of the resampled means, a one-sided
            lower bound on the true mean at confidence ``1 - alpha``.
        alpha: The level of the lower bound.
    """

    draws: int
    se: float
    lower: float
    alpha: float


def bootstrap(
    d: np.ndarray, alpha: float = 0.05, draws: int = 10_000, seed: int = 0
) -> Bootstrap:
    """Resample the tasks with replacement to see how the mean difference spreads.

    Each resample draws ``len(d)`` tasks with replacement and takes their mean
    difference; ``se`` is the standard deviation of those means, a check on
    the analytic standard error of ``mean_test``, and ``lower`` their
    ``alpha`` quantile. With a fixed ``seed`` the result is reproducible.

    Args:
        d: The per-task differences, candidate minus current.
        alpha: One minus the confidence of the lower bound.
        draws: The number of resamples.
        seed: The seed of the resampling.

    Returns:
        The spread and the lower bound.
    """
    d = np.asarray(d, dtype=float)
    rows = np.random.default_rng(seed).integers(0, len(d), size=(draws, len(d)))
    means = d[rows].mean(axis=1)
    return Bootstrap(
        draws=draws,
        se=float(means.std(ddof=1)),
        lower=float(np.quantile(means, alpha)),
        alpha=alpha,
    )
