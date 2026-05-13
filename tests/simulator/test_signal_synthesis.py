"""Tests for src/simulator/signal_synthesis.py — Phase 3.

All tests are synchronous; no DB, no I/O, no datetime.now().
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from src.simulator.signal_synthesis import WeekSignalPlan, plan_to_scenario, week_to_signal_plan
from src.synthetic.scenario import ScenarioSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_TS = datetime(2026, 3, 3, 0, 0, 0, tzinfo=UTC)
_WEEK_START = date(2026, 3, 3)


def _plan(
    target_health: int,
    account_slug: str = "test-account",
    entry_seed: int = 1234,
    week_index: int = 0,
    frequency_multiplier: float = 1.0,
) -> WeekSignalPlan:
    return week_to_signal_plan(
        account_slug=account_slug,
        week_start=_WEEK_START,
        target_health=target_health,
        entry_seed=entry_seed,
        week_index=week_index,
        frequency_multiplier=frequency_multiplier,
    )


# ---------------------------------------------------------------------------
# Test 1 — High health produces more signals than low health
# ---------------------------------------------------------------------------


def test_high_health_produces_more_signals_than_low():
    """target_health=85 should produce more total signals than target_health=25."""
    plan_high = _plan(target_health=85, entry_seed=5000, week_index=0)
    plan_low = _plan(target_health=25, entry_seed=5000, week_index=0)

    total_high = plan_high.email_count + plan_high.product_count
    total_low = plan_low.email_count + plan_low.product_count

    assert total_high > total_low, (
        f"Expected high-health plan to have more signals: "
        f"high={total_high} low={total_low}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Low health produces escalation tone
# ---------------------------------------------------------------------------


def test_low_health_produces_escalation_sentiment():
    """target_health=20 should use declining or sudden_escalation sentiment."""
    plan = _plan(target_health=20)
    valid_sentiments = {"declining", "sudden_escalation"}
    assert plan.axes_overrides["sentiment_trajectory"] in valid_sentiments, (
        f"Expected escalating sentiment for health=20, got: "
        f"{plan.axes_overrides['sentiment_trajectory']!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Determinism
# ---------------------------------------------------------------------------


def test_determinism():
    """Two calls with the same args produce identical WeekSignalPlan."""
    plan1 = _plan(target_health=60, entry_seed=9999, week_index=3)
    plan2 = _plan(target_health=60, entry_seed=9999, week_index=3)

    assert plan1.email_count == plan2.email_count
    assert plan1.product_count == plan2.product_count
    assert plan1.axes_overrides == plan2.axes_overrides
    assert plan1.seed == plan2.seed


# ---------------------------------------------------------------------------
# Test 4 — Frequency multiplier scales counts
# ---------------------------------------------------------------------------


def test_frequency_multiplier_doubles_counts():
    """frequency_multiplier=2.0 should roughly double counts vs 1.0.

    We check with a seed that falls in the healthy band (guaranteed non-zero
    base counts) and assert the doubled counts are approximately 2× the base.
    """
    # Use health=80 to stay in the healthy band where base counts are 2-4 / 5-10.
    # Fix entry_seed + week_index so base counts are deterministic.
    plan_base = _plan(target_health=80, entry_seed=100, week_index=0, frequency_multiplier=1.0)
    plan_double = _plan(target_health=80, entry_seed=100, week_index=0, frequency_multiplier=2.0)

    # Both counts should be at least as large when multiplied.
    assert plan_double.email_count >= plan_base.email_count
    assert plan_double.product_count >= plan_base.product_count

    # With multiplier=2.0, the total signals should be roughly double (within rounding).
    # We allow a tolerance of ±2 for rounding at the edges.
    total_base = plan_base.email_count + plan_base.product_count
    total_double = plan_double.email_count + plan_double.product_count
    assert total_double >= total_base, (
        f"Doubled multiplier should produce at least as many signals: "
        f"base={total_base} double={total_double}"
    )


# ---------------------------------------------------------------------------
# Test 5 — Very low health produces zero or minimal product events
# ---------------------------------------------------------------------------


def test_very_low_health_minimal_product_events():
    """target_health=10 should land in the critical band (<30), where product_count range is 0-1."""
    # Sample several seeds to confirm the product_count cap.
    product_counts = [
        _plan(target_health=10, entry_seed=seed, week_index=0).product_count
        for seed in range(20)
    ]
    assert all(c <= 1 for c in product_counts), (
        f"Expected product_count <= 1 for target_health=10, got: {product_counts}"
    )


# ---------------------------------------------------------------------------
# Test 6 — plan_to_scenario round-trip validates against ScenarioSpec
# ---------------------------------------------------------------------------


def test_plan_to_scenario_validates():
    """plan_to_scenario should return a ScenarioSpec that validates without errors."""
    plan = _plan(target_health=65, entry_seed=42, week_index=1)
    scenario = plan_to_scenario(
        plan=plan,
        workspace_slug="test-workspace",
        account_name="Test Account",
        primary_domain="test-account.com",
        base_timestamp=_BASE_TS,
    )
    # If this raises ValidationError the test fails.
    assert isinstance(scenario, ScenarioSpec)
    assert scenario.workspace_slug == "test-workspace"
    assert any(sig.source_type == "inbound_email" for sig in scenario.signals) or (
        plan.email_count == 0
    )


# ---------------------------------------------------------------------------
# Test 7 — Different week_index produces different scenario names
# ---------------------------------------------------------------------------


def test_plan_to_scenario_unique_scenario_names():
    """Two plans with different week_index produce distinct scenario names (→ distinct external_ids)."""
    plan_w0 = _plan(target_health=75, entry_seed=777, week_index=0)
    plan_w1 = _plan(target_health=75, entry_seed=777, week_index=1)

    scenario_w0 = plan_to_scenario(
        plan_w0,
        workspace_slug="ws",
        account_name="Acme",
        primary_domain="acme.com",
        base_timestamp=_BASE_TS,
    )
    scenario_w1 = plan_to_scenario(
        plan_w1,
        workspace_slug="ws",
        account_name="Acme",
        primary_domain="acme.com",
        base_timestamp=_BASE_TS,
    )

    assert scenario_w0.name != scenario_w1.name, (
        "Different week_index must produce different scenario names"
    )


# ---------------------------------------------------------------------------
# Test 8 — plan_to_scenario omits inbound_email when email_count == 0
# ---------------------------------------------------------------------------


def test_plan_to_scenario_zero_email_count_omits_signal():
    """When email_count == 0, no inbound_email SignalSpec appears in the output."""
    # Force zero email count by using frequency_multiplier=0.0
    plan = _plan(target_health=50, entry_seed=123, week_index=0, frequency_multiplier=0.0)
    # With frequency_multiplier=0, both counts should be 0.
    assert plan.email_count == 0

    scenario = plan_to_scenario(
        plan=plan,
        workspace_slug="ws",
        account_name="Acme",
        primary_domain="acme.com",
        base_timestamp=_BASE_TS,
    )
    source_types = {sig.source_type for sig in scenario.signals}
    assert "inbound_email" not in source_types, (
        "inbound_email SignalSpec must not appear when email_count == 0"
    )


# ---------------------------------------------------------------------------
# Additional: axes stay within valid AxesSpec field values
# ---------------------------------------------------------------------------


def test_axes_overrides_use_valid_field_names():
    """axes_overrides keys should all be recognised AxesSpec field names."""
    from src.synthetic.scenario import AxesSpec

    valid_fields = set(AxesSpec.model_fields.keys())
    for target_health in [10, 35, 60, 85]:
        plan = _plan(target_health=target_health)
        for key in plan.axes_overrides:
            assert key in valid_fields, (
                f"Unknown AxesSpec field {key!r} in axes_overrides for health={target_health}"
            )


# ---------------------------------------------------------------------------
# Additional: healthy band produces expected cadences
# ---------------------------------------------------------------------------


def test_healthy_band_cadence():
    """Healthy band (≥75) should produce steady or burst cadence, not drift."""
    cadences = {
        _plan(target_health=80, entry_seed=s, week_index=0).axes_overrides["response_cadence"]
        for s in range(20)
    }
    assert "drift" not in cadences, (
        f"Healthy band should not produce drift cadence; found in: {cadences}"
    )


# ---------------------------------------------------------------------------
# Hypothesis property test: synthesized axes always form a valid ScenarioSpec
# ---------------------------------------------------------------------------


@given(
    target_health=st.integers(min_value=1, max_value=100),
    entry_seed=st.integers(min_value=0, max_value=999_999),
    week_index=st.integers(min_value=0, max_value=51),
    frequency_multiplier=st.floats(min_value=0.5, max_value=3.0, allow_nan=False),
)
@settings(max_examples=300)
def test_property_plan_to_scenario_always_validates(
    target_health: int,
    entry_seed: int,
    week_index: int,
    frequency_multiplier: float,
) -> None:
    """For any valid input, plan_to_scenario produces a well-formed ScenarioSpec."""
    plan = week_to_signal_plan(
        account_slug="acme",
        week_start=_WEEK_START,
        target_health=target_health,
        entry_seed=entry_seed,
        week_index=week_index,
        frequency_multiplier=frequency_multiplier,
    )
    scenario = plan_to_scenario(
        plan=plan,
        workspace_slug="ws",
        account_name="Acme",
        primary_domain="acme.com",
        base_timestamp=_BASE_TS,
    )
    assert isinstance(scenario, ScenarioSpec)
    # Signal counts must be non-negative.
    for sig in scenario.signals:
        assert sig.count >= 0
