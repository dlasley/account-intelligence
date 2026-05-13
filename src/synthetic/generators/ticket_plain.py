# ruff: noqa: E501
"""Plain ticket signal generator (ADR-020 Phase 4 / Phase 4.5).

Produces a Plain-webhook-shaped dict that, when passed through
``parse_plain_event``, yields a valid ``StructuredSignalInput``.

Contract rules:
- Returns a dict matching Plain's ``thread.created`` or ``email.received``/
  ``email.sent`` event shape (the ``data`` field of a Plain webhook).
- Accepts a seeded ``random.Random`` — no module-level random calls.
- Accepts ``now: datetime`` — no ``datetime.now()`` calls.
- ``external_id`` is deterministic: uuid5(NAMESPACE_DNS,
  "{scenario_name}:ticket:{signal_index}").

Variability axes honoured:
  contact_diversity      — single (dominant), multi (cc'd colleagues)
  contact_email_origin   — corporate / personal_email / mixed
  email_tone             — formal / technical / casual / escalation / apologetic
                           (escalation + apologetic are most ticket-shaped)
  message_length         — short / paragraph / multi (tickets skew shorter than email)
  sentiment_trajectory   — drives tone across the spec's signal sequence
  concern_topic          — selects a topically distinct template family

Threading:
  Even-indexed signals get ``thread.created`` (first contact).
  Odd-indexed signals get ``email.received`` or ``email.sent`` in the same thread.
  Outbound (``email.sent``) appears roughly 25% of the time for odd-indexed signals.
"""

import random
import uuid
from datetime import datetime

from src.synthetic.generators.email import (
    _FIRST_NAMES,
    _FREE_MAIL_DOMAINS,
    _LAST_NAMES,
    _TEMPLATES,
    _TOPICAL_TEMPLATES,
)
from src.synthetic.scenario import AxesSpec, SignalSpec

# ---------------------------------------------------------------------------
# Ticket-specific topic phrases (short, support-desk flavoured)
# ---------------------------------------------------------------------------

_TICKET_TOPICS = [
    "API integration issue",
    "login / SSO failure",
    "export not working",
    "permission error",
    "billing discrepancy",
    "slow response times",
    "data sync delay",
    "webhook not firing",
    "dashboard loading error",
    "onboarding question",
    "upgrade path",
    "CSV import failure",
    "report generation stuck",
    "access provisioning request",
    "contract / renewal query",
]

_TICKET_SUBJECT_TEMPLATES = {
    "outage":             ["[URGENT] {topic} — production down", "Outage affecting {topic}", "{account_name}: {topic} incident"],
    "feature_gap":        ["Feature request: {topic}", "Missing capability — {topic}", "Can we add {topic}?"],
    "pricing":            ["Billing question about {topic}", "Invoice query — {topic}", "{account_name} pricing question"],
    "utilization_decline": ["Help with {topic} adoption", "Low usage on {topic}", "Re: {topic} training"],
    "competitive":        ["Evaluating alternatives to {topic}", "{topic} comparison question", "Vendor review — {topic}"],
    "success_expansion":  ["Expanding use of {topic}", "Adding teams to {topic}", "Scale question — {topic}"],
    "renewal_pending":    ["Renewal discussion — {topic}", "{account_name} contract renewal", "Upcoming renewal — {topic}"],
    "none":               ["Support request: {topic}", "Question about {topic}", "Help needed with {topic}", "{topic} — {account_name}"],
}


# ---------------------------------------------------------------------------
# Contact pool builder (mirrors email.build_contact_pool; tickets typically
# have fewer participants so "crowded" is capped at 3)
# ---------------------------------------------------------------------------


def build_ticket_contact_pool(
    rng: random.Random,
    axes: AxesSpec,
    primary_domain: str,
) -> list[tuple[str, str]]:
    """Build a fixed contact pool for a ticket SignalSpec.

    Returns [(email, name), ...]:
      single  → 1 contact (dominant for tickets)
      multi   → 2 contacts (reporter + one cc'd colleague)
      crowded → 2-3 contacts (small team escalation; capped below email's 4-6)
    """
    count_map = {"single": 1, "multi": 2, "crowded": rng.randint(2, 3)}
    count = count_map.get(axes.contact_diversity, 1)

    pool: list[tuple[str, str]] = []
    for _ in range(count):
        first = rng.choice(_FIRST_NAMES)
        last = rng.choice(_LAST_NAMES)
        name = f"{first} {last}"

        if axes.contact_email_origin == "corporate":
            domain = primary_domain
        elif axes.contact_email_origin == "personal_email":
            domain = rng.choice(_FREE_MAIL_DOMAINS)
        else:  # "mixed"
            domain = primary_domain if rng.random() < 0.6 else rng.choice(_FREE_MAIL_DOMAINS)

        local = f"{first.lower()}.{last.lower()}"
        pool.append((f"{local}@{domain}", name))

    return pool


# ---------------------------------------------------------------------------
# Tone / body helpers (reuse email template corpus)
# ---------------------------------------------------------------------------


def _resolve_ticket_tone(rng: random.Random, axes: AxesSpec, signal_index_within_spec: int) -> str:
    """Map sentiment_trajectory + email_tone to a register for this ticket signal.

    Ticket-specific twist: sentiment_trajectory == "declining" ends in escalation
    faster than email (tickets escalate quickly); "recovering" starts at apologetic.
    """
    traj = axes.sentiment_trajectory
    base = axes.email_tone
    i = signal_index_within_spec

    if traj == "flat":
        return base
    elif traj == "declining":
        if i < 2:
            return "technical"
        elif i < 5:
            return "apologetic"
        else:
            return "escalation"
    elif traj == "recovering":
        if i < 2:
            return "escalation"
        elif i < 5:
            return "apologetic"
        else:
            return "technical"
    elif traj == "oscillating":
        return "casual" if i % 2 == 0 else "escalation"
    elif traj == "sudden_escalation":
        return "escalation" if i >= 4 else "technical"
    return base


def _build_ticket_body(
    rng: random.Random,
    axes: AxesSpec,
    contact_name: str,
    account_name: str,
    topic: str,
    register: str,
) -> str:
    """Build a ticket message body.

    message_length governs size:
      short     → first sentence (~1 line)
      paragraph → one template (~2-4 lines) — default for tickets
      multi     → two templates concatenated
      chain     → paragraph (no quoted-reply for tickets; same length)
    """
    concern_topic = getattr(axes, "concern_topic", "none")
    if concern_topic != "none" and concern_topic in _TOPICAL_TEMPLATES:
        topic_family = _TOPICAL_TEMPLATES[concern_topic]
        templates = topic_family.get(register, topic_family.get("casual", next(iter(topic_family.values()))))
    else:
        templates = _TEMPLATES.get(register, _TEMPLATES["technical"])

    template = rng.choice(templates)
    body = template.format(contact_name=contact_name, account_name=account_name, topic=topic)

    if axes.message_length == "short":
        period_pos = body.find(".")
        if period_pos > 20:
            body = body[: period_pos + 1]
        if len(body) > 120:
            body = body[:100]
    elif axes.message_length == "multi":
        second = rng.choice(templates).format(
            contact_name=contact_name, account_name=account_name, topic=topic
        )
        body = f"{body}\n\n{second}"
    # "paragraph" and "chain" both produce a single paragraph for tickets

    if not body.strip():
        body = f"Support request from {account_name} regarding {topic}."

    return body


def _build_subject(rng: random.Random, axes: AxesSpec, topic: str, account_name: str) -> str:
    concern_topic = getattr(axes, "concern_topic", "none")
    templates = _TICKET_SUBJECT_TEMPLATES.get(concern_topic, _TICKET_SUBJECT_TEMPLATES["none"])
    tmpl = rng.choice(templates)
    return tmpl.format(topic=topic, account_name=account_name)


# ---------------------------------------------------------------------------
# Plain event shape builders
# ---------------------------------------------------------------------------


def _thread_id_for(scenario_name: str, spec_account_slug: str, signal_index: int) -> str:
    """Deterministic Plain thread ID for a ticket.

    The first signal in a spec opens a new thread; subsequent signals in the
    same spec continue the same thread to simulate a support conversation.
    Signal 0 → thread_0, signals 1+ → thread_0 (same thread).
    """
    return f"thr_{uuid.uuid5(uuid.NAMESPACE_DNS, f'{scenario_name}:thr:{spec_account_slug}')}"


def _build_thread_created_event(
    event_id: str,
    thread_id: str,
    timestamp: str,
    workspace_id_plain: str,
    customer_email: str,
    customer_name: str,
    subject: str,
) -> dict:
    """Minimal Plain ``thread.created`` event shape."""
    return {
        "id": event_id,
        "type": "thread.created",
        "timestamp": timestamp,
        "workspaceId": workspace_id_plain,
        "payload": {
            "thread": {
                "id": thread_id,
                "title": subject,
            },
            "customer": {
                "email": {"email": customer_email},
                "fullName": customer_name,
            },
        },
        "webhookMetadata": {},
    }


def _build_email_received_event(
    event_id: str,
    thread_id: str,
    timestamp: str,
    workspace_id_plain: str,
    customer_email: str,
    customer_name: str,
    subject: str,
    body: str,
) -> dict:
    return {
        "id": event_id,
        "type": "email.received",
        "timestamp": timestamp,
        "workspaceId": workspace_id_plain,
        "payload": {
            "thread": {"id": thread_id},
            "email": {
                "subject": subject,
                "textContent": body,
                "from_": {
                    "email": customer_email,
                    "name": customer_name,
                },
            },
        },
        "webhookMetadata": {},
    }


def _build_email_sent_event(
    event_id: str,
    thread_id: str,
    timestamp: str,
    workspace_id_plain: str,
    customer_email: str,
    customer_name: str,
    subject: str,
    body: str,
) -> dict:
    return {
        "id": event_id,
        "type": "email.sent",
        "timestamp": timestamp,
        "workspaceId": workspace_id_plain,
        "payload": {
            "thread": {"id": thread_id},
            "email": {
                "subject": subject,
                "textContent": body,
                "to": [{"email": customer_email, "name": customer_name}],
            },
        },
        "webhookMetadata": {},
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_ticket_payload(
    spec: SignalSpec,
    rng: random.Random,
    now: datetime,
    signal_index: int,
    scenario_name: str,
    account_name: str,
    primary_domain: str,
    signal_index_within_spec: int = 0,
    contact_pool: list[tuple[str, str]] | None = None,
) -> dict:
    """Generate a single Plain-shaped event dict for a ticket signal.

    The returned dict is the full Plain webhook body (containing ``id``,
    ``type``, ``timestamp``, ``workspaceId``, and ``payload``).  It can be
    passed directly to ``parse_plain_event`` to produce a
    ``StructuredSignalInput``.

    Args:
        spec:                    SignalSpec driving this signal's axes.
        rng:                     Seeded Random instance.
        now:                     Timestamp for this signal — no datetime.now().
        signal_index:            Zero-based index across the full scenario; used for uuid5.
        scenario_name:           Used to derive the deterministic external_id.
        account_name:            Human-readable account name for template substitution.
        primary_domain:          Primary domain for corporate email addresses.
        signal_index_within_spec: Position within this spec's count; drives tone drift.
        contact_pool:            Pre-built pool of (email, name) tuples for this spec.
                                 When None a single-contact pool is built on the fly.

    Returns:
        dict — Plain webhook body ready for ``parse_plain_event``.
    """
    axes = spec.axes

    # --- Contact pool ---
    if contact_pool is None:
        contact_pool = build_ticket_contact_pool(rng, axes, primary_domain)

    # Primary reporter is pool[0]; additional contacts are cc'd (not yet surfaced
    # in StructuredSignalInput v1 but kept in the payload for fidelity).
    customer_email, customer_name = contact_pool[0]

    # --- Deterministic IDs ---
    event_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{scenario_name}:ticket:{signal_index}"))
    thread_id = _thread_id_for(scenario_name, spec.account_slug, signal_index)

    # --- Timestamp ---
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Plain workspace ID (synthetic, per-scenario) ---
    workspace_id_plain = f"worksp_{uuid.uuid5(uuid.NAMESPACE_DNS, scenario_name).hex[:16]}"

    # --- Topic, tone, body ---
    topic = rng.choice(_TICKET_TOPICS)
    register = _resolve_ticket_tone(rng, axes, signal_index_within_spec)
    body = _build_ticket_body(rng, axes, customer_name.split()[0], account_name, topic, register)
    subject = _build_subject(rng, axes, topic, account_name)

    # --- Event type selection ---
    # First signal in any spec always opens a thread.
    # Odd-indexed follow-ups: 25% chance of outbound (email.sent), else inbound.
    if signal_index_within_spec == 0:
        event_type = "thread.created"
    elif rng.random() < 0.25:
        event_type = "email.sent"
    else:
        event_type = "email.received"

    if event_type == "thread.created":
        return _build_thread_created_event(
            event_id, thread_id, timestamp, workspace_id_plain,
            customer_email, customer_name, subject,
        )
    elif event_type == "email.received":
        return _build_email_received_event(
            event_id, thread_id, timestamp, workspace_id_plain,
            customer_email, customer_name, subject, body,
        )
    else:  # email.sent
        return _build_email_sent_event(
            event_id, thread_id, timestamp, workspace_id_plain,
            customer_email, customer_name, subject, body,
        )
