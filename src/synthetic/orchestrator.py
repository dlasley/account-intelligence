"""Orchestrator for the synthetic data generator.

Public API:
    load_scenario(path)          — parse + validate a scenario YAML file
    yield_events(scenario, ...)  — pure generator; no I/O; suitable for test injection
    run_scenario(...)            — generate + process events end-to-end; returns processed Signals

Timestamp-advancing logic (deterministic, no datetime.now()):
    The scenario's `seed` is used to seed a random.Random instance.  The orchestrator
    derives a start timestamp from _SCENARIO_BASE_TIME (2026-01-01T00:00:00Z) offset
    by a deterministic per-scenario jitter (seed % 86400 seconds).  Each signal
    then advances `now` by a cadence-specific delta drawn from the seeded RNG:
        burst   -> 5-120 minutes between signals
        steady  -> 1-3 days between signals
        drift   → starts at 1-day intervals, doubles every 4 signals (up to ~14 days)
        silence → 0 signals (caller omits the SignalSpec entirely)
    All resulting timestamps are UTC-aware.  No wall-clock calls anywhere in this module.

Dependency constraint: this module must NOT import src.db.  See ADR-015 §D6.
"""

import json
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import yaml

from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
from src.domain.signal import Signal, SourceType
from src.pipeline.product_event import ProductEvent
from src.synthetic.generators.email import generate_email_payload
from src.synthetic.generators.product import generate_product_event_payload
from src.synthetic.scenario import AccountSpec, ScenarioSpec

# ticket_plain and ticket_pylon are imported lazily inside the per-signal block
# to keep the top-level import graph minimal (same pattern as email/product).

# Fixed base timestamp — all synthetic signal timestamps anchor to this date.
# Using a fixed date rather than datetime.now() keeps output byte-identical across runs.
_SCENARIO_BASE_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def load_scenario(path: Path) -> ScenarioSpec:
    """Parse and validate a scenario YAML file.

    Raises pydantic.ValidationError with field-path context on invalid input.
    Raises SystemExit if the file does not exist.
    """
    if not path.exists():
        raise SystemExit(f"Scenario file not found: {path}")
    raw = yaml.safe_load(path.read_text())
    return ScenarioSpec.model_validate(raw)


def _scenario_start_time(seed: int) -> datetime:
    """Returns the deterministic start timestamp for a scenario given its seed.

    Applies a seed-derived jitter of ±12 hours around _SCENARIO_BASE_TIME.
    """
    jitter_seconds = (seed % 86400) - 43200  # offset in [-43200, 43199]
    return _SCENARIO_BASE_TIME + timedelta(seconds=jitter_seconds)


def _advance_time(
    rng: random.Random,
    current: datetime,
    cadence: str,
    signal_index_within_spec: int,
) -> datetime:
    """Return next timestamp for this signal.

    cadence:
        burst  -> 5-120 minutes (all within 48h of start)
        steady -> 1-3 days
        drift  → starts 1 day, doubles every 4 signals (simulates declining engagement)
        silence → should never be called (caller skips silence specs)
    """
    if cadence == "burst":
        delta_minutes = rng.randint(5, 120)
        return current + timedelta(minutes=delta_minutes)
    elif cadence == "drift":
        # Doubling window: signals 0-3 → 1 day, 4-7 → 2 days, 8-11 → 4 days, 12+ → 8 days
        base_days = 2 ** min(signal_index_within_spec // 4, 3)
        delta_days = rng.randint(base_days, base_days * 2)
        return current + timedelta(days=delta_days)
    else:  # "steady" and any unrecognised cadence
        delta_days = rng.randint(1, 3)
        return current + timedelta(days=delta_days)


def _make_raw_event(
    workspace_id: uuid.UUID,
    payload: dict,
    source_type: SourceType,
    received_at: datetime,
) -> RawInboundEvent:
    return RawInboundEvent(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        received_at=received_at,
        source_type=source_type,
        raw_payload=json.dumps(payload),
        parse_status=ParseStatus.PENDING,
        signal_id=None,
        error_detail=None,
        processed_at=None,
    )


def _account_lookup(scenario: ScenarioSpec) -> dict[str, AccountSpec]:
    """Build slug → AccountSpec map from scenario accounts list."""
    return {acc.slug: acc for acc in scenario.accounts}


def yield_events(
    scenario: ScenarioSpec,
    workspace_id: uuid.UUID,
    now_anchor: datetime | None = None,
) -> Iterator[tuple[str, RawInboundEvent | ProductEvent | dict]]:
    """Generate (account_slug, event) tuples in signal-spec order.

    event is one of:
      RawInboundEvent — for email source types (inbound_email, json_fixture, outbound_email)
      ProductEvent    — for product_event source type
      dict            — for plain_ticket, pylon_ticket, and granola_note (vendor webhook shapes)

    Pure generator — no I/O, no DB calls, no datetime.now().
    Suitable for test injection and in-memory scenarios.

    Yielding the slug alongside the event lets downstream consumers (materialiser,
    run_scenario) partition without re-walking the signal specs.
    """
    rng = random.Random(scenario.seed)
    account_map = _account_lookup(scenario)
    # now_anchor overrides the fixed-epoch start time — the simulator passes week_start
    # so historical signals land at the correct backdated timestamp (ADR-021 §O2).
    if now_anchor is not None:
        # Apply the same seed-derived jitter as _scenario_start_time, but anchored
        # to the caller's base time rather than _SCENARIO_BASE_TIME.
        jitter_seconds = (scenario.seed % 86400) - 43200
        now = now_anchor + timedelta(seconds=jitter_seconds)
    else:
        now = _scenario_start_time(scenario.seed)
    signal_index = 0  # global zero-based index across all signals in scenario

    # Dispatch table: source_type string → generator callable
    # Adding a new modality is one entry here + one new generator module.
    #
    # source_type dispatch decision (ADR-020 Phase 4.5):
    #   "plain_ticket"  — Phase 4 original; Plain-shaped payloads → parse_plain_event
    #   "pylon_ticket"  — Phase 4.5 new;    Pylon-shaped payloads → parse_pylon_event
    # The unqualified "ticket" value was never used; explicit vendor-qualified names
    # are required to keep cross-vendor dispatch unambiguous. Existing scenario YAMLs
    # using "plain_ticket" continue to work without modification.
    dispatch: dict[str, object] = {
        "inbound_email": generate_email_payload,
        "json_fixture": generate_email_payload,  # same generator; source_type overrideable
        "outbound_email": generate_email_payload,
        "product_event": generate_product_event_payload,
        "plain_ticket": "ticket_plain",  # sentinel — handled in the per-signal block below
        "pylon_ticket": "ticket_pylon",  # sentinel — handled in the per-signal block below
        "granola_note": "meeting_note",  # sentinel — handled in the per-signal block below
    }

    for spec in scenario.signals:
        if spec.axes.response_cadence == "silence":
            # silence means zero signals — structural gap, not a generator parameter
            continue

        acc_spec = account_map.get(spec.account_slug)
        if acc_spec is None:
            # Allow referencing quantas-labs-shaped accounts by slug without re-declaring them
            # in the scenario's accounts list (empty accounts: [] re-uses quantas-labs fixtures).
            # Use a safe fallback domain derived from the slug.
            account_name = spec.account_slug.replace("-", " ").title()
            primary_domain = f"{spec.account_slug}.com"
        else:
            account_name = acc_spec.name
            primary_domain = acc_spec.primary_domain or f"{acc_spec.slug}.com"

        source_type_str = spec.source_type
        generator = dispatch.get(source_type_str)

        if generator is None:
            # Unrecognised source_type — skip with warning.
            import logging

            logging.getLogger(__name__).warning(
                "No generator for source_type=%s; skipping %d signals", source_type_str, spec.count
            )
            signal_index += spec.count
            continue

        # Pre-compute the contact pool once per spec so contact_diversity is
        # honored across ALL signals in this spec, not freshly re-drawn per signal.
        # The pool is seeded from the same rng at this point in the walk so it
        # remains deterministic for a given (scenario_seed, spec ordering).
        email_contact_pool: list[tuple[str, str]] | None = None
        if source_type_str in ("inbound_email", "json_fixture", "outbound_email"):
            from src.synthetic.generators.email import build_contact_pool

            email_contact_pool = build_contact_pool(rng, spec.axes, primary_domain)

        # Similarly, pre-compute a contact email pool for product events so
        # contact_diversity is honored across all product signals in this spec.
        product_contact_pool: list[str | None] | None = None
        if source_type_str == "product_event":
            from src.synthetic.generators.product import build_product_contact_pool

            product_contact_pool = build_product_contact_pool(rng, spec.axes, primary_domain)

        # Pre-compute ticket contact pool once per spec (mirrors email pool pattern).
        ticket_contact_pool: list[tuple[str, str]] | None = None
        if source_type_str == "plain_ticket":
            from src.synthetic.generators.ticket_plain import build_ticket_contact_pool

            ticket_contact_pool = build_ticket_contact_pool(rng, spec.axes, primary_domain)
        elif source_type_str == "pylon_ticket":
            from src.synthetic.generators.ticket_pylon import build_pylon_contact_pool

            ticket_contact_pool = build_pylon_contact_pool(rng, spec.axes, primary_domain)

        for i in range(spec.count):
            # Advance timestamp for each signal
            if i > 0:
                now = _advance_time(rng, now, spec.axes.response_cadence, i)

            if source_type_str in ("inbound_email", "json_fixture", "outbound_email"):
                # Type narrowing for mypy/pyright: generator is the email callable
                from src.synthetic.generators.email import generate_email_payload as email_gen

                payload = email_gen(
                    spec=spec,
                    rng=rng,
                    now=now,
                    signal_index=signal_index,
                    scenario_name=scenario.name,
                    account_name=account_name,
                    primary_domain=primary_domain,
                    signal_index_within_spec=i,
                    contact_pool=email_contact_pool,
                )
                event = _make_raw_event(
                    workspace_id,
                    payload,
                    SourceType.JSON_FIXTURE,
                    received_at=now,
                )
                yield (spec.account_slug, event)

            elif source_type_str == "product_event":
                product_event = generate_product_event_payload(
                    spec=spec,
                    rng=rng,
                    now=now,
                    signal_index=signal_index,
                    scenario_name=scenario.name,
                    primary_domain=primary_domain,
                    contact_pool=product_contact_pool,
                    signal_index_within_spec=i,
                    spec_total_count=spec.count,
                )
                yield (spec.account_slug, product_event)

            elif source_type_str == "plain_ticket":
                from src.synthetic.generators.ticket_plain import generate_ticket_payload

                ticket_payload = generate_ticket_payload(
                    spec=spec,
                    rng=rng,
                    now=now,
                    signal_index=signal_index,
                    scenario_name=scenario.name,
                    account_name=account_name,
                    primary_domain=primary_domain,
                    signal_index_within_spec=i,
                    contact_pool=ticket_contact_pool,
                )
                yield (spec.account_slug, ticket_payload)

            elif source_type_str == "pylon_ticket":
                from src.synthetic.generators.ticket_pylon import generate_pylon_ticket_payload

                pylon_payload = generate_pylon_ticket_payload(
                    spec=spec,
                    rng=rng,
                    now=now,
                    signal_index=signal_index,
                    scenario_name=scenario.name,
                    account_name=account_name,
                    primary_domain=primary_domain,
                    signal_index_within_spec=i,
                    contact_pool=ticket_contact_pool,
                )
                yield (spec.account_slug, pylon_payload)

            elif source_type_str == "granola_note":
                from src.synthetic.generators.meeting_note import generate_meeting_note_payload

                note_payload = generate_meeting_note_payload(
                    spec=spec,
                    rng=rng,
                    now=now,
                    signal_index=signal_index,
                    scenario_name=scenario.name,
                    account_name=account_name,
                    primary_domain=primary_domain,
                    signal_index_within_spec=i,
                )
                yield (spec.account_slug, note_payload)

            signal_index += 1

        # Advance time between specs (simulate gap between signal groups)
        now = now + timedelta(hours=rng.randint(1, 48))


def run_scenario(
    scenario: ScenarioSpec,
    workspace_id: uuid.UUID,
    workspace: object,  # src.domain.workspace.Workspace — avoid importing domain here
    accounts: list,  # list[Account] — mutated in-place by process_event
    client: object,  # supabase.Client
    api_key_id: UUID | None = None,
    now_anchor: datetime | None = None,
) -> list[Signal]:
    """Generate and process all events end-to-end.

    Feeds each RawInboundEvent through process_event() and each ProductEvent
    through normalize_product_event(). Returns the list of processed Signal objects.

    Note: `workspace` and `accounts` are typed as `object`/`list` to avoid pulling
    domain imports into this module at import time — the caller always passes the
    correct concrete types.
    """
    from src.pipeline.product_event import normalize_product_event
    from src.pipeline.run import process_event

    effective_api_key_id = api_key_id if api_key_id is not None else uuid.UUID(int=0)
    workspace_name: str = getattr(workspace, "name", str(workspace_id))

    signals: list[Signal] = []
    for _slug, event in yield_events(scenario, workspace_id, now_anchor=now_anchor):
        if isinstance(event, RawInboundEvent):
            # Email pipeline entry point — never bypass (ADR-015 §D1)
            signal = process_event(event, workspace, accounts, client)  # type: ignore[arg-type]
            signals.append(signal)
        elif isinstance(event, ProductEvent):
            # Product-telemetry entry point — call normalize_product_event directly
            # (ADR-015 §D1 sanctions this as the internal equivalent of POST /event).
            result = normalize_product_event(
                event, workspace_id, workspace_name, effective_api_key_id, client  # type: ignore[arg-type]
            )
            signals.append(result.signal)
        elif isinstance(event, dict):
            # Structured-signal modalities (plain_ticket, granola_note).
            # Route through the appropriate production adapter then normalize_structured_signal.
            # Never bypass production normalisation (ADR-015 §D1).
            signal = _process_structured_signal_dict(event, workspace_id, workspace_name, client)
            if signal is not None:
                signals.append(signal)
    return signals


def _process_structured_signal_dict(
    payload: dict,
    workspace_id: uuid.UUID,
    workspace_name: str,
    client: object,
) -> Signal | None:
    """Route a raw dict through the correct production adapter + normalizer.

    Dispatch rules (first match wins):
    1. ``payload.get("id", "").startswith("not_")``  → Granola note
    2. ``"data" in payload``                          → Pylon ticket ({"data": {...}} envelope)
    3. otherwise                                      → Plain ticket (top-level "type" key)

    The synthetic credential_id is a zero UUID (no real credential exists during
    synthesis; the normalizer uses it only for audit tagging).

    BOTH COPIES MUST CHANGE TOGETHER — see worker.py:317-326. The shape-based
    partitioning cascade in worker._process_fixtures (Granola startswith → Pylon
    "data" key → Plain top-level "type") is duplicated here. Adding a fifth
    structured-signal source (Stripe, Intercom, Salesforce, Svix shapes would
    collide) requires updating both call sites or the materialise path and
    live-simulation path silently diverge — and this copy's bare ``else`` falls
    through to Plain, so the misroute is silent, not a loud error.
    """
    from uuid import UUID as _UUID

    from src.pipeline.structured_signal import normalize_structured_signal

    _SYNTHETIC_CREDENTIAL_ID = _UUID(int=0)

    note_id: str = payload.get("id", "")

    if note_id.startswith("not_"):
        # Granola note
        from src.integrations.granola.adapter import parse_granola_note

        structured = parse_granola_note(payload, _SYNTHETIC_CREDENTIAL_ID)
        if structured is None:
            return None
        result = normalize_structured_signal(
            structured,
            workspace_id,
            workspace_name,
            _SYNTHETIC_CREDENTIAL_ID,
            "granola_api_key",
            client,  # type: ignore[arg-type]
        )
        return result.signal
    elif "data" in payload:
        # Pylon ticket event — {"data": {"id": ..., "type": ..., "issue": {...}}}
        from src.integrations.pylon.adapter import parse_pylon_event

        event_type: str = payload["data"].get("type", "")
        structured = parse_pylon_event(payload, event_type, _SYNTHETIC_CREDENTIAL_ID)
        if structured is None:
            return None
        result = normalize_structured_signal(
            structured,
            workspace_id,
            workspace_name,
            _SYNTHETIC_CREDENTIAL_ID,
            "pylon_webhook_secret",
            client,  # type: ignore[arg-type]
        )
        return result.signal
    else:
        # Plain ticket event — top-level "type" field
        from src.integrations.plain.adapter import parse_plain_event

        plain_event_type: str = payload.get("type", "")
        structured = parse_plain_event(payload, plain_event_type, _SYNTHETIC_CREDENTIAL_ID)
        if structured is None:
            return None
        result = normalize_structured_signal(
            structured,
            workspace_id,
            workspace_name,
            _SYNTHETIC_CREDENTIAL_ID,
            "plain_webhook_secret",
            client,  # type: ignore[arg-type]
        )
        return result.signal
