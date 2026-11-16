"""The error budget over a run's decisions.

A run tests many candidates on the same validation tasks, and each decision
spends some of the run's overall Type-I error budget ``alpha``. ``reuse_level``
is the allocation of Sun et al. (arXiv 2609.33180, Eq. 4): it splits
``alpha`` over every decision the run could make, by round, by the number of
earlier promotions and by the candidates per round, so that with probability
at least ``1 - alpha`` every promotion in the run is a true improvement.
``bonferroni_level`` is the equal split over a number of decisions fixed in
advance, and ``fixed_level`` the same significance threshold at every decision
with no budget at all; both are kept beside it for comparison.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``stats``.
"""

from math import comb

from scipy import stats


def reuse_level(
    alpha: float, round_number: int, promotions: int, candidates: int
) -> float:
    """The Type-I error level of one decision under the REUSE allocation.

    ``alpha * w_t * v_p / (C(t - 1, p) * K^(p + 1))`` with ``w_t = 1 / (t (t + 1))``
    and ``v_p = 1 / ((p + 1)(p + 2))`` (Sun et al., arXiv 2609.33180, Eq. 4, with
    the paper's weights). The weights over rounds and over promotion counts
    each sum to one, and the binomial coefficient and the power of ``K`` cover
    every history of ``p`` promotions among ``t - 1`` earlier rounds with ``K``
    candidates each, so the levels of all possible decisions sum to at most
    ``alpha``. Later rounds and more promotions get smaller levels.

    Args:
        alpha: The run's overall error budget, between 0 and 1.
        round_number: The round number ``t``, from 1.
        promotions: The number of candidates promoted in earlier rounds, ``p``.
        candidates: The number of candidates tested in this round, ``K``.

    Returns:
        The level for each candidate's test in this round.

    Raises:
        ValueError: If ``round_number`` is below 1, ``promotions`` outside
            ``0 .. round_number - 1``, or ``candidates`` below 1.
    """
    if round_number < 1:
        raise ValueError("rounds are numbered from 1")
    if not 0 <= promotions <= round_number - 1:
        raise ValueError("promotions must lie between 0 and round - 1")
    if candidates < 1:
        raise ValueError("a round tests at least one candidate")
    w = 1 / (round_number * (round_number + 1))
    v = 1 / ((promotions + 1) * (promotions + 2))
    return (
        alpha
        * w
        * v
        / (comb(round_number - 1, promotions) * candidates ** (promotions + 1))
    )


def bonferroni_level(alpha: float, decisions: int, candidates: int = 1) -> float:
    """The equal split of the budget over a fixed number of decisions.

    ``alpha / (decisions * candidates)``: valid when the number of decisions
    is fixed before the run starts, and the same for every round.

    Args:
        alpha: The run's overall error budget, between 0 and 1.
        decisions: The number of rounds the run will make decisions in.
        candidates: The number of candidates tested in each round.

    Returns:
        The level for each candidate's test.

    Raises:
        ValueError: If ``decisions`` or ``candidates`` is below 1.
    """
    if decisions < 1 or candidates < 1:
        raise ValueError("the split needs at least one decision and one candidate")
    return alpha / (decisions * candidates)


def fixed_level(sigma: float = 3.0) -> float:
    """The Type-I error of a fixed one-sided significance threshold.

    ``1 - Phi(sigma)``: the level at which a decision requires the statistic
    to reach ``sigma`` standard deviations, the same at every round, so a
    run's overall error grows with its number of decisions. Three standard
    deviations is the usual threshold for evidence and five for discovery in
    particle physics; 1.645 is the level 0.05.

    Args:
        sigma: The threshold in standard deviations, positive.

    Returns:
        The level of each decision.

    Raises:
        ValueError: If ``sigma`` is not positive.
    """
    if sigma <= 0:
        raise ValueError("the threshold is a positive number of standard deviations")
    return float(stats.norm.sf(sigma))


def levels(
    alpha: float = 0.05,
    round_number: int = 1,
    promotions: int = 0,
    candidates: int = 1,
    decisions: int = 20,
    sigma: float = 3.0,
) -> dict[str, float]:
    """The decision rules shown side by side, each with the level it gives one decision.

    Args:
        alpha: The run's overall Type-I error budget, and the level of a
            decision taken on its own.
        round_number: The decision's round, for the REUSE allocation.
        promotions: The promotions before it, for the REUSE allocation.
        candidates: The candidates in the round, for the REUSE allocation.
        decisions: The number of decisions the equal split plans for.
        sigma: The fixed threshold, in standard deviations of a normal variable.

    Returns:
        Each rule's name, as the reports write it, to its level: one decision
        on its own, the REUSE allocation, the equal split and the fixed
        significance, in that order.
    """
    share = f"{100 * alpha:g}%"
    reuse = f"{share} shared over a run (REUSE), decision {round_number}"
    if promotions:
        reuse += f", after {promotions} accepted"
    if candidates > 1:
        reuse += f", {candidates} candidates a round"
    return {
        f"one decision at {share}": alpha,
        reuse: reuse_level(alpha, round_number, promotions, candidates),
        f"{share} shared equally over {decisions} decisions": bonferroni_level(
            alpha, decisions
        ),
        f"{sigma:g} sigma": fixed_level(sigma),
    }
