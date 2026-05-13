"""Product-event signal generator.

Produces a ProductEvent dataclass (see src/pipeline/product_event.py:19-24).

Contract rules enforced here (ADR-015 §D10):
- Returns ProductEvent directly — no wrapping in RawInboundEvent
- Accepts a seeded `random.Random` instance — no module-level random calls
- Accepts a `now: datetime` parameter — no datetime.now() calls
- `event_id` is deterministic: uuid5(NAMESPACE_DNS, "{scenario_name}:pe:{signal_index}")
"""

import random
import uuid
from datetime import datetime

from src.pipeline.product_event import ProductEvent
from src.synthetic.scenario import AxesSpec, SignalSpec

# ---------------------------------------------------------------------------
# SaaS-plausible event names
# ---------------------------------------------------------------------------

# Expansion-coded events: indicate plan upgrades, seat growth, or trial extensions.
# Excluded from selection when cross_modal == "divergent" because warm email already
# carries the expansion signal; including these in product data would contradict the
# multi-source divergence pattern the scenario is designed to surface.
_EXPANSION_EVENT_NAMES: frozenset[str] = frozenset(
    [
        "plan_changed",
        "trial_extended",
        "member_invited",
    ]
)

_EVENT_NAMES = [
    "feature_used",
    "export_started",
    "dashboard_viewed",
    "settings_updated",
    "workspace_created",
    "file_uploaded",
    "share_link_generated",
    "comment_added",
    "integration_connected",
    "trial_extended",
    "plan_changed",
    "member_invited",
    "report_generated",
    "search_performed",
]

# Subset of _EVENT_NAMES with expansion-coded events removed — used when divergent.
_NON_EXPANSION_EVENT_NAMES = [e for e in _EVENT_NAMES if e not in _EXPANSION_EVENT_NAMES]

# Plausible product-feature names. Used as the `feature` property on synthetic
# product events. Decoupled from `event_name` so events like `feature_used` get
# a meaningful feature label (e.g. "saved_views") instead of the meaningless
# `feature=feature_used` pattern. Noun-like — describes a part of the product
# the user interacted with, not the verb describing what they did with it.
_PRODUCT_FEATURES = [
    "dashboards",
    "reports",
    "search",
    "saved_views",
    "alerts",
    "workflows",
    "templates",
    "integrations",
    "audit_log",
    "api_keys",
    "analytics",
    "team_management",
    "billing",
    "notifications",
    "comments",
    "mentions",
    "bulk_actions",
    "favorites",
    "shareable_links",
    "column_filters",
    "date_range_picker",
    "csv_export",
    "pdf_export",
    "webhooks",
    "sso_settings",
]

# Domains used for personal_email contacts
_FREE_MAIL_DOMAINS = ["gmail.com", "outlook.com"]

# Contact first/last names — same pool as email generator for consistency
_FIRST_NAMES = [
    "Alex",
    "Jordan",
    "Morgan",
    "Taylor",
    "Casey",
    "Riley",
    "Drew",
    "Cameron",
    "Quinn",
    "Avery",
    "Blake",
    "Reese",
    "Skylar",
    "Dana",
    "Kendall",
]
_LAST_NAMES = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Wilson",
    "Anderson",
    "Thomas",
    "Jackson",
    "White",
    "Harris",
]

_PLAN_TIERS = ["free", "pro", "enterprise"]


def _pick_contact_email(rng: random.Random, axes: AxesSpec, primary_domain: str) -> str | None:
    """Return a contact email based on contact_email_origin axis.

    corporate      -> <name>@<primary_domain> (always present)
    personal_email -> 30% None (anonymous session), 50% corporate, 20% free-mail
    mixed          -> 50% corporate, 50% free-mail (always present)
    """
    first = rng.choice(_FIRST_NAMES)
    last = rng.choice(_LAST_NAMES)
    local = f"{first.lower()}.{last.lower()}"

    if axes.contact_email_origin == "corporate":
        return f"{local}@{primary_domain}"
    elif axes.contact_email_origin == "personal_email":
        roll = rng.random()
        if roll < 0.30:
            # Anonymous session — exercises the unmatched routing branch
            return None
        elif roll < 0.80:
            return f"{local}@{primary_domain}"
        else:
            domain = rng.choice(_FREE_MAIL_DOMAINS)
            return f"{local}@{domain}"
    else:  # "mixed"
        if rng.random() < 0.50:
            return f"{local}@{primary_domain}"
        domain = rng.choice(_FREE_MAIL_DOMAINS)
        return f"{local}@{domain}"


def build_product_contact_pool(
    rng: random.Random,
    axes: AxesSpec,
    primary_domain: str,
) -> list[str | None]:
    """Build the fixed contact email pool for an entire product-event SignalSpec.

    Call once per spec in the orchestrator; pass the result to every signal so
    contact_diversity is honored across signals, not just within one.

    Returns a list of email strings (or None for anonymous sessions):
      single  → 1 email
      multi   → 2-3 emails
      crowded → 4-6 emails
    """
    count_map = {"single": 1, "multi": rng.randint(2, 3), "crowded": rng.randint(4, 6)}
    count = count_map.get(axes.contact_diversity, 1)
    return [_pick_contact_email(rng, axes, primary_domain) for _ in range(count)]


def generate_product_event_payload(
    spec: SignalSpec,
    rng: random.Random,
    now: datetime,
    signal_index: int,
    scenario_name: str,
    primary_domain: str,
    contact_pool: list[str | None] | None = None,
    signal_index_within_spec: int = 0,
    spec_total_count: int = 1,
) -> ProductEvent:
    """Generate a single ProductEvent.

    Args:
        spec: The SignalSpec driving this signal's axes.
        rng: Seeded Random instance — no module-level random calls.
        now: Timestamp for this signal — no datetime.now() calls.
        signal_index: Zero-based index in the full generated sequence; used for uuid5.
        scenario_name: Used to derive the deterministic event_id.
        primary_domain: Account's primary email domain — resolved from AccountSpec
            in the orchestrator. Used to construct corporate-domain contact emails
            so they actually match Contact rows in the live workspace.
        contact_pool: Pre-built list of email strings (or None for anonymous) for
            this spec.  When provided, a contact is chosen from this fixed pool so
            contact_diversity is honored across all signals in the spec, not just
            within a single signal.  The orchestrator builds this once per spec.
            When None (legacy / test callers), falls back to per-signal pick.
        signal_index_within_spec: Zero-based position of this signal within the current
            spec (0 = first signal in spec).  Used by the divergent cross_modal behavior
            to narrow the active contact pool in the last quarter of the spec.
        spec_total_count: Total number of signals in the current spec.  Paired with
            signal_index_within_spec to determine the temporal position boundary
            for the divergent shrinking-pool logic.

    Returns:
        ProductEvent dataclass — ready for normalize_product_event().
    """
    axes = spec.axes

    # Resolve contact_email: explicit override takes precedence over axes
    if "contact_email" in spec.overrides:
        # None override explicitly forces the unmatched routing branch
        contact_email: str | None = spec.overrides["contact_email"]
    elif contact_pool is not None:
        # divergent cross_modal: narrow the active pool in the last quarter of the spec.
        # Signals in the first half draw from the full pool; signals in the last quarter
        # draw from only the first 1-2 members.  This produces a deterministic
        # team-shrinking pattern without any unseeded RNG or wall-clock calls.
        if axes.cross_modal == "divergent" and len(contact_pool) > 2:
            last_quarter_start = max(1, int(spec_total_count * 0.75))
            if signal_index_within_spec >= last_quarter_start:
                # Clamp to at most 2 contacts from the front of the pool
                active_pool = contact_pool[:2]
            else:
                active_pool = contact_pool
            contact_email = rng.choice(active_pool)
        else:
            contact_email = rng.choice(contact_pool)
    else:
        contact_email = _pick_contact_email(rng, axes, primary_domain)

    # divergent cross_modal: exclude expansion-coded events so the product data
    # does not contradict the warm-email channel that carries the expansion signal.
    if axes.cross_modal == "divergent":
        event_name = rng.choice(_NON_EXPANSION_EVENT_NAMES)
    else:
        event_name = rng.choice(_EVENT_NAMES)
    plan = rng.choice(_PLAN_TIERS)
    duration_ms = rng.randint(50, 30000)
    feature = rng.choice(_PRODUCT_FEATURES)

    event_properties = {
        "plan": plan,
        "feature": feature,
        "duration_ms": duration_ms,
    }

    # Deterministic ID: uuid5(NAMESPACE_DNS, "{scenario_name}:pe:{signal_index}")
    event_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{scenario_name}:pe:{signal_index}"))

    return ProductEvent(
        contact_email=contact_email,
        event_name=event_name,
        event_properties=event_properties,
        event_id=event_id,
        occurred_at=now,
    )
