"""Unit tests for render_product_usage_trajectory (ADR-017 D5 Option C).

Tests verify the render function's output shape, edge cases, and the critical
determinism / symmetry property that makes auditor parity possible.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.pipeline.product_usage_render import render_product_usage_trajectory

_NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)

# Helper: build a minimal signal-like object accepted by the render function.
# The function uses getattr() only, so any object with the right attributes works.


def _signal(
    source_type: str = "product_event",
    occurred_at: datetime | str | None = None,
    author_contact_id: uuid.UUID | None = None,
    event_name: str | None = "feature_used",
) -> SimpleNamespace:
    if occurred_at is None:
        occurred_at = _NOW - timedelta(days=1)
    return SimpleNamespace(
        source_type=source_type,
        occurred_at=occurred_at,
        author_contact_id=author_contact_id or uuid.uuid5(uuid.NAMESPACE_DNS, "default"),
        event_name=event_name,
    )


# ---------------------------------------------------------------------------
# Test 9: No product events → empty string
# ---------------------------------------------------------------------------


def test_no_product_events_returns_empty_string() -> None:
    """When no product_event signals exist, the function returns ''."""
    email_sig = _signal(source_type="inbound_email")
    assert render_product_usage_trajectory([], _NOW) == ""
    assert render_product_usage_trajectory([email_sig], _NOW) == ""


def test_all_signals_outside_window_returns_empty_string() -> None:
    """Signals older than all cascade tiers are excluded; empty string if all out-of-window."""
    # Signal at 61 days is outside even the widest tier (60d) in the default cascade.
    old_sig = _signal(occurred_at=_NOW - timedelta(days=61))
    assert render_product_usage_trajectory([old_sig], _NOW) == ""
    # Explicit single-tier config: signal outside that tier also returns "".
    old_sig_7 = _signal(occurred_at=_NOW - timedelta(days=8))
    assert render_product_usage_trajectory([old_sig_7], _NOW, config={"window_days": 7}) == ""


# ---------------------------------------------------------------------------
# Test 10: Events in both windows → output contains all three headers
# ---------------------------------------------------------------------------


def test_both_windows_output_has_all_headers() -> None:
    """When both windows have events, output contains RECENT, PRIOR, and TRAJECTORY."""
    # window=7, mid=3.5d ago.  Early: 4d ago.  Recent: 1d ago.
    early_sig = _signal(occurred_at=_NOW - timedelta(days=4))
    recent_sig = _signal(occurred_at=_NOW - timedelta(days=1))
    result = render_product_usage_trajectory([early_sig, recent_sig], _NOW)
    assert "PRODUCT USAGE — RECENT WINDOW" in result
    assert "PRODUCT USAGE — PRIOR WINDOW" in result
    assert "TRAJECTORY:" in result


# ---------------------------------------------------------------------------
# Test 11: Events only in recent window
# ---------------------------------------------------------------------------


def test_events_only_in_recent_window() -> None:
    """Events only in recent half → 'no prior baseline' trajectory line."""
    recent_sig = _signal(occurred_at=_NOW - timedelta(days=1))
    result = render_product_usage_trajectory([recent_sig], _NOW)
    assert "no prior baseline" in result
    assert "PRODUCT USAGE — RECENT WINDOW" in result
    assert "PRODUCT USAGE — PRIOR WINDOW" in result
    # Prior window events should be 0
    all_lines = result.splitlines()
    prior_header_idx = next(
        (i for i, line in enumerate(all_lines) if "PRIOR WINDOW" in line), None
    )
    assert prior_header_idx is not None
    # The "Events:" line is the first line after the PRIOR WINDOW header
    prior_events_line = next(
        (line for line in all_lines[prior_header_idx:] if "Events:" in line), None
    )
    assert prior_events_line is not None
    assert "Events: 0" in prior_events_line


# ---------------------------------------------------------------------------
# Test 12: Events only in early window
# ---------------------------------------------------------------------------


def test_events_only_in_early_window() -> None:
    """Events only in prior half → 'account has gone quiet' trajectory line."""
    # window=7, mid=3.5d ago. Place event at 5 days ago (early half).
    early_sig = _signal(occurred_at=_NOW - timedelta(days=5))
    result = render_product_usage_trajectory([early_sig], _NOW)
    assert "account has gone quiet" in result
    # Recent window events should be 0
    all_lines = result.splitlines()
    recent_events_line = next(
        (line for line in all_lines if "Events:" in line),
        None,
    )
    assert recent_events_line is not None
    assert "Events: 0" in recent_events_line


# ---------------------------------------------------------------------------
# Test 13: Contact formatting and None author_contact_id handling
# ---------------------------------------------------------------------------


def test_none_author_contact_id_excluded_without_error() -> None:
    """Signals with author_contact_id=None do not raise and are excluded from count."""
    sig_with_contact = _signal(
        occurred_at=_NOW - timedelta(days=1),
        author_contact_id=uuid.uuid5(uuid.NAMESPACE_DNS, "real-contact"),
    )
    sig_without_contact = SimpleNamespace(
        source_type="product_event",
        occurred_at=_NOW - timedelta(days=1),
        author_contact_id=None,
        event_name="export_started",
    )
    result = render_product_usage_trajectory([sig_with_contact, sig_without_contact], _NOW)
    # Should not raise; contact count should be 1 (not 2, since one is None)
    assert "Distinct contacts: 1" in result


def test_string_occurred_at_is_parsed() -> None:
    """Signals from the audit harness (_SignalRow) have string occurred_at — must parse."""
    # Simulate the Postgres ISO timestamp format the audit harness sees
    occurred_str = "2026-05-06 12:00:00+00"
    sig = SimpleNamespace(
        source_type="product_event",
        occurred_at=occurred_str,
        author_contact_id=uuid.uuid5(uuid.NAMESPACE_DNS, "contact"),
        event_name="dashboard_viewed",
    )
    now = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)  # 1 day after occurred_at
    result = render_product_usage_trajectory([sig], now, config={"window_days": 7})
    # Signal is 1 day before now, within the 3.5-day recent window
    assert result != ""
    assert "PRODUCT USAGE — RECENT WINDOW" in result


# ---------------------------------------------------------------------------
# Test 14: Prompt symmetry — byte-identical output guarantee
# ---------------------------------------------------------------------------


def test_prompt_symmetry_byte_identical() -> None:
    """Asserts that render_product_usage_trajectory is pure and deterministic.

    The auditor calls this function with the same inputs as the narrative generator;
    byte-identical output is the contract. If the function ever acquires
    non-deterministic behavior (wall-clock calls, unsorted dict iteration, random
    sampling), this test will catch it.
    """
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "contact-1")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "contact-2")
    c3 = uuid.uuid5(uuid.NAMESPACE_DNS, "contact-3")

    # Early half (3.5d-7d ago): 6 events, 3 contacts
    # Recent half (0-3.5d ago): 4 events, 1 contact -- declining pattern
    _e = lambda c, d, ev: _signal(  # noqa: E731
        occurred_at=_NOW - timedelta(days=d), author_contact_id=c, event_name=ev
    )
    signals = [
        _e(c1, 5, "file_uploaded"),
        _e(c2, 4, "dashboard_viewed"),
        _e(c3, 4, "settings_updated"),
        _e(c1, 4, "comment_added"),
        _e(c2, 4, "feature_used"),
        _e(c3, 4, "export_started"),
        _e(c1, 2, "integration_connected"),
        _e(c1, 1, "integration_connected"),
        _e(c1, 1, "settings_updated"),
        _signal(occurred_at=_NOW - timedelta(hours=12), author_contact_id=c1),
    ]
    now = _NOW
    config = {"window_days": 7, "window_days_cascade": [7, 14, 30, 60]}

    first_call = render_product_usage_trajectory(signals, now, config)
    second_call = render_product_usage_trajectory(signals, now, config)

    assert first_call == second_call, (
        "render_product_usage_trajectory is not deterministic — "
        "narrative generator and auditor would see different context. "
        f"First call:\n{first_call}\n\nSecond call:\n{second_call}"
    )
    # Sanity-check that the output is non-empty (real signals produce real output)
    assert first_call != ""


# ---------------------------------------------------------------------------
# Additional: contact diversity trajectory line is present and accurate
# ---------------------------------------------------------------------------


def test_trajectory_line_shows_declining_diversity() -> None:
    """Trajectory line correctly describes contact diversity decline."""
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "c2")
    c3 = uuid.uuid5(uuid.NAMESPACE_DNS, "c3")
    c4 = uuid.uuid5(uuid.NAMESPACE_DNS, "c4")

    early_signals = [
        _signal(occurred_at=_NOW - timedelta(days=4), author_contact_id=c1),
        _signal(occurred_at=_NOW - timedelta(days=4, hours=1), author_contact_id=c2),
        _signal(occurred_at=_NOW - timedelta(days=5), author_contact_id=c3),
        _signal(occurred_at=_NOW - timedelta(days=5, hours=1), author_contact_id=c4),
    ]
    recent_signals = [
        _signal(occurred_at=_NOW - timedelta(days=1), author_contact_id=c1),
    ]

    result = render_product_usage_trajectory(early_signals + recent_signals, _NOW)
    # 4 early contacts → 1 recent contact: -75%
    assert "declined" in result
    assert "75%" in result or "-75" in result


def test_trajectory_line_shows_stable_diversity() -> None:
    """Trajectory line notes stable diversity when contact counts are equal."""
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "c2")

    early_signals = [
        _signal(occurred_at=_NOW - timedelta(days=4), author_contact_id=c1),
        _signal(occurred_at=_NOW - timedelta(days=5), author_contact_id=c2),
    ]
    recent_signals = [
        _signal(occurred_at=_NOW - timedelta(days=1), author_contact_id=c1),
        _signal(occurred_at=_NOW - timedelta(days=2), author_contact_id=c2),
    ]

    result = render_product_usage_trajectory(early_signals + recent_signals, _NOW)
    assert "stable" in result


# ---------------------------------------------------------------------------
# Cascade-specific trajectory tests (ADR-017 D5 amendment, D7 tests 17-20)
# ---------------------------------------------------------------------------


def test_cascade_fall_through_adds_note_line() -> None:
    """When a wider tier accepts, NOTE line appears describing the fall-through.

    Uses custom cascade [7, 30] (skipping 14) so T7 falls through (no events in last 7d)
    and T30 accepts with events in both T30 halves (25d ago = early, 10d ago = recent).
    10d ago is within T30 recent half (T30_mid = 15d ago; 10 < 15 → in recent half).
    """
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "c2")
    # Custom cascade skips T14 so T7 falls through (no events in last 7d),
    # and T30 accepts (both halves present).
    early_sig = _signal(occurred_at=_NOW - timedelta(days=25), author_contact_id=c1)
    recent_sig = _signal(occurred_at=_NOW - timedelta(days=10), author_contact_id=c2)
    config = {"window_days_cascade": [7, 30, 60]}

    result = render_product_usage_trajectory([early_sig, recent_sig], _NOW, config=config)
    assert result != ""
    assert "NOTE:" in result
    assert "no product events in the last 7 days" in result
    assert "scored from 30-day window" in result
    assert "PRODUCT USAGE — RECENT WINDOW" in result


def test_t1_accepts_no_note_line() -> None:
    """When T1 accepts (tightest tier), no NOTE line is rendered."""
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "c2")
    # T1 events: both halves have signals.
    early_sig = _signal(occurred_at=_NOW - timedelta(days=4), author_contact_id=c1)
    recent_sig = _signal(occurred_at=_NOW - timedelta(days=1), author_contact_id=c2)
    config = {"window_days_cascade": [7, 14, 30, 60]}

    result = render_product_usage_trajectory([early_sig, recent_sig], _NOW, config=config)
    assert result != ""
    assert "NOTE:" not in result
    assert "PRODUCT USAGE — RECENT WINDOW" in result


def test_cascade_symmetry_byte_identical() -> None:
    """Two calls with cascade config produce byte-identical output.

    Uses custom cascade [7, 30] so T7 falls through and T30 accepts.
    """
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "c2")
    # Signals in T30 range only; nothing in last 7d → T7 falls through; T30 accepts.
    _e = lambda c, d, ev: _signal(  # noqa: E731
        occurred_at=_NOW - timedelta(days=d), author_contact_id=c, event_name=ev
    )
    signals = [
        _e(c1, 25, "file_uploaded"),
        _e(c2, 22, "dashboard_viewed"),
        _e(c1, 10, "feature_used"),
        _e(c2, 8, "settings_updated"),
    ]
    config = {"window_days": 7, "window_days_cascade": [7, 30, 60]}

    first = render_product_usage_trajectory(signals, _NOW, config=config)
    second = render_product_usage_trajectory(signals, _NOW, config=config)
    assert first == second, (
        "render_product_usage_trajectory is not deterministic on cascade path. "
        f"First:\n{first}\n\nSecond:\n{second}"
    )
    assert first != ""
    assert "NOTE:" in first  # Confirms cascade path was exercised


def test_config_none_defaults_to_full_cascade() -> None:
    """config=None uses default cascade [7, 14, 30, 60]; same output as explicit default."""
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "c2")
    # Signals in T1 both halves — T1 should accept under both None and explicit config.
    early_sig = _signal(occurred_at=_NOW - timedelta(days=4), author_contact_id=c1)
    recent_sig = _signal(occurred_at=_NOW - timedelta(days=1), author_contact_id=c2)

    result_none = render_product_usage_trajectory([early_sig, recent_sig], _NOW, config=None)
    result_explicit = render_product_usage_trajectory(
        [early_sig, recent_sig], _NOW, config={"window_days_cascade": [7, 14, 30, 60]}
    )
    assert result_none == result_explicit
    assert result_none != ""
    assert "NOTE:" not in result_none  # T1 accepted; no fall-through annotation
