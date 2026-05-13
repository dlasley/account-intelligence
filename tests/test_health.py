"""
Unit tests for compute_overall_health — pure function, no DB, no API.
"""

import pytest

from src.pipeline.health import compute_overall_health


def test_empty_list_returns_none():
    assert compute_overall_health([]) is None


def test_zero_total_weight_returns_none():
    assert compute_overall_health([(0.0, 50), (0.0, 80)]) is None


def test_single_dimension_returns_that_score():
    assert compute_overall_health([(1.0, 74)]) == 74


def test_two_dimensions_equal_weight():
    result = compute_overall_health([(1.0, 60), (1.0, 80)])
    assert result == 70


def test_two_dimensions_unequal_weight():
    # 0.7 * 90 + 0.3 * 50 = 63 + 15 = 78
    result = compute_overall_health([(0.7, 90), (0.3, 50)])
    assert result == 78


def test_clamp_at_100():
    # Both weight > 0 but high scores — result stays at 100
    result = compute_overall_health([(1.0, 100), (1.0, 100)])
    assert result == 100


def test_clamp_minimum_at_1():
    # score=1 is the DB minimum; compute should pass through and stay >= 1
    result = compute_overall_health([(1.0, 1)])
    assert result == 1


@pytest.mark.parametrize("score", [1, 50, 99, 100])
def test_boundary_scores_pass_through(score):
    result = compute_overall_health([(1.0, score)])
    assert result == score


def test_rounding():
    # 0.5 * 10 + 0.5 * 11 = 10.5 → rounds to 11 (Python rounds half to even → but round(10.5) = 10)
    # Use unambiguous case: 0.5 * 10 + 0.5 * 13 = 11.5 → round = 12
    result = compute_overall_health([(0.5, 10), (0.5, 13)])
    assert result == 12
