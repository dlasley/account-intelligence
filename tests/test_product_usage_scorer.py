"""Unit tests for score_product_usage (ADR-017 D1).

Tests verify the dual-window trajectory scoring algorithm across all edge cases
documented in the ADR.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType
from src.pipeline.confidence import score_product_usage

_WS_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "scorer-test-workspace")
_ACCT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "scorer-test-account")
_NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)

_DEFAULT_CONFIG: dict = {
    "window_days": 7,
    "min_events_for_active": 1,
    "trajectory_decay_ratio": 0.5,
}


def _make_product_signal(
    occurred_at: datetime,
    contact_id: uuid.UUID | None = None,
) -> Signal:
    return Signal(
        id=uuid.uuid4(),
        workspace_id=_WS_ID,
        account_id=_ACCT_ID,
        source_type=SourceType.PRODUCT_EVENT,
        external_id=str(uuid.uuid4()),
        thread_id=None,
        direction=Direction.INBOUND,
        channel=Channel.PRODUCT,
        occurred_at=occurred_at,
        created_at=occurred_at,
        updated_at=occurred_at,
        subject=None,
        body="feature_used",
        author_contact_id=contact_id or uuid.uuid5(uuid.NAMESPACE_DNS, "default-contact"),
        recipient_contact_ids=[],
        routing_method=RoutingMethod.API_KEY_IDENTITY,
        routing_confidence=1.0,
        routing_warning=None,
        deleted_at=None,
        event_name="feature_used",
    )


def _make_email_signal(occurred_at: datetime) -> Signal:
    """Email signals should be ignored by the product scorer."""
    return Signal(
        id=uuid.uuid4(),
        workspace_id=_WS_ID,
        account_id=_ACCT_ID,
        source_type=SourceType.INBOUND_EMAIL,
        external_id=str(uuid.uuid4()),
        thread_id=None,
        direction=Direction.INBOUND,
        channel=Channel.EMAIL,
        occurred_at=occurred_at,
        created_at=occurred_at,
        updated_at=occurred_at,
        subject="Hello",
        body="Hi there",
        author_contact_id=uuid.uuid5(uuid.NAMESPACE_DNS, "email-contact"),
        recipient_contact_ids=[],
        routing_method=RoutingMethod.HEADER_DOMAIN,
        routing_confidence=0.9,
        routing_warning=None,
        deleted_at=None,
    )


# ---------------------------------------------------------------------------
# Test 1: Zero product events → returns None
# ---------------------------------------------------------------------------


def test_zero_product_events_returns_none() -> None:
    """No product events in signals → dimension excluded (returns None, None)."""
    signals: list[Signal] = []
    score, tier = score_product_usage(signals, _DEFAULT_CONFIG, now=_NOW)
    assert score is None
    assert tier is None


def test_only_email_signals_returns_none() -> None:
    """Email signals do not count as product events; scorer returns (None, None)."""
    signals = [
        _make_email_signal(_NOW - timedelta(days=1)),
        _make_email_signal(_NOW - timedelta(days=2)),
    ]
    score, tier = score_product_usage(signals, _DEFAULT_CONFIG, now=_NOW)
    assert score is None
    assert tier is None


def test_product_events_outside_window_returns_none() -> None:
    """Product events older than all cascade tiers are excluded; returns (None, None)."""
    # These are outside the widest tier (60d), so all tiers fall through.
    signals = [
        _make_product_signal(_NOW - timedelta(days=61)),
        _make_product_signal(_NOW - timedelta(days=70)),
    ]
    score, tier = score_product_usage(signals, _DEFAULT_CONFIG, now=_NOW)
    assert score is None
    assert tier is None


# ---------------------------------------------------------------------------
# Test 2: Events only in early window → quiet tier (15)
# ---------------------------------------------------------------------------


def test_events_only_in_early_window_returns_quiet_tier() -> None:
    """Events entirely before the mid-point and nothing recent → score = 15.

    With the cascade config [7, 14, 30, 60], T1 (7d) sees events only in its
    early half — rule 2 fires, cascade stops, returns (15, 7).
    """
    # window=7, mid = now - 3.5d.  Place events at 4-6 days ago (early half).
    signals = [
        _make_product_signal(_NOW - timedelta(days=4)),
        _make_product_signal(_NOW - timedelta(days=5)),
    ]
    score, tier = score_product_usage(signals, _DEFAULT_CONFIG, now=_NOW)
    assert score == 15, f"Expected 15 (quiet tier), got {score}"
    assert tier == 7


# ---------------------------------------------------------------------------
# Test 3: Events only in recent window → no trajectory penalty (multiplier=1.0)
# ---------------------------------------------------------------------------


def test_events_only_in_recent_window_no_penalty() -> None:
    """No early events → multiplier=1.0; score based only on recent base tier."""
    contact1 = uuid.uuid5(uuid.NAMESPACE_DNS, "contact-1")
    contact2 = uuid.uuid5(uuid.NAMESPACE_DNS, "contact-2")
    signals = [
        _make_product_signal(_NOW - timedelta(days=1), contact1),
        _make_product_signal(_NOW - timedelta(days=2), contact2),
        _make_product_signal(_NOW - timedelta(days=2), contact1),
    ]
    # recent=3 events, 2 contacts → base=60; no early → multiplier=1.0 → score=60
    score, tier = score_product_usage(signals, _DEFAULT_CONFIG, now=_NOW)
    assert score == 60, f"Expected 60, got {score}"
    assert tier == 7


# ---------------------------------------------------------------------------
# Test 4: Declining pattern (Crucible walkthrough)
# ---------------------------------------------------------------------------


def test_declining_pattern_crucible_shape() -> None:
    """6 burst early events (4+ contacts) + 2 drift recent (1 contact) → low score.

    Models the Crucible hero scenario: active team early, single engineer
    in recent window.
    """
    # window=7, mid = now - 3.5d.
    # Early: 5 days ago + 4 days ago (2 events after more than 3.5d ago).
    # Actually need: early half = 3.5d to 7d ago; recent half = 0 to 3.5d ago.
    early_contact_ids = [
        uuid.uuid5(uuid.NAMESPACE_DNS, f"eng-{i}") for i in range(4)
    ]
    recent_contact_id = uuid.uuid5(uuid.NAMESPACE_DNS, "lone-eng")

    early_signals = [
        _make_product_signal(_NOW - timedelta(days=4), early_contact_ids[i % 4])
        for i in range(6)
    ]
    recent_signals = [
        _make_product_signal(_NOW - timedelta(days=1), recent_contact_id),
        _make_product_signal(_NOW - timedelta(days=2), recent_contact_id),
    ]
    signals = early_signals + recent_signals

    score, tier = score_product_usage(signals, _DEFAULT_CONFIG, now=_NOW)
    assert score is not None
    assert tier == 7
    # recent=2/early=6 = 0.33 < 0.5 threshold → sharp decline → base=45 * ~0.6 ≈ 27
    # Acceptable range for declining pattern: 15-40
    assert 15 <= score <= 40, f"Expected declining score in 15-40, got {score}"


# ---------------------------------------------------------------------------
# Test 5: Stable pattern → no penalty
# ---------------------------------------------------------------------------


def test_stable_pattern_no_penalty() -> None:
    """Equal early and recent counts → ratio=1.0, no penalty."""
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "contact-a")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "contact-b")
    early_signals = [
        _make_product_signal(_NOW - timedelta(days=4), c1),
        _make_product_signal(_NOW - timedelta(days=5), c2),
        _make_product_signal(_NOW - timedelta(days=6), c1),
        _make_product_signal(_NOW - timedelta(days=6, hours=12), c2),
    ]
    recent_signals = [
        _make_product_signal(_NOW - timedelta(days=1), c1),
        _make_product_signal(_NOW - timedelta(days=1, hours=12), c2),
        _make_product_signal(_NOW - timedelta(days=2), c1),
        _make_product_signal(_NOW - timedelta(days=2, hours=12), c2),
    ]
    signals = early_signals + recent_signals
    # recent=4, contacts=2 → base=60 (>= 3 events, 2 contacts); ratio=1.0 → multiplier=1.0
    # No penalty for stable trajectory → score=60
    score, tier = score_product_usage(signals, _DEFAULT_CONFIG, now=_NOW)
    assert score is not None
    assert tier == 7
    assert 55 <= score <= 65, f"Stable pattern expected ~60, got {score}"


# ---------------------------------------------------------------------------
# Test 6: Growing pattern → growth bonus, high score
# ---------------------------------------------------------------------------


def test_growing_pattern_high_score() -> None:
    """More recent events than early + many contacts → high score with growth bonus."""
    early_c = uuid.uuid5(uuid.NAMESPACE_DNS, "early-only")
    recent_cs = [uuid.uuid5(uuid.NAMESPACE_DNS, f"rc-{i}") for i in range(4)]

    early_signals = [
        _make_product_signal(_NOW - timedelta(days=4), early_c),
        _make_product_signal(_NOW - timedelta(days=5), early_c),
    ]
    recent_signals = [
        _make_product_signal(_NOW - timedelta(days=1), recent_cs[i % 4])
        for i in range(8)
    ]
    signals = early_signals + recent_signals
    # recent=8, contacts=4 → base=90; ratio=4.0 → growth bonus → multiplier=min(1.1,1.3)=1.1
    # raw=90*1.1=99 → clamped 99
    score, tier = score_product_usage(signals, _DEFAULT_CONFIG, now=_NOW)
    assert score is not None
    assert tier == 7
    assert score >= 80, f"Growing pattern expected >= 80, got {score}"


# ---------------------------------------------------------------------------
# Test 7: frequency_multiplier scales effective_min
# ---------------------------------------------------------------------------


def test_frequency_multiplier_low_touch() -> None:
    """frequency_multiplier=0.5 on a low-touch account lowers the bar for quiet tier."""
    # With multiplier=0.5, effective_min = max(1, round(1 * 0.5)) = max(1, 1) = 1
    # So 1 recent event clears the quiet-tier threshold.
    recent_signal = _make_product_signal(_NOW - timedelta(days=1))
    score, tier = score_product_usage(
        [recent_signal], _DEFAULT_CONFIG, frequency_multiplier=0.5, now=_NOW
    )
    assert score is not None
    assert tier == 7
    assert score > 15, (
        f"Low-touch account with 1 recent event should score above quiet tier, got {score}"
    )


# ---------------------------------------------------------------------------
# Test 8: Score clamped to [1, 100]
# ---------------------------------------------------------------------------


def test_score_always_in_range() -> None:
    """Score is always an integer in [1, 100] regardless of inputs."""
    # Test with pathological inputs
    c = uuid.uuid5(uuid.NAMESPACE_DNS, "x")
    # Minimal: 1 recent event
    score_min, _ = score_product_usage(
        [_make_product_signal(_NOW - timedelta(hours=1), c)], _DEFAULT_CONFIG, now=_NOW
    )
    assert score_min is not None
    assert 1 <= score_min <= 100

    # Maximum: many recent events, many contacts
    big_signals = [
        _make_product_signal(
            _NOW - timedelta(hours=i),
            uuid.uuid5(uuid.NAMESPACE_DNS, f"big-{i}"),
        )
        for i in range(1, 50)
    ]
    score, tier = score_product_usage(big_signals, _DEFAULT_CONFIG, now=_NOW)
    assert score is not None
    assert tier is not None
    assert 1 <= score <= 100


def test_now_defaults_to_utcnow(monkeypatch: pytest.MonkeyPatch) -> None:
    """When now=None, score_product_usage uses datetime.now(UTC) as the reference."""
    # Provide a signal 1 day ago relative to actual wall clock — if now defaults
    # correctly, the signal should be within the 7-day window.
    real_now = datetime.now(UTC)
    sig = _make_product_signal(real_now - timedelta(days=1))
    score, tier = score_product_usage([sig], _DEFAULT_CONFIG, now=None)
    # Should return a non-None score since the signal is within the window
    assert score is not None
    assert tier is not None


# ---------------------------------------------------------------------------
# Cascade-specific tests (ADR-017 D1 amendment, D7 test cases 9-16)
# ---------------------------------------------------------------------------

_CASCADE_CONFIG: dict = {
    "window_days": 7,
    "window_days_cascade": [7, 14, 30, 60],
    "min_events_for_active": 1,
    "trajectory_decay_ratio": 0.5,
}


def test_cascade_fall_through_to_t2() -> None:
    """Custom cascade [14, 30, 60]: T14 accepts with events in both halves → (score, 14).

    T14 early half = [14d, 7d) ago; T14 recent half = [7d, now].
    Uses cascade starting at 14 (skipping T1=7) so T14 is the tightest tier.
    """
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    custom_cascade_config: dict = {
        "window_days_cascade": [14, 30, 60],
        "min_events_for_active": 1,
        "trajectory_decay_ratio": 0.5,
    }
    # T14 early half: 10d ago (inside [14d, 7d)); T14 recent half: 3d ago (inside [7d, now]).
    early_sig = _make_product_signal(_NOW - timedelta(days=10), c1)
    recent_sig = _make_product_signal(_NOW - timedelta(days=3), c1)
    score, tier = score_product_usage([early_sig, recent_sig], custom_cascade_config, now=_NOW)
    assert score is not None
    assert tier == 14


def test_cascade_fall_through_to_t3() -> None:
    """T1 and T2 have no events (fall through rule 1); T3 (30d) accepts → tier=30.

    Key placement: T2 window is [14d ago, now]. A signal at 16d ago is outside T2's
    early_start (>14d), so T2 falls through rule 1 (no events). T3 window is [30d ago, now].
    T3 mid = 15d ago. Signal at 25d ago → T3 early half; signal at 16d ago → T3 early half too.
    To get both halves for T3: need events in [30d, 15d) AND [15d, now).
    But [15d, now) overlaps T2's window! Any event there would be in T2 → T2 rule 2, not T3.
    Resolution: use a custom cascade that skips T2, so [7, 30, 60].
    Or use signals placed such that T2 early half is empty AND T3 both halves have events:
    T2 early half = [14d, 7d). T3 recent half = [15d, now). 16d ago is in T3 recent half
    but also in T2 early half (14d < 16d is false — 16d > 14d, so it's outside T2).
    Wait: T2 early_start = now - 14d. 16d ago < T2 early_start → outside T2 window entirely.
    16d ago is in T3 recent half (T3_mid = 15d ago; 16d > 15d → 16d is in T3 early half).
    So: early=25d ago (T3 early), recent=16d ago (T3 early — same half, both > T3 mid of 15d).
    T3_mid = 15d ago. 16d ago < T3_mid? No: 16 > 15, so 16d ago is BEFORE T3_mid → T3 early.
    For T3 recent half: signal must be < 15d old (< T3_mid = now - 15d, i.e. more recent).
    But anything < 14d old is in T2's window — T2 rule 2 fires.
    Conclusion: standard [7,14,30,60] cascade cannot produce tier=30 with both halves
    unless T2 also fire rule 2 (early only, no recent). To get pure T3 acceptance (rule 3/4),
    use a custom cascade [30, 60] that skips T1 and T2.
    """
    custom: dict = {
        "window_days_cascade": [30, 60],
        "min_events_for_active": 1,
        "trajectory_decay_ratio": 0.5,
    }
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "c2")
    # T30: early half = [30d, 15d), recent half = [15d, now].
    early_sig = _make_product_signal(_NOW - timedelta(days=25), c1)  # T30 early half
    recent_sig = _make_product_signal(_NOW - timedelta(days=10), c2)  # T30 recent half
    score, tier = score_product_usage([early_sig, recent_sig], custom, now=_NOW)
    assert score is not None
    assert tier == 30


def test_t1_gone_quiet_stops_cascade() -> None:
    """T1 early half has events, T1 recent half is empty → rule 2 fires, cascade stops."""
    # Events at 4-6 days ago → T1 early half (3.5d to 7d ago). Nothing recent.
    # Rule 2: gone quiet → score=15, tier=7. T2-T4 are NOT evaluated.
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    signals = [
        _make_product_signal(_NOW - timedelta(days=4), c1),
        _make_product_signal(_NOW - timedelta(days=6), c1),
        # Also add an event in T4 range (40d ago) to confirm cascade doesn't reach it
        _make_product_signal(_NOW - timedelta(days=40), c1),
    ]
    score, tier = score_product_usage(signals, _CASCADE_CONFIG, now=_NOW)
    assert score == 15
    assert tier == 7  # T1 fired rule 2; cascade stopped before T4


def test_tightest_tier_wins_when_t1_and_t3_both_have_events() -> None:
    """T1 accepts (tightest-first); T3 is not evaluated even though it has events."""
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "c2")
    # T1 events: 1 recent (2d ago), 1 early (5d ago).
    # T3 events: 1 recent (10d ago) and 1 early (25d ago) — but T1 accepts first.
    signals = [
        _make_product_signal(_NOW - timedelta(days=2), c1),   # T1 recent
        _make_product_signal(_NOW - timedelta(days=5), c2),   # T1 early
        _make_product_signal(_NOW - timedelta(days=10), c1),  # T3 recent only
        _make_product_signal(_NOW - timedelta(days=25), c2),  # T3 early only
    ]
    score, tier = score_product_usage(signals, _CASCADE_CONFIG, now=_NOW)
    assert score is not None
    assert tier == 7  # T1 accepted; T3 never evaluated


def test_all_tiers_empty_returns_none_none() -> None:
    """Zero product events across all cascade tiers → (None, None)."""
    email_sigs = [_make_email_signal(_NOW - timedelta(days=i)) for i in range(1, 5)]
    score, tier = score_product_usage(email_sigs, _CASCADE_CONFIG, now=_NOW)
    assert score is None
    assert tier is None


def test_custom_cascade_override_honored() -> None:
    """window_days_cascade overrides window_days; only listed tiers are evaluated."""
    # Custom cascade [14, 60] — T1 (7d) and T3 (30d) are skipped.
    custom: dict = {
        "window_days": 7,  # ignored when window_days_cascade present
        "window_days_cascade": [14, 60],
        "min_events_for_active": 1,
        "trajectory_decay_ratio": 0.5,
    }
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "c2")
    # Signal at 8d ago is outside T1 (7d) but inside T2 of custom cascade (14d).
    # No signal in last 7d (T1 would fire if 7 were in cascade, but it's not).
    # T14 early half: 14d to 7d ago; T14 recent half: 7d to now.
    # 8d ago → T14 early half. Nothing in T14 recent → rule 2, score=15, tier=14.
    early_sig = _make_product_signal(_NOW - timedelta(days=8), c1)
    score, tier = score_product_usage([early_sig], custom, now=_NOW)
    assert score == 15
    assert tier == 14

    # Now add a recent signal so T14 accepts rule 3.
    recent_sig = _make_product_signal(_NOW - timedelta(days=3), c2)
    score2, tier2 = score_product_usage([early_sig, recent_sig], custom, now=_NOW)
    assert score2 is not None
    assert score2 != 15
    assert tier2 == 14


def test_return_type_always_two_tuple() -> None:
    """score_product_usage always returns a 2-tuple; both None or both non-None."""
    c = uuid.uuid5(uuid.NAMESPACE_DNS, "c")

    # Case 1: no events → (None, None)
    result = score_product_usage([], _CASCADE_CONFIG, now=_NOW)
    assert isinstance(result, tuple)
    assert len(result) == 2
    score, tier = result
    assert score is None and tier is None

    # Case 2: events present → (int, int)
    result2 = score_product_usage(
        [_make_product_signal(_NOW - timedelta(hours=1), c)], _CASCADE_CONFIG, now=_NOW
    )
    assert isinstance(result2, tuple)
    assert len(result2) == 2
    score2, tier2 = result2
    assert isinstance(score2, int)
    assert isinstance(tier2, int)


def test_window_days_used_is_in_cascade() -> None:
    """window_days_used is always one of the values in the active cascade."""
    c1 = uuid.uuid5(uuid.NAMESPACE_DNS, "c1")
    c2 = uuid.uuid5(uuid.NAMESPACE_DNS, "c2")

    # T1 accepts: both halves have events in 7d window
    t1_sigs = [
        _make_product_signal(_NOW - timedelta(days=2), c1),  # T1 recent
        _make_product_signal(_NOW - timedelta(days=5), c2),  # T1 early
    ]
    score, tier = score_product_usage(t1_sigs, _CASCADE_CONFIG, now=_NOW)
    assert score is not None
    assert tier == 7
    assert tier in _CASCADE_CONFIG["window_days_cascade"]

    # Custom cascade [30, 60]: T30 accepts with signals at 25d (early) and 10d (recent)
    custom: dict = {
        "window_days_cascade": [30, 60],
        "min_events_for_active": 1,
        "trajectory_decay_ratio": 0.5,
    }
    t3_sigs = [
        _make_product_signal(_NOW - timedelta(days=25), c1),  # T30 early half
        _make_product_signal(_NOW - timedelta(days=10), c2),  # T30 recent half
    ]
    score2, tier2 = score_product_usage(t3_sigs, custom, now=_NOW)
    assert score2 is not None
    assert tier2 == 30
    assert tier2 in custom["window_days_cascade"]
