"""Trajectory primitive functions: (primitive, params, start_date, end_date, seed) -> health curve.

Each primitive is a deterministic pure function with no I/O and no datetime.now() calls.
See ADR-021 §D5 for the five primitives and their parameter contracts.
See ADR-021 §D7 for the determinism guarantee.
"""

from __future__ import annotations

import math
import random
import re
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Public data model
# ---------------------------------------------------------------------------


def primitive_to_curve(
    primitive: str,
    params: dict,  # TrajectoryParams.model_extra (or plain dict in tests)
    start_date: date,
    end_date: date,
    seed: int,
) -> list[tuple[date, int]]:
    """Return a list of (week_start, target_health) tuples, one per week.

    Week boundaries: the first week starts at start_date.  Each subsequent
    week starts 7 days later.  The final week may be shorter than 7 days if
    (end_date - start_date).days % 7 != 0; it is still emitted.

    target_health is clamped to [1, 100].

    Raises:
        ValueError: if `primitive` is not one of the five known names.
    """
    dispatch = {
        "stable": _curve_stable,
        "declining": _curve_declining,
        "recovering": _curve_recovering,
        "oscillating": _curve_oscillating,
        "cliff": _curve_cliff,
    }
    fn = dispatch.get(primitive)
    if fn is None:
        valid = sorted(dispatch)
        raise ValueError(f"unknown primitive {primitive!r}; valid: {valid}")
    return fn(start_date, end_date, params, seed)


# ---------------------------------------------------------------------------
# Week boundary helper
# ---------------------------------------------------------------------------


def _week_starts(start_date: date, end_date: date) -> list[date]:
    """Return the start date of every 7-day window within [start_date, end_date).

    The first element is always start_date.  The last element is the start of
    the final (possibly short) week.  end_date is inclusive: the final week
    covers up to and including end_date.
    """
    starts: list[date] = []
    current = start_date
    while current <= end_date:
        starts.append(current)
        current += timedelta(days=7)
    return starts


def _clamp(value: float | int) -> int:
    """Clamp a value to the integer range [1, 100]."""
    return max(1, min(100, round(value)))


# ---------------------------------------------------------------------------
# Primitive: stable
# ---------------------------------------------------------------------------


def _curve_stable(
    start_date: date,
    end_date: date,
    params: dict,
    seed: int,
) -> list[tuple[date, int]]:
    """Bounded random walk inside target_band.

    Each week is an independent seeded-random draw within [low, high].
    The walk is correlated: week N+1 is drawn from a sub-band centered on
    week N's value to keep adjacent weeks smooth.

    params:
        target_band: [low, high] (inclusive ints)
    """
    band: list[int] = params["target_band"]
    low, high = int(band[0]), int(band[1])
    # Clamp band itself to valid health range.
    low = max(1, low)
    high = min(100, high)
    if high < low:
        high = low

    starts = _week_starts(start_date, end_date)
    result: list[tuple[date, int]] = []

    # Correlation parameter: next week is drawn from a window of ±step around
    # the current value, clamped to [low, high].  step = max(1, band_width // 4)
    band_width = high - low
    step = max(1, band_width // 4)

    # Seed is stable per (params, start_date, end_date); each week derives its
    # own sub-seed as seed + week_index to avoid intra-band randomness that
    # accidentally correlates across entries sharing similar seeds.
    rng = random.Random(seed)
    current_value = rng.randint(low, high)

    for ws in starts:
        # Draw next value from a window around current_value, clamped to band.
        lo_sub = max(low, current_value - step)
        hi_sub = min(high, current_value + step)
        current_value = rng.randint(lo_sub, hi_sub)
        result.append((ws, _clamp(current_value)))

    return result


# ---------------------------------------------------------------------------
# Primitive: declining
# ---------------------------------------------------------------------------


def _parse_at_week_n(slope_shape: str, keyword: str) -> int | None:
    """Parse N from slope_shape strings like 'cliff_at_week_3' or 'jump_at_week_5'.

    Returns the integer N, or None if the string does not match.
    """
    pattern = rf"^{re.escape(keyword)}_at_week_(\d+)$"
    m = re.fullmatch(pattern, slope_shape)
    if m:
        return int(m.group(1))
    return None


def _curve_declining(
    start_date: date,
    end_date: date,
    params: dict,
    seed: int,
) -> list[tuple[date, int]]:
    """Interpolates from start_health down to end_health over the week range.

    params:
        start_health: int  (expected >= end_health)
        end_health: int
        slope_shape: 'linear' | 'exponential' | 'cliff_at_week_N'  (default 'linear')
    """
    start_h = int(params["start_health"])
    end_h = int(params["end_health"])
    slope_shape: str = str(params.get("slope_shape", "linear"))

    starts = _week_starts(start_date, end_date)
    n_weeks = len(starts)

    cliff_n = _parse_at_week_n(slope_shape, "cliff")

    result: list[tuple[date, int]] = []
    for i, ws in enumerate(starts):
        if n_weeks == 1:
            value = start_h
        elif cliff_n is not None:
            # Hold at start_health until cliff week, then snap to end_health.
            value = end_h if i >= cliff_n else start_h
        elif slope_shape == "exponential":
            if start_h == 0:
                value = 0.0
            else:
                # start_h * (end_h / start_h)^t, t in [0, 1]
                t = i / (n_weeks - 1)
                ratio = end_h / start_h if start_h != 0 else 0.0
                # Guard against log(0) when end_h == 0: treat as near-zero
                if ratio <= 0:
                    ratio = 0.001
                value = start_h * (ratio**t)
        else:  # linear (default)
            t = i / (n_weeks - 1)
            value = start_h + (end_h - start_h) * t

        result.append((ws, _clamp(value)))

    return result


# ---------------------------------------------------------------------------
# Primitive: recovering
# ---------------------------------------------------------------------------


def _curve_recovering(
    start_date: date,
    end_date: date,
    params: dict,
    seed: int,
) -> list[tuple[date, int]]:
    """Mirror of declining: interpolates from start_health up to end_health.

    params:
        start_health: int  (expected <= end_health)
        end_health: int
        slope_shape: 'linear' | 'exponential' | 'jump_at_week_N'  (default 'linear')
    """
    start_h = int(params["start_health"])
    end_h = int(params["end_health"])
    slope_shape: str = str(params.get("slope_shape", "linear"))

    starts = _week_starts(start_date, end_date)
    n_weeks = len(starts)

    jump_n = _parse_at_week_n(slope_shape, "jump")

    result: list[tuple[date, int]] = []
    for i, ws in enumerate(starts):
        if n_weeks == 1:
            value = start_h
        elif jump_n is not None:
            # Hold at start_health until jump week, then snap to end_health.
            value = end_h if i >= jump_n else start_h
        elif slope_shape == "exponential":
            # For recovering with exponential shape: same formula but in reverse.
            # Reflect around midpoint: use declining curve in reverse order.
            if end_h == 0:
                value = 0.0
            else:
                t = i / (n_weeks - 1)
                ratio = end_h / start_h if start_h != 0 else float("inf")
                if ratio <= 0:
                    ratio = 0.001
                if start_h == 0:
                    # Degenerate: start from near-zero
                    value = end_h * (0.001 ** (1 - t))
                else:
                    value = start_h * (ratio**t)
        else:  # linear (default)
            t = i / (n_weeks - 1)
            value = start_h + (end_h - start_h) * t

        result.append((ws, _clamp(value)))

    return result


# ---------------------------------------------------------------------------
# Primitive: oscillating
# ---------------------------------------------------------------------------


def _curve_oscillating(
    start_date: date,
    end_date: date,
    params: dict,
    seed: int,
) -> list[tuple[date, int]]:
    """Sinusoidal curve between low and high with period_weeks.

    Each week's target:
        low + (high - low) * 0.5 * (1 + sin(2π * week_index / period_weeks + phase_offset))

    The phase offset is seeded for variety across different entries while remaining
    deterministic given the same seed.

    params:
        low: int
        high: int
        period_weeks: int
    """
    low = int(params["low"])
    high = int(params["high"])
    period_weeks = int(params["period_weeks"])
    if period_weeks < 1:
        period_weeks = 1

    # Seeded phase offset in [0, 2π) — adds variety without breaking per-period repeat.
    rng = random.Random(seed)
    phase_offset = rng.uniform(0, 2 * math.pi)

    starts = _week_starts(start_date, end_date)
    result: list[tuple[date, int]] = []
    for i, ws in enumerate(starts):
        angle = 2 * math.pi * i / period_weeks + phase_offset
        raw = low + (high - low) * 0.5 * (1 + math.sin(angle))
        result.append((ws, _clamp(raw)))

    return result


# ---------------------------------------------------------------------------
# Primitive: cliff
# ---------------------------------------------------------------------------


def _curve_cliff(
    start_date: date,
    end_date: date,
    params: dict,
    seed: int,
) -> list[tuple[date, int]]:
    """Sharp transition at cliff_date: bounded random walk in pre_band before, post_band after.

    Weeks whose week_start is before cliff_date draw from pre_band.
    Weeks whose week_start is on or after cliff_date draw from post_band.
    Both bands use the correlated random walk pattern from _curve_stable.

    params:
        cliff_date: date (parsed from string if necessary)
        pre_band: [low, high]
        post_band: [low, high]
    """
    raw_cliff: object = params["cliff_date"]
    if isinstance(raw_cliff, date):
        cliff_dt = raw_cliff
    else:
        cliff_dt = date.fromisoformat(str(raw_cliff))

    pre_band: list[int] = params["pre_band"]
    post_band: list[int] = params["post_band"]

    pre_low, pre_high = max(1, int(pre_band[0])), min(100, int(pre_band[1]))
    post_low, post_high = max(1, int(post_band[0])), min(100, int(post_band[1]))
    if pre_high < pre_low:
        pre_high = pre_low
    if post_high < post_low:
        post_high = post_low

    # Separate seeded RNGs for pre and post bands so they don't interfere.
    pre_rng = random.Random(seed)
    post_rng = random.Random(seed + 1)

    pre_step = max(1, (pre_high - pre_low) // 4)
    post_step = max(1, (post_high - post_low) // 4)

    pre_current = pre_rng.randint(pre_low, pre_high)
    post_current = post_rng.randint(post_low, post_high)

    starts = _week_starts(start_date, end_date)
    result: list[tuple[date, int]] = []

    for ws in starts:
        if ws < cliff_dt:
            lo_sub = max(pre_low, pre_current - pre_step)
            hi_sub = min(pre_high, pre_current + pre_step)
            pre_current = pre_rng.randint(lo_sub, hi_sub)
            result.append((ws, _clamp(pre_current)))
        else:
            lo_sub = max(post_low, post_current - post_step)
            hi_sub = min(post_high, post_current + post_step)
            post_current = post_rng.randint(lo_sub, hi_sub)
            result.append((ws, _clamp(post_current)))

    return result
