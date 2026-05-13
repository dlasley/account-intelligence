from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.config.schema import AccountHealthConfig
from src.domain.signal import Signal, SourceType


@dataclass(frozen=True)
class HealthResult:
    score: int
    tier_name: str
    rationale: str
    window_start: datetime
    window_end: datetime
    signal_count: int
    contact_count: int
    signals_in_window: tuple[Signal, ...]


def _filter_signals_in_window(
    signals: list[Signal], window_days: int, now: datetime
) -> tuple[datetime, list[Signal], int]:
    """Returns (window_start, signals_in_window, contact_count)."""
    window_start = now - timedelta(days=window_days)
    window = [s for s in signals if s.occurred_at >= window_start]
    contacts = len({s.author_contact_id for s in window if s.author_contact_id})
    return window_start, window, contacts


def score_product_usage(
    signals: list[Signal],
    config: dict,
    frequency_multiplier: float = 1.0,
    now: datetime | None = None,
) -> tuple[int | None, int | None]:
    """Deterministic cascade-window product-usage dimension scorer (ADR-017 D1 amended).

    Tries each tier in ``window_days_cascade`` (default [7, 14, 30, 60]) from
    tightest to widest. Accepts the first tier that has meaningful signal:
      - rule 1: no events in tier → fall through
      - rule 2: events only in early half → score 15 (gone quiet), stop
      - rule 3/4: events in both halves (or recent-only) → score and stop
    Falls through all tiers → returns (None, None).

    Returns (score, window_days_used). Both are None when no tier had signal —
    the caller (compute_overall_health) excludes None-scored dimensions from the
    weighted average, so accounts without product telemetry are unaffected.

    ``now`` defaults to ``datetime.now(UTC)`` for production code paths. Callers
    operating on synthetic / fixed-epoch signals pass an explicit ``now`` so
    window arithmetic lines up with signal timestamps.
    """
    if now is None:
        now = datetime.now(UTC)

    # Backwards-compat: prefer window_days_cascade; fall back to single-element
    # list from window_days if cascade key is absent.
    cascade: list[int] = config.get(
        "window_days_cascade",
        [int(config.get("window_days", 7))],
    )
    min_events_for_active = int(config.get("min_events_for_active", 1))
    decay_ratio_threshold = float(config.get("trajectory_decay_ratio", 0.5))
    effective_min = max(1, round(min_events_for_active * frequency_multiplier))

    for window_days in cascade:
        early_start = now - timedelta(days=window_days)
        mid = now - timedelta(days=window_days / 2)

        product_signals = [
            s
            for s in signals
            if s.source_type == SourceType.PRODUCT_EVENT and s.occurred_at >= early_start
        ]

        if not product_signals:
            continue  # rule 1: no events in this tier — fall through

        recent = [s for s in product_signals if s.occurred_at >= mid]
        early = [s for s in product_signals if s.occurred_at < mid]

        recent_count = len(recent)
        early_count = len(early)
        recent_contacts = len({s.author_contact_id for s in recent if s.author_contact_id})

        if recent_count == 0:
            # rule 2: events only in early half — gone quiet; stop cascade
            return 15, window_days

        # rules 3 and 4: this tier has recent signal
        if recent_count < effective_min:
            return 15, window_days  # quiet tier (effective_min check)

        # Base score: tier cascade on recent_count + recent contact diversity.
        # Mirrors the tier structure in determine_account_health.
        if recent_count >= 10 and recent_contacts >= 3:
            base = 90
        elif recent_count >= 6 and recent_contacts >= 2:
            base = 75
        elif recent_count >= 3 and recent_contacts >= 2:
            base = 60
        elif recent_count >= 2 and recent_contacts >= 1:
            base = 45
        else:
            base = 30

        # Trajectory multiplier: penalise declining patterns.
        # If no early events, trajectory is unknown — no penalty (rule 4).
        if early_count > 0:
            ratio = recent_count / early_count
            if ratio < decay_ratio_threshold:
                # Sharp decline: multiplier in [0.4, 1.0) proportional to threshold
                multiplier = 0.4 + 0.6 * (ratio / decay_ratio_threshold)
            elif ratio < 1.0:
                # Mild decline: small penalty
                multiplier = 0.85 + 0.15 * ratio
            else:
                # Stable or growing — no penalty, small growth bonus capped at 1.1
                multiplier = min(1.1, 1.0 + 0.1 * (ratio - 1.0))
        else:
            multiplier = 1.0  # rule 4: all events in recent window; optimistic

        raw = base * multiplier
        return max(1, min(100, round(raw))), window_days

    return None, None  # no tier had any events


def determine_account_health(
    signals: list[Signal],
    config: AccountHealthConfig,
    frequency_multiplier: float = 1.0,
    now: datetime | None = None,
) -> HealthResult:
    """
    Deterministic engagement classification. Spec §8.3: the LLM explains the score,
    it does NOT decide it.

    Iterates tiers in order (highest → default). The last tier in the list is always returned
    if no earlier tier matches — its thresholds are not checked.

    ``now`` defaults to ``datetime.now(UTC)`` for production code paths. Callers
    that operate on synthetic / fixed-epoch signals (distribution tests, snapshot
    fixtures, replays) pass an explicit ``now`` so window arithmetic lines up with
    the signal timestamps without re-timestamping the signals themselves.
    """
    if now is None:
        now = datetime.now(UTC)
    tiers = config.engagement_tiers
    default_tier = tiers[-1]

    for tier_cfg in tiers[:-1]:
        window_start, window, contacts = _filter_signals_in_window(
            signals, tier_cfg.window_days, now
        )
        effective_min_signals = (
            max(1, round(tier_cfg.min_signals * frequency_multiplier))
            if tier_cfg.min_signals > 0
            else 0
        )
        if len(window) >= effective_min_signals and contacts >= tier_cfg.min_contacts:
            return HealthResult(
                score=tier_cfg.score,
                tier_name=tier_cfg.name,
                rationale=(
                    f"{len(window)} signals in the last {tier_cfg.window_days} days "
                    f"from {contacts} contact{'s' if contacts != 1 else ''}."
                ),
                window_start=window_start,
                window_end=now,
                signal_count=len(window),
                contact_count=contacts,
                signals_in_window=tuple(window),
            )

    # default tier
    window_start, window, contacts = _filter_signals_in_window(
        signals, default_tier.window_days, now
    )
    rationale = (
        f"{len(window)} signal{'s' if len(window) != 1 else ''} "
        f"in the last {default_tier.window_days} days"
        + (
            f" from {contacts} contact{'s' if contacts != 1 else ''}."
            if contacts
            else ". No signals in window."
        )
    )
    return HealthResult(
        score=default_tier.score,
        tier_name=default_tier.name,
        rationale=rationale,
        window_start=window_start,
        window_end=now,
        signal_count=len(window),
        contact_count=contacts,
        signals_in_window=tuple(window),
    )
