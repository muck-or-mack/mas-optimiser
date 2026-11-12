"""Unit tests of ``stats.budget``: the error level of one decision in a run."""

import pytest

from mas_optimiser.stats import budget


def test_reuse_level_matches_the_allocation_formula():
    assert budget.reuse_level(
        0.05, round_number=1, promotions=0, candidates=1
    ) == pytest.approx(0.0125)
    # w_2 = 1/6, v_1 = 1/6, C(1, 1) = 1, K^2 = 1
    assert budget.reuse_level(
        0.05, round_number=2, promotions=1, candidates=1
    ) == pytest.approx(0.05 / 36)
    assert budget.reuse_level(
        0.05, round_number=1, promotions=0, candidates=3
    ) == pytest.approx(0.0125 / 3)


def test_reuse_level_falls_with_rounds_promotions_and_candidates():
    base = budget.reuse_level(0.05, round_number=3, promotions=1, candidates=2)
    assert budget.reuse_level(0.05, round_number=4, promotions=1, candidates=2) < base
    assert budget.reuse_level(0.05, round_number=3, promotions=2, candidates=2) < base
    assert budget.reuse_level(0.05, round_number=3, promotions=1, candidates=3) < base


def test_reuse_levels_over_all_rounds_stay_within_the_budget():
    # With no promotions the levels over rounds sum to alpha * v_0 = alpha / 2.
    total = sum(
        budget.reuse_level(0.05, round_number=t, promotions=0, candidates=1)
        for t in range(1, 2001)
    )
    assert 0.0249 < total <= 0.025


def test_reuse_level_rejects_impossible_histories():
    with pytest.raises(ValueError, match="numbered from 1"):
        budget.reuse_level(0.05, round_number=0, promotions=0, candidates=1)
    with pytest.raises(ValueError, match="between 0 and round - 1"):
        budget.reuse_level(0.05, round_number=2, promotions=2, candidates=1)
    with pytest.raises(ValueError, match="at least one candidate"):
        budget.reuse_level(0.05, round_number=1, promotions=0, candidates=0)


def test_bonferroni_level_is_the_equal_split():
    assert budget.bonferroni_level(0.05, decisions=20) == pytest.approx(0.0025)
    assert budget.bonferroni_level(0.05, decisions=20, candidates=3) == pytest.approx(
        0.05 / 60
    )
    with pytest.raises(ValueError, match="at least one decision"):
        budget.bonferroni_level(0.05, decisions=0)


def test_fixed_level_is_the_one_sided_tail_beyond_sigma():
    assert budget.fixed_level(3) == pytest.approx(0.00135, rel=1e-2)
    assert budget.fixed_level(5) == pytest.approx(2.87e-7, rel=1e-2)
    assert budget.fixed_level(1.6449) == pytest.approx(0.05, rel=1e-3)
    with pytest.raises(ValueError, match="positive"):
        budget.fixed_level(0)
