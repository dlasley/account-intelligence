"""Pylon webhook payload adapter (ADR-020 Phase 2.5).

Maps Pylon's native event shape onto StructuredSignalInput.

V1 recognized event types:
  issue.created      — new support issue opened by a customer
  issue.message_added — message posted on an existing issue
  issue.status_changed — skipped (operational state change; not a customer interaction signal)

All other event types return None — the handler logs and returns 200.
Pylon does not publish a complete event type catalog; customers configure which events
to send from the Pylon UI. Unknown types are treated identically to recognized-but-skipped
types: log and return None so the handler responds 200 without retrying.

Pylon event envelope:
    {
        "data": {
            "id": "<event-id>",                  # dedup key
            "type": "issue.created" | ...,
            "timestamp": "<ISO 8601>",
            "issue": {
                "id": "<issue-id>",
                "title": "<title>",
                "requester": {"email": "<email>", "name": "<name>" | null},
                "messages": [
                    {
                        "id": "<msg-id>",
                        "body": "<text>",
                        "author": {
                            "type": "customer" | "agent",
                            "email": "<email>",
                            "name": "<name>" | null
                        }
                    }
                ]
            }
        }
    }

external_id = "pylon:<data.id>"   (dedup key per ADR-020 D6)
thread_id   = "pylon:<data.issue.id>"  (for signal grouping across events on same issue)

Direction logic:
  issue.created      — always INBOUND (customer opens the issue)
  issue.message_added — INBOUND if message author type is "customer"; OUTBOUND if "agent"
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from src.domain.signal import Direction
from src.pipeline.structured_signal import SignalParticipant, StructuredSignalInput

logger = logging.getLogger(__name__)

# Recognized v1 event types. Values not in this dict return None (unknown or skipped).
# Each handler returns StructuredSignalInput | None (None = recognized but intentionally skipped).
_PYLON_EVENT_HANDLERS: dict[str, object] = {
    "issue.created": "_handle_issue_created",
    "issue.message_added": "_handle_issue_message_added",
    # issue.status_changed is recognized but skipped — not a customer interaction signal.
    "issue.status_changed": None,
}


def parse_pylon_event(
    body: dict,
    event_type: str,
    credential_id: UUID,  # reserved for future per-event tracing; unused by pure parser
) -> StructuredSignalInput | None:
    """Map a Pylon webhook body to StructuredSignalInput.

    Returns None for:
    - Recognized-but-skipped event types (issue.status_changed).
    - Unknown event types (Pylon sends events based on customer UI configuration;
      we log and skip anything we don't handle, without retrying).

    Raises ValueError for structurally malformed payloads (missing required fields).
    """
    if event_type not in _PYLON_EVENT_HANDLERS:
        logger.info("pylon_event_unknown type=%s", event_type)
        return None

    handler_name = _PYLON_EVENT_HANDLERS[event_type]
    if handler_name is None:
        logger.info("pylon_event_skipped type=%s", event_type)
        return None

    try:
        data = body["data"]
        event_id = data["id"]
        timestamp_raw = data.get("timestamp") or data.get("created_at", "")
        issue = data["issue"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"pylon event missing required field: {exc}") from exc

    occurred_at = _parse_timestamp(timestamp_raw)
    external_id = f"pylon:{event_id}"
    thread_id = f"pylon:{issue['id']}" if issue.get("id") else None

    if event_type == "issue.created":
        return _handle_issue_created(issue, external_id, occurred_at, thread_id)
    else:  # issue.message_added
        return _handle_issue_message_added(issue, external_id, occurred_at, thread_id)


# ─── Per-event-type handlers ─────────────────────────────────────────────────


def _handle_issue_created(
    issue: dict,
    external_id: str,
    occurred_at: datetime,
    thread_id: str | None,
) -> StructuredSignalInput:
    """New issue opened by a customer — always INBOUND."""
    try:
        subject = issue.get("title") or ""
        requester = issue["requester"]
        customer_email = requester["email"]
        customer_name = requester.get("name")
    except (KeyError, TypeError) as exc:
        raise ValueError(f"issue.created payload malformed: {exc}") from exc

    # Use the first message body if available, otherwise empty
    body_text = ""
    messages = issue.get("messages") or []
    if messages:
        body_text = messages[0].get("body") or ""

    participants = [SignalParticipant(email=customer_email, name=customer_name, role="customer")]
    metadata = {"pylon_issue_id": issue.get("id"), "pylon_event_type": "issue.created"}

    return StructuredSignalInput(
        external_id=external_id,
        kind="ticket",
        occurred_at=occurred_at,
        body=body_text,
        participants=participants,
        subject=subject,
        direction=Direction.INBOUND,
        thread_id=thread_id,
        metadata=metadata,
    )


def _handle_issue_message_added(
    issue: dict,
    external_id: str,
    occurred_at: datetime,
    thread_id: str | None,
) -> StructuredSignalInput:
    """New message on an existing issue.

    Direction:
      INBOUND  — author.type == "customer"
      OUTBOUND — author.type == "agent" (support team reply)
    """
    try:
        subject = issue.get("title") or ""
        # The triggering message is the last entry in the messages list
        messages = issue.get("messages") or []
        if not messages:
            raise ValueError("issue.message_added has no messages")
        message = messages[-1]
        body_text = message.get("body") or ""
        author = message["author"]
        author_type = author.get("type", "customer")
        author_email = author["email"]
        author_name = author.get("name")
    except (KeyError, TypeError) as exc:
        raise ValueError(f"issue.message_added payload malformed: {exc}") from exc

    direction = Direction.OUTBOUND if author_type == "agent" else Direction.INBOUND
    # The participant we track is always the customer side of the exchange.
    # role is always "customer" regardless of direction — we are tracking the customer contact.
    # For INBOUND: author is the customer. For OUTBOUND: requester (if present) is the customer.
    if author_type == "agent":
        requester = issue.get("requester") or {}
        customer_email_resolved = requester.get("email") or author_email
        customer_name_resolved = requester.get("name") or author_name
    else:
        customer_email_resolved = author_email
        customer_name_resolved = author_name

    participants = [
        SignalParticipant(
            email=customer_email_resolved,
            name=customer_name_resolved,
            role="customer",
        )
    ]
    metadata = {
        "pylon_issue_id": issue.get("id"),
        "pylon_event_type": "issue.message_added",
        "pylon_author_type": author_type,
    }

    return StructuredSignalInput(
        external_id=external_id,
        kind="ticket",
        occurred_at=occurred_at,
        body=body_text,
        participants=participants,
        subject=subject,
        direction=direction,
        thread_id=thread_id,
        metadata=metadata,
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _parse_timestamp(raw: str) -> datetime:
    if not raw:
        return datetime.now(UTC)
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
