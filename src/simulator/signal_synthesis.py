"""Signal synthesis: map a (week_start, target_health) point to a WeekSignalPlan.

The WeekSignalPlan captures the axis overrides and signal counts that, when fed
through the existing synthetic orchestrator, produce signal mixes whose scoring
lands within ±10 of the target health value.

See ADR-021 §O1 for the health-band → axis mapping table, signal count ranges,
and the ±10 tolerance rationale.

No I/O, no DB calls, no datetime.now(). Pure functions only.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime

from src.synthetic.scenario import AccountSpec, AxesSpec, ScenarioSpec, SignalSpec

# ---------------------------------------------------------------------------
# Health band definitions (ADR-021 §O1)
# ---------------------------------------------------------------------------
#
# Each entry maps a (low, high) health range to the axis value lists and signal
# count ranges that represent that band's account state.  The seeded RNG picks
# from each list with random.choice; count ranges use rng.randint(lo, hi).

_BANDS: list[
    tuple[
        int,   # low threshold (inclusive)
        int,   # high threshold (inclusive)
        dict,  # axis → list of candidate values
        tuple[int, int],  # (email_lo, email_hi) base count range
        tuple[int, int],  # (product_lo, product_hi) base count range
    ]
] = [
    # ≥75 — healthy
    (
        75,
        100,
        {
            "sentiment_trajectory": ["flat", "flat", "recovering"],
            "response_cadence": ["steady", "steady", "burst"],
            "cross_modal": ["balanced", "product_heavy", "aligned"],
            "concern_topic": ["success_expansion", "renewal_pending"],
            "email_tone": ["technical", "casual"],
        },
        (2, 4),   # email signals per week
        (5, 10),  # product events per week
    ),
    # 50-74 -- moderate
    (
        50,
        74,
        {
            "sentiment_trajectory": ["flat", "flat", "recovering"],
            "response_cadence": ["steady", "drift"],
            "cross_modal": ["balanced", "balanced", "email_heavy"],
            "concern_topic": ["none", "none", "feature_gap"],
            "email_tone": ["technical", "formal"],
        },
        (1, 3),
        (2, 6),
    ),
    # 30-49 -- at-risk
    (
        30,
        49,
        {
            "sentiment_trajectory": ["declining", "declining", "flat"],
            "response_cadence": ["drift", "drift", "steady"],
            "cross_modal": ["email_heavy", "email_heavy", "balanced"],
            "concern_topic": ["utilization_decline", "pricing", "feature_gap"],
            "email_tone": ["formal", "escalation"],
        },
        (1, 2),
        (1, 3),
    ),
    # <30 — critical
    (
        1,
        29,
        {
            "sentiment_trajectory": ["declining", "sudden_escalation"],
            "response_cadence": ["burst", "drift"],
            "cross_modal": ["email_heavy", "email_heavy", "divergent"],
            "concern_topic": ["outage", "competitive", "utilization_decline"],
            "email_tone": ["escalation", "apologetic"],
        },
        (0, 2),
        (0, 1),
    ),
]


def _pick_band(target_health: int) -> tuple[dict, tuple[int, int], tuple[int, int]]:
    """Return the (axis_map, email_range, product_range) for a target health value.

    Bands are checked from highest to lowest; the first matching band wins.
    target_health is clamped to [1, 100] defensively.
    """
    h = max(1, min(100, target_health))
    for low, high, axis_map, email_range, product_range in _BANDS:
        if low <= h <= high:
            return axis_map, email_range, product_range
    # Should never be reached given [1, 100] bands cover all values.
    # Fall through to at-risk band as a safe default.
    _, _, axis_map, email_range, product_range = _BANDS[2]
    return axis_map, email_range, product_range


# ---------------------------------------------------------------------------
# Public data model
# ---------------------------------------------------------------------------


@dataclass
class WeekSignalPlan:
    """What to synthesize for one account for one week.

    Attributes:
        account_slug: the account this week's signals belong to.
        week_start: the Monday-aligned (or entry-anchored) start date of the week.
        email_count: number of inbound_email signals to generate.
        product_count: number of product_event signals to generate.
        axes_overrides: dict of AxesSpec field overrides chosen for this week's
            health band; merged over AxesSpec defaults by plan_to_scenario.
        seed: stable per-week seed derived from entry_seed + week_index * 13.
        week_index: 0-based position of this week within the trajectory entry.
        entry_seed: the trajectory entry's seed (stored for scenario naming).
    """

    account_slug: str
    week_start: object  # datetime.date — typed as object to avoid import at model level
    email_count: int
    product_count: int
    axes_overrides: dict
    seed: int
    week_index: int
    entry_seed: int


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------


def week_to_signal_plan(
    account_slug: str,
    week_start: object,  # datetime.date
    target_health: int,
    entry_seed: int,
    week_index: int,
    frequency_multiplier: float = 1.0,
) -> WeekSignalPlan:
    """Map a (week_start, target_health) point to a synthesis plan for that week.

    ``entry_seed`` is the trajectory entry's seed; ``week_index`` is the week's
    0-based position within the entry.  The per-week seed is derived as::

        per_week_seed = entry_seed + week_index * 13

    The multiplier 13 is coprime with common seed spacings (1, 2, 4, ...) so
    adjacent weeks do not accidentally produce identical RNG sequences.

    Signal counts are scaled by ``frequency_multiplier`` and floored at 0.

    Args:
        account_slug: account this plan belongs to.
        week_start: start date of the week (datetime.date).
        target_health: integer health target [1, 100] for this week.
        entry_seed: the trajectory entry's seed field.
        week_index: 0-based week position within the entry.
        frequency_multiplier: account-level scaling factor for signal counts.
            Defaults to 1.0.

    Returns:
        WeekSignalPlan with concrete counts and axis choices.
    """
    per_week_seed = entry_seed + week_index * 13
    rng = random.Random(per_week_seed)

    axis_map, (email_lo, email_hi), (product_lo, product_hi) = _pick_band(target_health)

    # Pick concrete axis values via random.choice from each band's candidate list.
    axes_overrides: dict[str, str] = {
        axis: rng.choice(candidates)
        for axis, candidates in axis_map.items()
    }

    # Base counts drawn from the band's range, then scaled by frequency_multiplier.
    base_email = rng.randint(email_lo, email_hi)
    base_product = rng.randint(product_lo, product_hi)

    email_count = max(0, round(base_email * frequency_multiplier))
    product_count = max(0, round(base_product * frequency_multiplier))

    return WeekSignalPlan(
        account_slug=account_slug,
        week_start=week_start,
        email_count=email_count,
        product_count=product_count,
        axes_overrides=axes_overrides,
        seed=per_week_seed,
        week_index=week_index,
        entry_seed=entry_seed,
    )


# ---------------------------------------------------------------------------
# ScenarioSpec builder
# ---------------------------------------------------------------------------


def plan_to_scenario(
    plan: WeekSignalPlan,
    workspace_slug: str,
    account_name: str,
    primary_domain: str,
    base_timestamp: datetime,  # week_start as tz-aware datetime
) -> ScenarioSpec:
    """Build a minimal ScenarioSpec for one account for one week.

    The scenario name encodes the account slug, entry seed, and week index to
    guarantee unique uuid5 external_ids across all simulated batches::

        name = f"traj:{plan.account_slug}:{plan.entry_seed}:{plan.week_index}"

    The scenario contains at most two SignalSpec entries:
    - ``inbound_email`` with ``count=plan.email_count`` (omitted when count == 0)
    - ``product_event`` with ``count=plan.product_count`` (omitted when count == 0)

    The orchestrator's ``_scenario_start_time`` derives a jitter from the seed,
    so the actual timestamps will vary slightly around ``base_timestamp``.  The
    executor (Phase 4) is responsible for anchoring the real timestamps — this
    function only builds the structural spec.

    Args:
        plan: the WeekSignalPlan produced by week_to_signal_plan.
        workspace_slug: target workspace slug.
        account_name: human-readable account name (used by email generator).
        primary_domain: primary email domain for the account.
        base_timestamp: tz-aware datetime representing the start of the week
            (informational; the orchestrator uses scenario.seed for its own anchor).

    Returns:
        ScenarioSpec validated against the existing Pydantic model.
    """
    scenario_name = f"traj:{plan.account_slug}:{plan.entry_seed}:{plan.week_index}"

    # Build the AxesSpec by merging the band's overrides over the defaults.
    # AxesSpec uses extra="forbid" so we must only pass valid field names.
    valid_axes_fields = set(AxesSpec.model_fields.keys())
    axes_kwargs: dict[str, str] = {
        k: v for k, v in plan.axes_overrides.items() if k in valid_axes_fields
    }
    axes = AxesSpec(**axes_kwargs)

    account_spec = AccountSpec(
        slug=plan.account_slug,
        name=account_name,
        primary_domain=primary_domain,
    )

    signals: list[SignalSpec] = []
    if plan.email_count > 0:
        signals.append(
            SignalSpec(
                source_type="inbound_email",
                account_slug=plan.account_slug,
                count=plan.email_count,
                axes=axes,
            )
        )
    if plan.product_count > 0:
        signals.append(
            SignalSpec(
                source_type="product_event",
                account_slug=plan.account_slug,
                count=plan.product_count,
                axes=axes,
            )
        )

    return ScenarioSpec(
        version=1,
        name=scenario_name,
        seed=plan.seed,
        description=f"Simulated week {plan.week_index} for {plan.account_slug} "
        f"(base_ts={base_timestamp.isoformat()})",
        workspace_slug=workspace_slug,
        accounts=[account_spec],
        signals=signals,
    )
