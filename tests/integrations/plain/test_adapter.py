"""Unit tests for src.integrations.plain.adapter.parse_plain_event."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.domain.signal import Direction
from src.integrations.plain.adapter import parse_plain_event

_CRED_ID = uuid4()

# ─── Fixture helpers ──────────────────────────────────────────────────────────


def _thread_created_body(
    *,
    event_id: str = "evt_thread_001",
    workspace_id: str = "worksp_abc123",
    timestamp: str = "2026-05-08T10:00:00Z",
    thread_id: str = "thread_001",
    title: str = "I need help with billing",
    customer_email: str = "alice@acme.com",
    customer_name: str = "Alice Smith",
) -> dict:
    return {
        "id": event_id,
        "type": "thread.created",
        "timestamp": timestamp,
        "workspaceId": workspace_id,
        "payload": {
            "thread": {"id": thread_id, "title": title},
            "customer": {
                "email": {"email": customer_email},
                "fullName": customer_name,
            },
        },
        "webhookMetadata": {},
    }


def _email_received_body(
    *,
    event_id: str = "evt_email_recv_001",
    workspace_id: str = "worksp_abc123",
    timestamp: str = "2026-05-08T11:00:00Z",
    thread_id: str = "thread_002",
    subject: str = "Re: billing question",
    text_content: str = "Thanks for the reply!",
    from_email: str = "bob@acme.com",
    from_name: str = "Bob Jones",
) -> dict:
    return {
        "id": event_id,
        "type": "email.received",
        "timestamp": timestamp,
        "workspaceId": workspace_id,
        "payload": {
            "thread": {"id": thread_id},
            "email": {
                "subject": subject,
                "textContent": text_content,
                "from_": {"email": from_email, "name": from_name},
            },
        },
        "webhookMetadata": {},
    }


def _email_sent_body(
    *,
    event_id: str = "evt_email_sent_001",
    workspace_id: str = "worksp_abc123",
    timestamp: str = "2026-05-08T12:00:00Z",
    thread_id: str = "thread_003",
    subject: str = "Here is your invoice",
    text_content: str = "Please find attached your invoice.",
    to_email: str = "carol@acme.com",
    to_name: str = "Carol White",
) -> dict:
    return {
        "id": event_id,
        "type": "email.sent",
        "timestamp": timestamp,
        "workspaceId": workspace_id,
        "payload": {
            "thread": {"id": thread_id},
            "email": {
                "subject": subject,
                "textContent": text_content,
                "to": [{"email": to_email, "name": to_name}],
            },
        },
        "webhookMetadata": {},
    }


# ─── thread.created ──────────────────────────────────────────────────────────


def test_parse_thread_created():
    body = _thread_created_body()
    result = parse_plain_event(body, "thread.created", _CRED_ID)

    assert result is not None
    assert result.kind == "ticket"
    assert result.direction == Direction.INBOUND
    assert result.subject == "I need help with billing"
    assert result.thread_id == "thread_001"
    assert len(result.participants) == 1
    assert result.participants[0].email == "alice@acme.com"
    assert result.participants[0].name == "Alice Smith"
    assert result.participants[0].role == "customer"


def test_parse_thread_created_external_id_namespaced():
    body = _thread_created_body(event_id="evt_abc")
    result = parse_plain_event(body, "thread.created", _CRED_ID)
    assert result is not None
    assert result.external_id == "plain:evt_abc"


def test_parse_thread_created_occurred_at_utc():
    body = _thread_created_body(timestamp="2026-05-08T10:00:00Z")
    result = parse_plain_event(body, "thread.created", _CRED_ID)
    assert result is not None
    assert result.occurred_at == datetime(2026, 5, 8, 10, 0, 0, tzinfo=UTC)


# ─── email.received ───────────────────────────────────────────────────────────


def test_parse_email_received():
    body = _email_received_body()
    result = parse_plain_event(body, "email.received", _CRED_ID)

    assert result is not None
    assert result.kind == "ticket"
    assert result.direction == Direction.INBOUND
    assert result.subject == "Re: billing question"
    assert result.body == "Thanks for the reply!"
    assert len(result.participants) == 1
    assert result.participants[0].email == "bob@acme.com"
    assert result.participants[0].name == "Bob Jones"
    assert result.participants[0].role == "customer"


def test_parse_email_received_external_id_namespaced():
    body = _email_received_body(event_id="evt_recv_xyz")
    result = parse_plain_event(body, "email.received", _CRED_ID)
    assert result is not None
    assert result.external_id == "plain:evt_recv_xyz"


# ─── email.sent ───────────────────────────────────────────────────────────────


def test_parse_email_sent():
    body = _email_sent_body()
    result = parse_plain_event(body, "email.sent", _CRED_ID)

    assert result is not None
    assert result.kind == "ticket"
    assert result.direction == Direction.OUTBOUND
    assert result.subject == "Here is your invoice"
    assert result.body == "Please find attached your invoice."
    assert len(result.participants) == 1
    assert result.participants[0].email == "carol@acme.com"
    assert result.participants[0].name == "Carol White"
    assert result.participants[0].role == "customer"


def test_parse_email_sent_external_id_namespaced():
    body = _email_sent_body(event_id="evt_sent_xyz")
    result = parse_plain_event(body, "email.sent", _CRED_ID)
    assert result is not None
    assert result.external_id == "plain:evt_sent_xyz"


# ─── Unhandled event type ─────────────────────────────────────────────────────


def test_parse_unhandled_type_returns_none():
    body = {
        "id": "evt_sla_001",
        "type": "sla.breached",
        "timestamp": "2026-05-08T10:00:00Z",
        "workspaceId": "worksp_abc123",
        "payload": {},
        "webhookMetadata": {},
    }
    result = parse_plain_event(body, "sla.breached", _CRED_ID)
    assert result is None


def test_parse_thread_status_change_returns_none():
    body = {
        "id": "evt_status_001",
        "type": "thread.status_transitioned",
        "timestamp": "2026-05-08T10:00:00Z",
        "workspaceId": "worksp_abc123",
        "payload": {},
        "webhookMetadata": {},
    }
    result = parse_plain_event(body, "thread.status_transitioned", _CRED_ID)
    assert result is None


# ─── Malformed payloads ───────────────────────────────────────────────────────


def test_parse_missing_top_level_id_raises():
    body = _thread_created_body()
    del body["id"]
    with pytest.raises(ValueError, match="missing required field"):
        parse_plain_event(body, "thread.created", _CRED_ID)


def test_parse_thread_created_missing_customer_email_raises():
    body = _thread_created_body()
    del body["payload"]["customer"]["email"]
    with pytest.raises(ValueError, match=r"thread\.created payload malformed"):
        parse_plain_event(body, "thread.created", _CRED_ID)


def test_parse_email_received_missing_from_raises():
    body = _email_received_body()
    del body["payload"]["email"]["from_"]
    with pytest.raises(ValueError, match=r"email\.received payload malformed"):
        parse_plain_event(body, "email.received", _CRED_ID)


def test_parse_email_sent_empty_to_raises():
    body = _email_sent_body()
    body["payload"]["email"]["to"] = []
    with pytest.raises(ValueError, match=r"email\.sent payload malformed"):
        parse_plain_event(body, "email.sent", _CRED_ID)


# ─── Metadata ─────────────────────────────────────────────────────────────────


def test_metadata_contains_thread_id_and_event_type():
    body = _email_received_body(thread_id="thread_meta_test", event_id="evt_meta_001")
    result = parse_plain_event(body, "email.received", _CRED_ID)
    assert result is not None
    assert result.metadata["plain_thread_id"] == "thread_meta_test"
    assert result.metadata["plain_event_type"] == "email.received"
