"""Shared product-usage trajectory render function (ADR-017 D5 Option C).

Used by both src/pipeline/generator.py (narrative generation) and
scripts/audit_narratives.py (audit harness) so both LLMs receive identical
deterministic context. Kept in a lightweight module to avoid pulling
the full generator.py import chain into the audit script.

The render function is a pure function — no I/O, no DB calls, no datetime.now().
Determinism guarantee: calling it twice with the same (signals, now, config)
returns byte-identical output. This is the contract the audit symmetry test verifies.
"""

from datetime import UTC, datetime, timedelta
from typing import Any


def render_product_usage_trajectory(
    signals: list[Any],
    now: datetime,
    config: dict | None = None,
) -> str:
    """Render a deterministic product-usage trajectory block for LLM prompt injection.

    Runs the same cascade tier selection as score_product_usage (ADR-017 D1 amendment):
    tries tiers in ascending window-width order, accepts the first tier with meaningful
    signal (both halves non-empty, or early-half-only for quiet-tier detection).
    Renders facts from the accepted tier.

    Returns empty string if no tier has any product events.

    Args:
        signals: Signal rows for the account (all source types; function filters
                 to source_type='product_event' internally). Accepts both domain
                 Signal objects and raw dict-backed _SignalRow objects from the
                 audit harness — only standard attribute access is used.
        now: Reference timestamp for window arithmetic. Must be tz-aware.
        config: The health_dimension_configs.config dict for the product_usage dimension.
                Used to read window_days_cascade and window_days. If None, uses
                default cascade [7, 14, 30, 60].
    Returns:
        A multi-line string block, or empty string if no product events exist in any tier.
    """
    if config is None:
        config = {}

    # Derive cascade — same precedence as score_product_usage.
    cascade: list[int] = config.get(
        "window_days_cascade",
        [int(config.get("window_days", 7))],
    )
    tightest = cascade[0]

    def _to_dt(val: Any) -> datetime | None:
        """Coerce a value to a tz-aware datetime; return None if not parseable."""
        if isinstance(val, datetime):
            return val if val.tzinfo else val.replace(tzinfo=UTC)
        if isinstance(val, str):
            try:
                # Handle Postgres ISO strings with space separator and short tz offset
                return datetime.fromisoformat(val.replace(" ", "T").replace("+00", "+00:00"))
            except ValueError:
                return None
        return None

    # Run cascade tier selection — mirrors the acceptance rules in score_product_usage.
    accepted_window: int | None = None
    accepted_recent: list[Any] = []
    accepted_early: list[Any] = []

    for window_days in cascade:
        early_start = now - timedelta(days=window_days)
        mid = now - timedelta(days=window_days / 2)

        in_window: list[tuple[Any, datetime]] = []
        for s in signals:
            if str(getattr(s, "source_type", "")) != "product_event":
                continue
            occ = _to_dt(getattr(s, "occurred_at", None))
            if occ is not None and occ >= early_start:
                in_window.append((s, occ))

        if not in_window:
            continue  # rule 1: fall through

        recent_pairs = [(s, occ) for s, occ in in_window if occ >= mid]
        early_pairs = [(s, occ) for s, occ in in_window if occ < mid]

        # rule 2: gone quiet — early only, no recent; render this tier and stop
        # rule 3/4: both halves or recent-only; render this tier and stop
        accepted_window = window_days
        accepted_recent = [s for s, _ in recent_pairs]
        accepted_early = [s for s, _ in early_pairs]
        break

    if accepted_window is None:
        return ""

    window_days = accepted_window
    recent = accepted_recent
    early = accepted_early

    # Collect contact IDs as strings for diversity counts.
    # Exclude None author_contact_id values without raising.
    recent_contacts: set[str] = set()
    early_contacts: set[str] = set()
    for s in recent:
        cid = getattr(s, "author_contact_id", None)
        if cid is not None:
            recent_contacts.add(str(cid))
    for s in early:
        cid = getattr(s, "author_contact_id", None)
        if cid is not None:
            early_contacts.add(str(cid))

    # Distinct event names (up to 4 per window, sorted for determinism)
    recent_event_names = sorted(
        {e for s in recent if (e := getattr(s, "event_name", None)) is not None}
    )[:4]
    early_event_names = sorted(
        {e for s in early if (e := getattr(s, "event_name", None)) is not None}
    )[:4]

    lines: list[str] = []
    # Use half-window as displayed in headings. The scorer uses window_days / 2 (float);
    # we display the integer half for readability (3.5d rounds to 3d label for T1).
    half = window_days // 2

    # Framing preamble: separates cascade configuration (scoring window thresholds) from
    # signal evidence (observed activity counts). Without this, an LLM reading window-day
    # labels like "last 7 days" or "14-30 days ago" may infer that activity existed in
    # those windows even when event counts are zero. The preamble makes clear that the
    # numbers below the headers are the evidence — window labels are configuration only.
    lines.append(
        "SCORING CONFIGURATION: The following windows are derived from signal evidence."
        " Window labels below (e.g. 'last N days') are the scoring tier selected by the"
        " cascade — they describe which time range was evaluated, not that activity existed."
        " Absence of events (count=0 or n/a) means no product events were found in signals."
    )
    lines.append("")

    # Cascade fall-through annotation: surfaces "no recent activity" as an explicit fact.
    if window_days != tightest:
        lines.append(
            f"NOTE: no product events in the last {tightest} days;"
            f" scored from {window_days}-day window."
        )
        lines.append("")

    recent_events_str = ", ".join(recent_event_names) if recent_event_names else "n/a"
    lines.append(f"PRODUCT USAGE — RECENT WINDOW (last {half} days)")
    lines.append(f"  Events: {len(recent)} ({recent_events_str})")
    lines.append(f"  Distinct contacts: {len(recent_contacts)}")
    lines.append("")

    early_events_str = ", ".join(early_event_names) if early_event_names else "n/a"
    lines.append(f"PRODUCT USAGE — PRIOR WINDOW ({half}-{window_days} days ago)")
    lines.append(f"  Events: {len(early)} ({early_events_str})")
    lines.append(f"  Distinct contacts: {len(early_contacts)}")
    lines.append("")

    # Trajectory summary line
    if not recent:
        lines.append("TRAJECTORY: usage entirely in prior window — account has gone quiet")
    elif not early:
        lines.append("TRAJECTORY: usage entirely in recent window — no prior baseline")
    else:
        volume_dir = (
            f"+{len(recent) - len(early)}" if len(recent) >= len(early)
            else str(len(recent) - len(early))
        )
        if len(recent_contacts) < len(early_contacts) and len(early_contacts) > 0:
            pct = round(abs(len(recent_contacts) - len(early_contacts)) / len(early_contacts) * 100)
            diversity_str = (
                f"contact diversity declined from {len(early_contacts)}"
                f" to {len(recent_contacts)} (-{pct}%)"
            )
        elif len(recent_contacts) > len(early_contacts):
            diversity_str = (
                f"contact diversity grew from {len(early_contacts)} to {len(recent_contacts)}"
            )
        else:
            diversity_str = f"contact diversity stable at {len(recent_contacts)}"
        lines.append(
            f"TRAJECTORY: {diversity_str},"
            f" event volume {len(early)} → {len(recent)} ({volume_dir})"
        )

    return "\n".join(lines)
