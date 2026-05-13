# ruff: noqa: E501
"""Pylon ticket signal generator (ADR-020 Phase 4.5).

Produces a Pylon-webhook-shaped dict that, when passed through
``parse_pylon_event``, yields a valid ``StructuredSignalInput``.

Contract rules:
- Returns a dict matching Pylon's ``{"data": {...}}`` event envelope shape.
- Accepted event types: ``issue.created``, ``issue.message_added``,
  ``issue.status_changed`` (recognized-but-skipped — tests the skip path).
- Accepts a seeded ``random.Random`` — no module-level random calls.
- Accepts ``now: datetime`` — no ``datetime.now()`` calls.
- ``data.id`` is deterministic: uuid5(NAMESPACE_DNS,
  "{scenario_name}:pylon:{signal_index}").
- ``data.issue.id`` is deterministic per-spec thread:
  uuid5(NAMESPACE_DNS, "{scenario_name}:pylon-issue:{spec_account_slug}").

Variability axes honoured:
  contact_diversity      — single (dominant), multi (cc'd colleagues)
  contact_email_origin   — corporate / personal_email / mixed
  email_tone             — formal / technical / casual / escalation / apologetic
  message_length         — short / paragraph / multi / chain
  sentiment_trajectory   — drives tone across the spec's signal sequence
  concern_topic          — selects a topically distinct template family

Event type distribution:
  signal_index_within_spec == 0 → always ``issue.created``
  odd-indexed follow-ups: 20% chance of ``issue.status_changed`` (skipped),
    else 25% of remaining → ``issue.message_added`` with author.type="agent",
    else → ``issue.message_added`` with author.type="customer"

Direction (derived by parse_pylon_event):
  issue.created      → INBOUND (customer opens issue)
  issue.message_added, author.type="customer"  → INBOUND
  issue.message_added, author.type="agent"     → OUTBOUND
  issue.status_changed → None (skip; not a customer interaction signal)
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
from src.synthetic.generators.ticket_plain import (
    _TICKET_TOPICS,
    _build_subject,
    _resolve_ticket_tone,
)
from src.synthetic.scenario import AxesSpec, SignalSpec

# ---------------------------------------------------------------------------
# Contact pool builder (same interface as ticket_plain; same cap logic)
# ---------------------------------------------------------------------------


def build_pylon_contact_pool(
    rng: random.Random,
    axes: AxesSpec,
    primary_domain: str,
) -> list[tuple[str, str]]:
    """Build a fixed contact pool for a Pylon SignalSpec.

    Returns [(email, name), ...]:
      single  → 1 contact
      multi   → 2 contacts (reporter + one colleague)
      crowded → 2-3 contacts (small team escalation; capped)
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
# Body builder (reuses the email template corpus via the same helpers as Plain)
# ---------------------------------------------------------------------------


def _build_pylon_body(
    rng: random.Random,
    axes: AxesSpec,
    contact_name: str,
    account_name: str,
    topic: str,
    register: str,
) -> str:
    """Build a Pylon message body using the same template corpus as ticket_plain."""
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
    # "paragraph" and "chain" both produce a single paragraph

    if not body.strip():
        body = f"Support request from {account_name} regarding {topic}."

    return body


# ---------------------------------------------------------------------------
# Pylon event shape builders
# ---------------------------------------------------------------------------


def _issue_id_for(scenario_name: str, spec_account_slug: str) -> str:
    """Deterministic Pylon issue ID for a spec (shared across all signals in spec)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{scenario_name}:pylon-issue:{spec_account_slug}"))


def _build_issue_created_event(
    event_id: str,
    issue_id: str,
    timestamp: str,
    customer_email: str,
    customer_name: str | None,
    subject: str,
    body: str,
) -> dict:
    """Pylon ``issue.created`` envelope."""
    return {
        "data": {
            "id": event_id,
            "type": "issue.created",
            "timestamp": timestamp,
            "issue": {
                "id": issue_id,
                "title": subject,
                "requester": {
                    "email": customer_email,
                    "name": customer_name,
                },
                "messages": [
                    {
                        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{event_id}:msg:0")),
                        "body": body,
                        "author": {
                            "type": "customer",
                            "email": customer_email,
                            "name": customer_name,
                        },
                    }
                ],
            },
        }
    }


def _build_issue_message_added_event(
    event_id: str,
    issue_id: str,
    timestamp: str,
    customer_email: str,
    customer_name: str | None,
    subject: str,
    body: str,
    author_type: str,  # "customer" | "agent"
    agent_email: str = "support@internal.example.com",
) -> dict:
    """Pylon ``issue.message_added`` envelope.

    For OUTBOUND (agent reply): the message author is "agent"; the
    requester on the issue tracks the original customer.
    For INBOUND (customer follow-up): the message author is "customer".
    """
    if author_type == "agent":
        author_email = agent_email
        author_name = "Support Agent"
    else:
        author_email = customer_email
        author_name = customer_name

    return {
        "data": {
            "id": event_id,
            "type": "issue.message_added",
            "timestamp": timestamp,
            "issue": {
                "id": issue_id,
                "title": subject,
                "requester": {
                    "email": customer_email,
                    "name": customer_name,
                },
                "messages": [
                    {
                        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{event_id}:msg:0")),
                        "body": body,
                        "author": {
                            "type": author_type,
                            "email": author_email,
                            "name": author_name,
                        },
                    }
                ],
            },
        }
    }


def _build_issue_status_changed_event(
    event_id: str,
    issue_id: str,
    timestamp: str,
    subject: str,
) -> dict:
    """Pylon ``issue.status_changed`` envelope.

    parse_pylon_event skips this type (returns None); included to test the skip path.
    """
    return {
        "data": {
            "id": event_id,
            "type": "issue.status_changed",
            "timestamp": timestamp,
            "issue": {
                "id": issue_id,
                "title": subject,
                "status": "resolved",
            },
        }
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_pylon_ticket_payload(
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
    """Generate a single Pylon-shaped event dict for a ticket signal.

    The returned dict is a ``{"data": {...}}`` Pylon webhook envelope.
    It can be passed directly to ``parse_pylon_event`` to produce a
    ``StructuredSignalInput``.

    Args:
        spec:                    SignalSpec driving this signal's axes.
        rng:                     Seeded Random instance.
        now:                     Timestamp for this signal — no datetime.now().
        signal_index:            Zero-based index across the full scenario; used for uuid5.
        scenario_name:           Used to derive the deterministic event id.
        account_name:            Human-readable account name for template substitution.
        primary_domain:          Primary domain for corporate email addresses.
        signal_index_within_spec: Position within this spec's count; drives tone drift.
        contact_pool:            Pre-built pool of (email, name) tuples for this spec.
                                 When None a single-contact pool is built on the fly.

    Returns:
        dict — Pylon webhook body ready for ``parse_pylon_event``.
    """
    axes = spec.axes

    # --- Contact pool ---
    if contact_pool is None:
        contact_pool = build_pylon_contact_pool(rng, axes, primary_domain)

    customer_email, customer_name = contact_pool[0]

    # --- Deterministic IDs ---
    event_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{scenario_name}:pylon:{signal_index}"))
    issue_id = _issue_id_for(scenario_name, spec.account_slug)

    # --- Timestamp ---
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Topic, tone, body ---
    topic = rng.choice(_TICKET_TOPICS)
    register = _resolve_ticket_tone(rng, axes, signal_index_within_spec)
    body = _build_pylon_body(rng, axes, customer_name.split()[0], account_name, topic, register)
    subject = _build_subject(rng, axes, topic, account_name)

    # --- Event type selection ---
    # signal_index_within_spec == 0: always issue.created (new issue from customer)
    # odd-indexed follow-ups:
    #   20% → issue.status_changed (skipped by adapter — tests skip path)
    #   of the remaining 80%: 25% → issue.message_added (agent reply = OUTBOUND)
    #                         75% → issue.message_added (customer follow-up = INBOUND)
    if signal_index_within_spec == 0:
        event_type = "issue.created"
    else:
        roll = rng.random()
        if roll < 0.20:
            event_type = "issue.status_changed"
        elif roll < 0.40:  # 0.20 + 0.20 = top 20% of the 80% remaining → ~25% of non-status
            event_type = "issue.message_added:agent"
        else:
            event_type = "issue.message_added:customer"

    if event_type == "issue.created":
        return _build_issue_created_event(
            event_id, issue_id, timestamp,
            customer_email, customer_name, subject, body,
        )
    elif event_type == "issue.status_changed":
        return _build_issue_status_changed_event(event_id, issue_id, timestamp, subject)
    elif event_type == "issue.message_added:agent":
        return _build_issue_message_added_event(
            event_id, issue_id, timestamp,
            customer_email, customer_name, subject, body,
            author_type="agent",
        )
    else:  # issue.message_added:customer
        return _build_issue_message_added_event(
            event_id, issue_id, timestamp,
            customer_email, customer_name, subject, body,
            author_type="customer",
        )
