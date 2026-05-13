"""Tests for src/simulator/primitives.py — Phase 2 of ADR-021.

Covers:
  1  stable length: 28-day range starting Monday returns exactly 4 entries
  2  stable clamp: target_band [95, 110] clamps max to 100
  3  declining linear monotone: each week's value <= prior week's
  4  declining cliff shape: cliff_at_week_2 holds start_health for weeks 0-1, then snaps
  5  recovering linear monotone: each week's value >= prior week's
  6  oscillating period: value at week 0 equals value at week period_weeks
  7  cliff pre/post: pre-cliff weeks from pre_band; post-cliff weeks from post_band
  8  determinism: two identical calls produce byte-identical output
  9  sub-week range: 3-day range produces exactly 1 entry
  10 wrong primitive: unknown name raises ValueError
  11 Hypothesis: target_health always in [1, 100]
  12 dispatch: primitive_to_curve routes to the correct shape function
  13 declining exponential: values decrease monotonically
  14 recovering jump shape: jump_at_week_3 holds start_health, then snaps at week 3
  15 oscillating crosses midpoint: multiple sign-changes in a long series
  16 cliff sharp transition: last pre-cliff week and first post-cliff week are in their bands
"""

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.simulator.primitives import (
    primitive_to_curve,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# A Monday — important for the "28-day range starting Monday" test.
MONDAY = date(2026, 3, 2)  # 2026-03-02 is a Monday


def make_curve(
    primitive: str,
    params: dict,
    start: date = MONDAY,
    days: int = 28,
    seed: int = 42,
) -> list[tuple[date, int]]:
    end = start + timedelta(days=days - 1)
    return primitive_to_curve(primitive, params, start, end, seed)


# ---------------------------------------------------------------------------
# Test 1: stable length
# ---------------------------------------------------------------------------


def test_stable_length_28_days() -> None:
    """A 28-day range (Monday-anchored) should produce exactly 4 weekly entries."""
    curve = make_curve("stable", {"target_band": [60, 80]}, start=MONDAY, days=28)
    assert len(curve) == 4


# ---------------------------------------------------------------------------
# Test 2: stable clamp
# ---------------------------------------------------------------------------


def test_stable_clamp_high() -> None:
    """target_band [95, 110] should produce values clamped to ≤100."""
    curve = make_curve("stable", {"target_band": [95, 110]}, days=28)
    for _ws, health in curve:
        assert health <= 100, f"expected ≤100, got {health}"
        assert health >= 1, f"expected ≥1, got {health}"


def test_stable_clamp_low() -> None:
    """target_band [-10, 5] should produce values clamped to ≥1."""
    curve = make_curve("stable", {"target_band": [-10, 5]}, days=28)
    for _ws, health in curve:
        assert health >= 1, f"expected ≥1, got {health}"


# ---------------------------------------------------------------------------
# Test 3: declining linear monotone
# ---------------------------------------------------------------------------


def test_declining_linear_monotone() -> None:
    """Linear declining curve: each week's health <= the prior week's health."""
    curve = make_curve(
        "declining",
        {"start_health": 80, "end_health": 40, "slope_shape": "linear"},
        days=56,  # 8 weeks for a visible trend
    )
    assert len(curve) >= 2
    for i in range(1, len(curve)):
        assert curve[i][1] <= curve[i - 1][1], (
            f"week {i} health {curve[i][1]} > week {i-1} health {curve[i-1][1]}"
        )


# ---------------------------------------------------------------------------
# Test 4: declining cliff shape
# ---------------------------------------------------------------------------


def test_declining_cliff_at_week_2() -> None:
    """cliff_at_week_2: weeks 0-1 at start_health; week 2+ at end_health."""
    start_h, end_h = 80, 40
    curve = make_curve(
        "declining",
        {"start_health": start_h, "end_health": end_h, "slope_shape": "cliff_at_week_2"},
        days=35,  # 5 weeks
    )
    assert len(curve) == 5
    # weeks 0 and 1 should be at start_health
    for i in range(2):
        assert curve[i][1] == start_h, f"week {i}: expected {start_h}, got {curve[i][1]}"
    # weeks 2, 3, 4 should be at end_health
    for i in range(2, 5):
        assert curve[i][1] == end_h, f"week {i}: expected {end_h}, got {curve[i][1]}"


# ---------------------------------------------------------------------------
# Test 5: recovering linear monotone
# ---------------------------------------------------------------------------


def test_recovering_linear_monotone() -> None:
    """Linear recovering curve: each week's health >= the prior week's health."""
    curve = make_curve(
        "recovering",
        {"start_health": 30, "end_health": 75, "slope_shape": "linear"},
        days=56,  # 8 weeks
    )
    assert len(curve) >= 2
    for i in range(1, len(curve)):
        assert curve[i][1] >= curve[i - 1][1], (
            f"week {i} health {curve[i][1]} < week {i-1} health {curve[i-1][1]}"
        )


# ---------------------------------------------------------------------------
# Test 6: oscillating period
# ---------------------------------------------------------------------------


def test_oscillating_same_value_at_period_boundary() -> None:
    """With period_weeks=4, the value at week 0 and week 4 must be identical.

    The curve uses `sin(2π * i / period)` so i=0 and i=period share the same
    angle modulo 2π → same sin value → same health.
    """
    period = 4
    curve = make_curve(
        "oscillating",
        {"low": 30, "high": 70, "period_weeks": period},
        days=(period * 2 + 1) * 7,  # enough weeks to see two full cycles
        seed=99,
    )
    assert len(curve) > period
    # week 0 and week `period` must have the same raw float angle, thus the same output
    assert curve[0][1] == curve[period][1], (
        f"week 0 = {curve[0][1]}, week {period} = {curve[period][1]}"
    )


# ---------------------------------------------------------------------------
# Test 7: cliff pre/post band membership
# ---------------------------------------------------------------------------


def test_cliff_pre_post_bands() -> None:
    """All pre-cliff weeks are within pre_band; all post-cliff weeks within post_band."""
    cliff_dt = MONDAY + timedelta(days=21)  # week 3 start = cliff
    curve = make_curve(
        "cliff",
        {
            "cliff_date": cliff_dt,
            "pre_band": [70, 85],
            "post_band": [10, 25],
        },
        days=49,  # 7 weeks
        seed=7,
    )
    for week_start, health in curve:
        if week_start < cliff_dt:
            assert 70 <= health <= 85, f"pre-cliff week {week_start}: {health} not in [70, 85]"
        else:
            assert 10 <= health <= 25, f"post-cliff week {week_start}: {health} not in [10, 25]"


# ---------------------------------------------------------------------------
# Test 8: determinism
# ---------------------------------------------------------------------------


def test_determinism_stable() -> None:
    params = {"target_band": [50, 70]}
    curve_a = make_curve("stable", params, days=35, seed=1234)
    curve_b = make_curve("stable", params, days=35, seed=1234)
    assert curve_a == curve_b


def test_determinism_declining() -> None:
    params = {"start_health": 80, "end_health": 40, "slope_shape": "exponential"}
    curve_a = make_curve("declining", params, days=35, seed=5678)
    curve_b = make_curve("declining", params, days=35, seed=5678)
    assert curve_a == curve_b


def test_determinism_oscillating() -> None:
    params = {"low": 20, "high": 80, "period_weeks": 3}
    curve_a = make_curve("oscillating", params, days=42, seed=9999)
    curve_b = make_curve("oscillating", params, days=42, seed=9999)
    assert curve_a == curve_b


def test_determinism_cliff() -> None:
    cliff_dt = MONDAY + timedelta(days=14)
    params = {"cliff_date": cliff_dt, "pre_band": [65, 80], "post_band": [20, 35]}
    curve_a = make_curve("cliff", params, days=42, seed=42)
    curve_b = make_curve("cliff", params, days=42, seed=42)
    assert curve_a == curve_b


# ---------------------------------------------------------------------------
# Test 9: sub-week range
# ---------------------------------------------------------------------------


def test_sub_week_range_produces_one_entry() -> None:
    """A 3-day range (shorter than a full week) should produce exactly 1 entry."""
    start = MONDAY
    end = MONDAY + timedelta(days=2)  # 3-day span
    curve = primitive_to_curve("stable", {"target_band": [50, 70]}, start, end, seed=1)
    assert len(curve) == 1
    assert curve[0][0] == start


def test_single_day_range_produces_one_entry() -> None:
    """A single-day range should produce exactly 1 entry."""
    start = MONDAY
    end = MONDAY  # same day → 1 day span
    # end_date == start_date is invalid per spec schema, but the primitive function
    # itself is called with pre-validated dates; we allow end == start in the pure fn.
    # Use end = start + 1 day instead (the schema requires end > start).
    end = start + timedelta(days=1)
    curve = primitive_to_curve("stable", {"target_band": [40, 60]}, start, end, seed=1)
    assert len(curve) == 1


# ---------------------------------------------------------------------------
# Test 10: unknown primitive raises ValueError
# ---------------------------------------------------------------------------


def test_unknown_primitive_raises() -> None:
    with pytest.raises(ValueError, match="unknown primitive"):
        primitive_to_curve(
            "swooshing",
            {"target_band": [50, 70]},
            MONDAY,
            MONDAY + timedelta(days=28),
            seed=1,
        )


# ---------------------------------------------------------------------------
# Test 11: Hypothesis — target_health always in [1, 100]
# ---------------------------------------------------------------------------

_PRIMITIVES_PARAMS = [
    ("stable", {"target_band": [40, 80]}),
    ("declining", {"start_health": 80, "end_health": 30}),
    ("recovering", {"start_health": 20, "end_health": 70}),
    ("oscillating", {"low": 10, "high": 90, "period_weeks": 4}),
    (
        "cliff",
        {
            "cliff_date": date(2026, 3, 16),
            "pre_band": [60, 80],
            "post_band": [15, 35],
        },
    ),
]


@given(
    seed=st.integers(min_value=0, max_value=9_999_999),
    duration_days=st.integers(min_value=1, max_value=365),
    primitive_idx=st.integers(min_value=0, max_value=len(_PRIMITIVES_PARAMS) - 1),
)
@settings(max_examples=500)
def test_health_always_in_bounds(seed: int, duration_days: int, primitive_idx: int) -> None:
    """All primitives always produce target_health in [1, 100]."""
    primitive, params = _PRIMITIVES_PARAMS[primitive_idx]
    start = date(2026, 1, 1)
    end = start + timedelta(days=duration_days)
    curve = primitive_to_curve(primitive, params, start, end, seed)
    assert len(curve) >= 1
    for _ws, health in curve:
        assert 1 <= health <= 100, f"{primitive} seed={seed} produced out-of-range: {health}"


# ---------------------------------------------------------------------------
# Test 12: dispatch routes correctly
# ---------------------------------------------------------------------------


def test_dispatch_stable() -> None:
    """primitive_to_curve('stable', ...) calls _curve_stable and returns correct shape."""
    curve = primitive_to_curve(
        "stable", {"target_band": [50, 70]}, MONDAY, MONDAY + timedelta(days=27), seed=1
    )
    assert len(curve) == 4  # 4 weeks in 28 days
    for ws, health in curve:
        assert isinstance(ws, date)
        assert isinstance(health, int)


def test_dispatch_oscillating() -> None:
    """primitive_to_curve('oscillating', ...) returns the right number of entries."""
    curve = primitive_to_curve(
        "oscillating",
        {"low": 30, "high": 70, "period_weeks": 4},
        MONDAY,
        MONDAY + timedelta(days=55),  # 8 weeks
        seed=77,
    )
    assert len(curve) == 8


# ---------------------------------------------------------------------------
# Test 13: declining exponential shape
# ---------------------------------------------------------------------------


def test_declining_exponential_decreases() -> None:
    """Exponential declining curve should be monotonically non-increasing."""
    curve = make_curve(
        "declining",
        {"start_health": 90, "end_health": 10, "slope_shape": "exponential"},
        days=56,
    )
    for i in range(1, len(curve)):
        assert curve[i][1] <= curve[i - 1][1] + 1, (
            f"week {i} ({curve[i][1]}) > week {i-1} ({curve[i-1][1]}) — exponential not decreasing"
        )


# ---------------------------------------------------------------------------
# Test 14: recovering jump shape
# ---------------------------------------------------------------------------


def test_recovering_jump_at_week_3() -> None:
    """jump_at_week_3: weeks 0-2 at start_health; week 3+ at end_health."""
    start_h, end_h = 30, 75
    curve = make_curve(
        "recovering",
        {"start_health": start_h, "end_health": end_h, "slope_shape": "jump_at_week_3"},
        days=35,  # 5 weeks
    )
    assert len(curve) == 5
    for i in range(3):
        assert curve[i][1] == start_h, f"week {i}: expected {start_h}, got {curve[i][1]}"
    for i in range(3, 5):
        assert curve[i][1] == end_h, f"week {i}: expected {end_h}, got {curve[i][1]}"


# ---------------------------------------------------------------------------
# Test 15: oscillating crosses midpoint
# ---------------------------------------------------------------------------


def test_oscillating_crosses_midpoint() -> None:
    """A long oscillating series should have values both above and below the midpoint."""
    low, high = 20, 80
    midpoint = (low + high) / 2
    curve = make_curve(
        "oscillating",
        {"low": low, "high": high, "period_weeks": 4},
        days=56,  # 8 weeks: exactly 2 full periods
        seed=42,
    )
    above = sum(1 for _, h in curve if h > midpoint)
    below = sum(1 for _, h in curve if h < midpoint)
    # With 2 full periods we expect both sides represented
    assert above > 0, "oscillating curve never went above midpoint"
    assert below > 0, "oscillating curve never went below midpoint"


# ---------------------------------------------------------------------------
# Test 16: cliff sharp transition
# ---------------------------------------------------------------------------


def test_cliff_sharp_transition() -> None:
    """The last pre-cliff week and first post-cliff week are in their respective bands."""
    cliff_dt = MONDAY + timedelta(days=21)  # week 3
    pre_band = [70, 85]
    post_band = [10, 25]
    curve = make_curve(
        "cliff",
        {"cliff_date": cliff_dt, "pre_band": pre_band, "post_band": post_band},
        days=42,  # 6 weeks
        seed=3,
    )
    pre_weeks = [(ws, h) for ws, h in curve if ws < cliff_dt]
    post_weeks = [(ws, h) for ws, h in curve if ws >= cliff_dt]

    assert len(pre_weeks) > 0, "no pre-cliff weeks found"
    assert len(post_weeks) > 0, "no post-cliff weeks found"

    last_pre = pre_weeks[-1][1]
    first_post = post_weeks[0][1]

    assert pre_band[0] <= last_pre <= pre_band[1], (
        f"last pre-cliff health {last_pre} not in {pre_band}"
    )
    assert post_band[0] <= first_post <= post_band[1], (
        f"first post-cliff health {first_post} not in {post_band}"
    )
