"""Plain webhook payload adapter (ADR-020 D7, Phase 2).

Maps Plain's native event shape onto StructuredSignalInput.
Supported v1 event types: thread.created, email.received, email.sent.
All other event types return None — the handler logs and returns 200.

Plain event shape:
    {
        "id": "<event-uuid>",
        "type": "thread.created" | "email.received" | "email.sent" | ...,
        "timestamp": "<ISO 8601>",
        "workspaceId": "worksp_<alphanum>",
        "payload": { ... },
        "webhookMetadata": { ... }
    }

external_id = "plain:<body['id']>"   (dedup key per ADR-020 D6)
thread_id   = body['payload']['thread']['id']   (for signal grouping)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from src.domain.signal import Direction
from src.pipeline.structured_signal import SignalParticipant, StructuredSignalInput

logger = logging.getLogger(__name__)

# Event types the v1 adapter handles. Anything outside this set returns None.
_HANDLED_TYPES = {"thread.created", "email.received", "email.sent"}


def parse_plain_event(
    body: dict,
    event_type: str,
    credential_id: UUID,  # reserved for future per-event tracing; unused by pure parser
) -> StructuredSignalInput | None:
    """Map a Plain webhook body to StructuredSignalInput.

    Returns None for unhandled event types (handler should return 200 with event_skipped).
    Raises ValueError for structurally malformed payloads (missing required fields).
    """
    if event_type not in _HANDLED_TYPES:
        logger.info("plain_event_skipped type=%s", event_type)
        return None

    try:
        event_id = body["id"]
        timestamp_raw = body["timestamp"]
        payload = body["payload"]
    except KeyError as exc:
        raise ValueError(f"plain event missing required field: {exc}") from exc

    occurred_at = _parse_timestamp(timestamp_raw)
    external_id = f"plain:{event_id}"

    thread_id: str | None = None
    try:
        thread_id = payload["thread"]["id"]
    except (KeyError, TypeError):
        pass  # thread_id is best-effort for grouping

    if event_type == "thread.created":
        return _parse_thread_created(payload, external_id, occurred_at, thread_id)
    elif event_type == "email.received":
        return _parse_email_received(payload, external_id, occurred_at, thread_id)
    else:  # email.sent
        return _parse_email_sent(payload, external_id, occurred_at, thread_id)


# ─── Per-event-type parsers ──────────────────────────────────────────────────


def _parse_thread_created(
    payload: dict, external_id: str, occurred_at: datetime, thread_id: str | None
) -> StructuredSignalInput:
    try:
        thread = payload["thread"]
        subject = thread.get("title") or ""
        customer = payload["customer"]
        customer_email = customer["email"]["email"]
        customer_name = _customer_display_name(customer)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"thread.created payload malformed: {exc}") from exc

    participants = [SignalParticipant(email=customer_email, name=customer_name, role="customer")]
    metadata = {"plain_thread_id": thread_id, "plain_event_type": "thread.created"}

    return StructuredSignalInput(
        external_id=external_id,
        kind="ticket",
        occurred_at=occurred_at,
        body="",  # no message body on thread.created itself
        participants=participants,
        subject=subject,
        direction=Direction.INBOUND,
        thread_id=thread_id,
        metadata=metadata,
    )


def _parse_email_received(
    payload: dict, external_id: str, occurred_at: datetime, thread_id: str | None
) -> StructuredSignalInput:
    try:
        email = payload["email"]
        subject = email.get("subject") or ""
        body_text = email.get("textContent") or ""
        from_ = email["from_"]
        customer_email = from_["email"]
        customer_name = from_.get("name")
    except (KeyError, TypeError) as exc:
        raise ValueError(f"email.received payload malformed: {exc}") from exc

    participants = [SignalParticipant(email=customer_email, name=customer_name, role="customer")]
    metadata = {"plain_thread_id": thread_id, "plain_event_type": "email.received"}

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


def _parse_email_sent(
    payload: dict, external_id: str, occurred_at: datetime, thread_id: str | None
) -> StructuredSignalInput:
    try:
        email = payload["email"]
        subject = email.get("subject") or ""
        body_text = email.get("textContent") or ""
        to_list = email["to"]
        # First recipient is the customer we sent to
        recipient = to_list[0]
        customer_email = recipient["email"]
        customer_name = recipient.get("name")
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError(f"email.sent payload malformed: {exc}") from exc

    participants = [SignalParticipant(email=customer_email, name=customer_name, role="customer")]
    metadata = {"plain_thread_id": thread_id, "plain_event_type": "email.sent"}

    return StructuredSignalInput(
        external_id=external_id,
        kind="ticket",
        occurred_at=occurred_at,
        body=body_text,
        participants=participants,
        subject=subject,
        direction=Direction.OUTBOUND,
        thread_id=thread_id,
        metadata=metadata,
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _parse_timestamp(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _customer_display_name(customer: dict) -> str | None:
    """Extract a display name from Plain's customer object."""
    full_name = customer.get("fullName")
    if full_name:
        return full_name
    short_name = customer.get("shortName")
    return short_name or None
