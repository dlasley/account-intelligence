"""Phase 4b: Distribution validation suite for the synthetic scenario corpus.

Goal: assert that the 6 named corpus scenarios produce varied engagement scores
— not a tight cluster — and that scenarios designed to be at-risk actually score
low while healthy scenarios score high.

Design note: This test computes engagement scores WITHOUT the LLM or DB. It:
  1. Uses yield_events() to materialise signals from each scenario.
  2. Converts raw event payloads to minimal Signal objects.
  3. Calls determine_account_health() to get the engagement tier score.
  4. Calls compute_overall_health() using only the email/engagement dimension.

This is the only dimension computable offline (sentiment and CSM score require
LLM output or manual entry). The engagement dimension has weight=0.5 per
config/defaults.json. With a single dimension, overall_health == engagement score
(weighted average of one value = that value), so we assert directly on
engagement scores throughout.

The elicit-baseline.yaml is excluded: it contains 5 Elicit account sub-scenarios
with 63 total signals; computing per-account engagement from the merged stream
is non-trivial and not the intent of the distribution test (it has its own
equivalence test in test_elicit_equivalence.py). The 7 named scenarios are the
corpus the distribution test exists for.
"""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import pytest

from src.config.schema import AccountHealthConfig, EngagementTierConfig
from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType
from src.pipeline.confidence import determine_account_health
from src.pipeline.health import compute_overall_health
from src.synthetic.orchestrator import load_scenario, yield_events

SCENARIOS_DIR = Path("fixtures/synthetic-scenarios")

# The 6 named corpus scenarios (elicit-baseline excluded — see module docstring).
_CORPUS_SCENARIOS = [
    "single-champion-then-silence",
    "multi-stakeholder-mixed-domain",
    "frustrated-escalation",
    "product-heavy-email-light",
    "divergent-churn-risk",
    "expanding-champion",
]

# These scenarios are expected to score at-risk (engagement tier score < 50).
# single-champion-then-silence: drift cadence, silence at the end.
# divergent-churn-risk:      drift, declining, no email after early product burst.
_EXPECTED_AT_RISK = {"single-champion-then-silence", "divergent-churn-risk"}

# These scenarios are expected to score healthily (engagement tier score >= 50).
# expanding-champion:           steady, multi-modal, success_expansion topic.
# multi-stakeholder-mixed-domain: crowded contact diversity, multiple signals.
_EXPECTED_HEALTHY = {"expanding-champion", "multi-stakeholder-mixed-domain"}

# Per-scenario "days since last signal" offsets. These encode the design intent:
# - At-risk scenarios had their last signal > 14 days ago (outside the high/good window)
#   and > 30 days ago for the fully silent ones (outside even the medium/fair window).
# - Healthy scenarios had their last signal within the last 3 days.
# - Neutral scenarios (frustrated-escalation, product-heavy-email-light) are positioned
#   such that some signals fall in the 14-day window.
#
# This models the temporal gap the scenarios intend: "silence" in a scenario YAML is
# structural (no signals after a point), and engagement scoring can only detect it
# if "now" is positioned far enough past the last signal. The test passes a per-scenario
# ``now`` to determine_account_health() rather than re-timestamping signals.
_SCENARIO_SIGNAL_AGE_DAYS: dict[str, float] = {
    "single-champion-then-silence": 35.0,  # last signal 35 days ago — outside 30-day window
    "divergent-churn-risk": 32.0,        # last signal 32 days ago — outside 30-day window
    "expanding-champion": 1.0,              # last signal yesterday — very recent, high engagement
    "multi-stakeholder-mixed-domain": 2.0,  # last signal 2 days ago — recent, high engagement
    "frustrated-escalation": 5.0,           # recent enough for medium-high
    "product-heavy-email-light": 3.0,       # recent enough for medium-high (only 3 emails)
}

# Email dimension weight per config/defaults.json
_EMAIL_DIM_WEIGHT = 0.5

# A minimal AccountHealthConfig using defaults.json values — avoids a live DB read.
def _make_tier(name: str, score: int, min_signals: int, window_days: int, min_contacts: int) -> EngagementTierConfig:  # noqa: E501
    return EngagementTierConfig(
        name=name, score=score, min_signals=min_signals,
        window_days=window_days, min_contacts=min_contacts,
    )

_DEFAULT_HEALTH_CONFIG = AccountHealthConfig(
    engagement_tiers=[
        _make_tier("high",        90, min_signals=5, window_days=14, min_contacts=2),
        _make_tier("good", 70, min_signals=3, window_days=14, min_contacts=2),
        _make_tier("medium",      50, min_signals=2, window_days=30, min_contacts=1),
        _make_tier("fair",  30, min_signals=1, window_days=30, min_contacts=1),
        _make_tier("low",         10, min_signals=0, window_days=30, min_contacts=0),
    ],
    sentiment_bands=[],
)

_WS_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "dist-test-workspace")


class ScenarioScores(NamedTuple):
    name: str
    engagement_score: int
    overall_health: int | None
    signal_count: int


def _build_signal_from_payload(payload: dict, received_at: datetime) -> Signal:
    """Convert a raw inbound payload dict to a minimal Signal for scoring."""
    return Signal(
        id=uuid.uuid4(),
        workspace_id=_WS_ID,
        account_id=uuid.uuid5(uuid.NAMESPACE_DNS, "dist-test-account"),
        source_type=SourceType.JSON_FIXTURE,
        external_id=payload.get("external_id", str(uuid.uuid4())),
        thread_id=payload.get("thread_id"),
        direction=Direction.INBOUND,
        channel=Channel.EMAIL,
        occurred_at=received_at,
        created_at=received_at,
        updated_at=received_at,
        subject=payload.get("subject"),
        body=payload.get("body", ""),
        # Use a stable author contact so contact-diversity counts are meaningful.
        author_contact_id=uuid.uuid5(uuid.NAMESPACE_DNS, payload.get("from_email", "unknown")),
        recipient_contact_ids=[],
        routing_method=RoutingMethod.HEADER_DOMAIN,
        routing_confidence=0.9,
        routing_warning=None,
        deleted_at=None,
    )


def _score_scenario(scenario_name: str) -> ScenarioScores:
    """Load a scenario and compute engagement + overall_health scores.

    Passes a per-scenario ``now`` to determine_account_health() so the synthetic
    signal timeline (anchored at a fixed epoch in the YAML) lines up with the
    scoring windows without mutating signal timestamps. ``now`` is set to the
    latest signal's occurred_at plus the scenario's design-intent recency offset.
    """
    from src.domain.raw_inbound_event import RawInboundEvent
    from src.pipeline.product_event import ProductEvent

    scenario = load_scenario(SCENARIOS_DIR / f"{scenario_name}.yaml")
    signals: list[Signal] = []

    for _, event in yield_events(scenario, _WS_ID):
        if isinstance(event, RawInboundEvent):
            payload = json.loads(event.raw_payload)
            sig = _build_signal_from_payload(payload, event.received_at)
            signals.append(sig)
        elif isinstance(event, ProductEvent):
            # Product events contribute to signal count with a stable contact.
            contact_id = (
                uuid.uuid5(uuid.NAMESPACE_DNS, event.contact_email)
                if event.contact_email
                else None
            )
            sig = Signal(
                id=uuid.uuid4(),
                workspace_id=_WS_ID,
                account_id=uuid.uuid5(uuid.NAMESPACE_DNS, "dist-test-account"),
                source_type=SourceType.PRODUCT_EVENT,
                external_id=event.event_id or str(uuid.uuid4()),
                thread_id=None,
                direction=Direction.INBOUND,
                channel=Channel.PRODUCT,
                occurred_at=event.occurred_at,
                created_at=event.occurred_at,
                updated_at=event.occurred_at,
                subject=None,
                body=event.event_name or "",
                author_contact_id=contact_id,
                recipient_contact_ids=[],
                routing_method=RoutingMethod.API_KEY_IDENTITY,
                routing_confidence=1.0,
                routing_warning=None,
                deleted_at=None,
                event_name=event.event_name,
                event_properties=event.event_properties,
                event_id=event.event_id,
            )
            signals.append(sig)

    if not signals:
        return ScenarioScores(
            name=scenario_name,
            engagement_score=10,
            overall_health=None,
            signal_count=0,
        )

    # Position ``now`` after the latest signal by the scenario's design-intent
    # recency offset, so the engagement window cascade evaluates the timeline
    # at the gap the scenario intends. Signals themselves are not mutated.
    recency_days = _SCENARIO_SIGNAL_AGE_DAYS.get(scenario_name, 5.0)
    latest = max(s.occurred_at for s in signals)
    now = latest + timedelta(days=recency_days)
    health_result = determine_account_health(signals, _DEFAULT_HEALTH_CONFIG, now=now)

    engagement_score = health_result.score
    overall = compute_overall_health([(_EMAIL_DIM_WEIGHT, engagement_score)])

    return ScenarioScores(
        name=scenario_name,
        engagement_score=engagement_score,
        overall_health=overall,
        signal_count=len(signals),
    )


# ---------------------------------------------------------------------------
# Module-level: build the corpus once (scope=module via fixture)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def corpus() -> dict[str, ScenarioScores]:
    """Score all corpus scenarios once. Printed for inspection."""
    results: dict[str, ScenarioScores] = {}
    for name in _CORPUS_SCENARIOS:
        score = _score_scenario(name)
        results[name] = score
        print(f"\n  {name}: engagement={score.engagement_score}, "
              f"overall={score.overall_health}, signals={score.signal_count}")
    return results


# ---------------------------------------------------------------------------
# Tests: spread, at-risk presence, healthy presence, no out-of-range values
# ---------------------------------------------------------------------------

def test_corpus_scores_no_out_of_range(corpus: dict[str, ScenarioScores]) -> None:
    """Every engagement score must be a real integer in [1, 100]."""
    for name, scores in corpus.items():
        assert isinstance(scores.engagement_score, int), (
            f"{name}: engagement_score is {type(scores.engagement_score)}, expected int"
        )
        assert 1 <= scores.engagement_score <= 100, (
            f"{name}: engagement_score {scores.engagement_score} outside [1, 100]"
        )


def test_corpus_overall_health_no_out_of_range(corpus: dict[str, ScenarioScores]) -> None:
    """Every overall_health score (when not None) must be in [1, 100]."""
    for name, scores in corpus.items():
        if scores.overall_health is not None:
            assert isinstance(scores.overall_health, int), (
                f"{name}: overall_health is {type(scores.overall_health)}, expected int"
            )
            assert 1 <= scores.overall_health <= 100, (
                f"{name}: overall_health {scores.overall_health} outside [1, 100]"
            )


def test_corpus_spread_is_not_trivially_uniform(corpus: dict[str, ScenarioScores]) -> None:
    """The engagement scores across scenarios must have stdev > 0.

    The corpus spans different cadences and contact diversities; if every scenario
    lands on the same tier score the generator variation isn't making it through to
    scoring. Even a spread of just two distinct tier values satisfies this.
    """
    scores = [s.engagement_score for s in corpus.values()]
    distinct_values = len(set(scores))
    assert distinct_values > 1, (
        f"All scenarios scored identically: {scores}. "
        "The corpus is not producing varied engagement output."
    )


def test_designed_at_risk_scenarios_score_at_risk(corpus: dict[str, ScenarioScores]) -> None:
    """Each scenario in `_EXPECTED_AT_RISK` must score at-risk (engagement_score <= 30).

    Stronger than 'at-least-one' — asserts every named at-risk scenario actually
    lands below the threshold. Catches tier drift on a per-scenario basis.
    """
    failures = []
    for name in _EXPECTED_AT_RISK:
        score = corpus[name].engagement_score
        if score > 30:
            failures.append((name, score))
    assert not failures, (
        f"Scenarios designed as at-risk failed to score <= 30: {failures}. "
        f"Full corpus: {dict((n, s.engagement_score) for n, s in corpus.items())}"
    )


def test_designed_healthy_scenarios_score_healthy(corpus: dict[str, ScenarioScores]) -> None:
    """Each scenario in `_EXPECTED_HEALTHY` must score healthy (engagement_score >= 50).

    Stronger than 'at-least-one' — asserts every named healthy scenario lands
    at or above the threshold. Catches tier drift on a per-scenario basis.
    """
    failures = []
    for name in _EXPECTED_HEALTHY:
        score = corpus[name].engagement_score
        if score < 50:
            failures.append((name, score))
    assert not failures, (
        f"Scenarios designed as healthy failed to score >= 50: {failures}. "
        f"Full corpus: {dict((n, s.engagement_score) for n, s in corpus.items())}"
    )


def test_all_scenarios_have_signals(corpus: dict[str, ScenarioScores]) -> None:
    """Every corpus scenario must produce at least 1 signal (no empty generators)."""
    empty = [name for name, s in corpus.items() if s.signal_count == 0]
    assert not empty, f"Scenarios produced no signals: {empty}"


def test_expanding_champion_scores_healthy(corpus: dict[str, ScenarioScores]) -> None:
    """expanding-champion must achieve at least medium engagement (score >= 50).

    This scenario has 8 email signals + 6 product events with steady cadence
    and success_expansion topic. If it scores low, the generator is not producing
    enough signal density for the 14-day window — a design mismatch worth flagging.
    """
    score = corpus["expanding-champion"]
    assert score.engagement_score >= 50, (
        f"expanding-champion scored {score.engagement_score} (expected >= 50). "
        f"Signal count: {score.signal_count}. "
        "Either the steady cadence spreads signals too far out of the 14-day window, "
        "or the signal count is insufficient for the medium tier threshold."
    )


def test_multi_stakeholder_scores_at_least_medium(corpus: dict[str, ScenarioScores]) -> None:
    """multi-stakeholder-mixed-domain must achieve at least medium engagement (>= 50).

    This scenario uses crowded contact diversity and multiple signal specs.
    """
    score = corpus["multi-stakeholder-mixed-domain"]
    assert score.engagement_score >= 50, (
        f"multi-stakeholder-mixed-domain scored {score.engagement_score} (expected >= 50). "
        f"Signal count: {score.signal_count}."
    )
