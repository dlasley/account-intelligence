"""
Engagement health unit tests — pure function, no DB, no API.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.config.schema import AccountHealthConfig, EngagementTierConfig, SentimentBandConfig
from src.domain.signal import Channel, Direction, Signal, SourceType
from src.pipeline.confidence import determine_account_health

_SENTIMENT_BANDS = [
    SentimentBandConfig(min_score=80, label="high"),
    SentimentBandConfig(min_score=60, label="good"),
    SentimentBandConfig(min_score=40, label="medium"),
    SentimentBandConfig(min_score=20, label="fair"),
    SentimentBandConfig(min_score=0, label="low"),
]


# Default config matching config/defaults.json
def _tier(name: str, score: int, min_signals: int, window_days: int, min_contacts: int):
    return EngagementTierConfig(
        name=name,
        score=score,
        min_signals=min_signals,
        window_days=window_days,
        min_contacts=min_contacts,
    )


DEFAULT_CONFIG = AccountHealthConfig(
    engagement_tiers=[
        _tier("high", 90, min_signals=5, window_days=14, min_contacts=2),
        _tier("good", 70, min_signals=3, window_days=14, min_contacts=2),
        _tier("medium", 50, min_signals=2, window_days=30, min_contacts=1),
        _tier("fair", 30, min_signals=1, window_days=30, min_contacts=1),
        _tier("low", 10, min_signals=0, window_days=30, min_contacts=0),
    ],
    sentiment_bands=_SENTIMENT_BANDS,
)

# Quantas Labs workspace config override
QUANTAS_LABS_CONFIG = AccountHealthConfig(
    engagement_tiers=[
        _tier("high", 90, min_signals=3, window_days=21, min_contacts=2),
        _tier("good", 70, min_signals=2, window_days=21, min_contacts=1),
        _tier("medium", 50, min_signals=1, window_days=30, min_contacts=1),
        _tier("fair", 30, min_signals=1, window_days=45, min_contacts=1),
        _tier("low", 10, min_signals=0, window_days=30, min_contacts=0),
    ],
    sentiment_bands=_SENTIMENT_BANDS,
)

_WS_ID = uuid4()
_ACCOUNT_ID = uuid4()


def _signal(days_ago: float, contact_id=None) -> Signal:
    now = datetime.now(UTC)
    occurred = now - timedelta(days=days_ago)
    return Signal(
        id=uuid4(),
        workspace_id=_WS_ID,
        account_id=_ACCOUNT_ID,
        source_type=SourceType.JSON_FIXTURE,
        external_id=f"test-{uuid4()}",
        thread_id=None,
        direction=Direction.INBOUND,
        channel=Channel.EMAIL,
        occurred_at=occurred,
        created_at=occurred,
        updated_at=occurred,
        subject="Test subject",
        body="Test body",
        author_contact_id=contact_id or uuid4(),
        recipient_contact_ids=[],
        routing_method=None,
        routing_confidence=None,
        routing_warning=None,
        deleted_at=None,
    )


# ---------------------------------------------------------------------------
# Parametrized tier cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "signals, config, expected_score, expected_tier",
    [
        # HIGH: 5 signals in 14 days, 2 contacts
        pytest.param(
            [_signal(1, uuid4()), _signal(2, uuid4()), _signal(3), _signal(5), _signal(7)],
            DEFAULT_CONFIG,
            90,
            "high",
            id="high_5_signals_14_days_2_contacts",
        ),
        # MEDIUM: 2 signals in 30 days, same contact (fails HIGH and MEDIUM_HIGH contact threshold)
        pytest.param(
            [_signal(10), _signal(20)],
            DEFAULT_CONFIG,
            50,
            "medium",
            id="medium_2_signals_same_contact",
        ),
        # LOW: 0 signals
        pytest.param(
            [],
            DEFAULT_CONFIG,
            10,
            "low",
            id="low_no_signals",
        ),
        # MEDIUM_LOW: 1 signal in 30 days (fails MEDIUM min_contacts=1 but passes MEDIUM_LOW)
        pytest.param(
            [_signal(5)],
            DEFAULT_CONFIG,
            30,
            "fair",
            id="fair_one_signal",
        ),
        # LOW: all signals outside 30-day window (Shionogi scenario)
        pytest.param(
            [_signal(35), _signal(40), _signal(50)],
            DEFAULT_CONFIG,
            10,
            "low",
            id="low_all_signals_outside_window",
        ),
    ],
)
def test_engagement_tier(signals, config, expected_score, expected_tier):
    result = determine_account_health(signals, config)
    assert result.score == expected_score
    assert result.tier_name == expected_tier


# ---------------------------------------------------------------------------
# Specific cases
# ---------------------------------------------------------------------------


def test_medium_when_high_window_has_too_few():
    """3 signals in 14 days doesn't reach HIGH (need 5); falls to MEDIUM_HIGH if contacts ≥ 2."""
    c1, c2 = uuid4(), uuid4()
    signals = [
        _signal(3, c1),
        _signal(7, c2),
        _signal(10, c1),  # in 14-day window
        _signal(20, c1),
        _signal(25, c2),  # only in 30-day window
    ]
    result = determine_account_health(signals, DEFAULT_CONFIG)
    # 3 signals in 14 days, 2 contacts → MEDIUM_HIGH (score 70)
    assert result.score == 70
    assert result.tier_name == "good"
    assert result.signal_count == 3


def test_high_tier_five_signals():
    c1, c2 = uuid4(), uuid4()
    signals = [
        _signal(1, c1),
        _signal(3, c2),
        _signal(5, c1),
        _signal(8, c2),
        _signal(12, c1),
    ]
    result = determine_account_health(signals, DEFAULT_CONFIG)
    assert result.score == 90
    assert result.tier_name == "high"
    assert result.signal_count == 5
    assert result.contact_count == 2
    assert "14 days" in result.rationale


def test_medium_tier_rationale():
    signals = [_signal(15), _signal(20)]
    result = determine_account_health(signals, DEFAULT_CONFIG)
    assert result.score == 50
    assert result.tier_name == "medium"
    assert "30 days" in result.rationale


def test_low_tier_no_signals_rationale():
    result = determine_account_health([], DEFAULT_CONFIG)
    assert result.score == 10
    assert result.tier_name == "low"
    assert "No signals in window" in result.rationale


def test_shionogi_low():
    """All signals > 30 days ago — falls outside both windows → LOW."""
    signals = [_signal(35), _signal(44), _signal(50), _signal(60)]
    result = determine_account_health(signals, DEFAULT_CONFIG)
    assert result.score == 10
    assert result.tier_name == "low"
    assert result.signal_count == 0  # none in 30-day window


def test_window_boundary_inclusive():
    """
    A signal at 13.9 days old is counted in the 14-day window.
    Moving it to 14.1 days drops it out, reducing the high-window count
    and causing the tier to fall from HIGH to lower.
    """
    c1, c2 = uuid4(), uuid4()
    base_signals = [_signal(1, c1), _signal(3, c2), _signal(6, c1), _signal(9, c2)]

    # With the 5th signal just inside the 14-day window → 5 signals in window → HIGH
    inside = determine_account_health([*base_signals, _signal(13.9, c2)], DEFAULT_CONFIG)
    assert inside.score == 90
    assert inside.tier_name == "high"

    # With the 5th signal just outside the 14-day window → only 4 in window → not HIGH
    outside = determine_account_health([*base_signals, _signal(14.1, c2)], DEFAULT_CONFIG)
    assert outside.score != 90
    assert outside.tier_name != "high"


def test_quantas_labs_config_lower_thresholds():
    """With Quantas Labs config (min_signals=3 for HIGH), 3 signals qualifies for HIGH."""
    c1, c2 = uuid4(), uuid4()
    signals = [_signal(5, c1), _signal(10, c2), _signal(15, c1)]
    result = determine_account_health(signals, QUANTAS_LABS_CONFIG)
    assert result.score == 90
    assert result.tier_name == "high"


def test_contact_count_in_result():
    c1, c2, c3 = uuid4(), uuid4(), uuid4()
    signals = [
        _signal(1, c1),
        _signal(3, c2),
        _signal(5, c3),
        _signal(8, c1),
        _signal(12, c2),
    ]
    result = determine_account_health(signals, DEFAULT_CONFIG)
    assert result.contact_count == 3


def test_frequency_multiplier_doubles_threshold():
    """
    With multiplier=2.0, the HIGH threshold (5 signals) doubles to 10.
    5 signals that would normally qualify for HIGH now fall to MEDIUM_HIGH.
    """
    c1, c2 = uuid4(), uuid4()
    signals = [_signal(1, c1), _signal(2, c2), _signal(4, c1), _signal(7, c2), _signal(10, c1)]
    # Without multiplier: 5 signals in 14 days, 2 contacts → HIGH
    result_default = determine_account_health(signals, DEFAULT_CONFIG)
    assert result_default.score == 90
    assert result_default.tier_name == "high"

    # With multiplier=2.0: effective_min_signals = max(1, round(5 * 2.0)) = 10 → not HIGH
    result_multiplied = determine_account_health(signals, DEFAULT_CONFIG, frequency_multiplier=2.0)
    assert result_multiplied.score != 90
    assert result_multiplied.tier_name != "high"


def test_frequency_multiplier_low_floors_at_one():
    """
    Without the max(1, ...) guard, round(1 * 0.1) = 0 — a tier with min_signals=1 would
    match even with 0 signals. The floor ensures at least 1 signal is always required.

    Verify with 0 signals and multiplier=0.1: result is still LOW (catch-all), not any
    higher tier whose effective threshold was rounded to 0 before the floor was applied.
    """
    # 0 signals — without floor, any tier whose round(min_signals * 0.1) == 0 would match.
    result_zero = determine_account_health([], DEFAULT_CONFIG, frequency_multiplier=0.1)
    assert result_zero.tier_name == "low"
    assert result_zero.score == 10

    # 1 signal — with floor applied, that same tier now requires 1 and this signal qualifies.
    c1 = uuid4()
    result_one = determine_account_health(
        [_signal(5, c1)], DEFAULT_CONFIG, frequency_multiplier=0.1
    )
    assert result_one.tier_name != "low"


def test_catchall_tier_immune_to_multiplier():
    """
    The catch-all tier (min_signals=0) must stay at 0 regardless of multiplier.
    An account with 0 signals always returns LOW regardless of how large the multiplier is.
    """
    result = determine_account_health([], DEFAULT_CONFIG, frequency_multiplier=5.0)
    assert result.tier_name == "low"
    assert result.score == 10
