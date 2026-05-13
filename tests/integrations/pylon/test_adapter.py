"""Unit tests for src.integrations.pylon.adapter.parse_pylon_event."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.domain.signal import Direction
from src.integrations.pylon.adapter import parse_pylon_event

_CRED_ID = uuid4()

# ─── Fixture helpers ──────────────────────────────────────────────────────────


def _issue_created_body(
    *,
    event_id: str = "evt_pylon_001",
    workspace_id: str = "pylon_ws_abc",
    timestamp: str = "2026-05-08T10:00:00Z",
    issue_id: str = "issue_001",
    title: str = "Cannot access dashboard",
    requester_email: str = "alice@acme.com",
    requester_name: str = "Alice Smith",
    first_message_body: str = "I keep getting a 403 when I log in.",
) -> dict:
    return {
        "data": {
            "id": event_id,
            "type": "issue.created",
            "timestamp": timestamp,
            "workspace_id": workspace_id,
            "issue": {
                "id": issue_id,
                "title": title,
                "requester": {"email": requester_email, "name": requester_name},
                "messages": [
                    {
                        "id": "msg_001",
                        "body": first_message_body,
                        "author": {
                            "type": "customer",
                            "email": requester_email,
                            "name": requester_name,
                        },
                    }
                ],
            },
        }
    }


def _issue_message_added_body(
    *,
    event_id: str = "evt_pylon_002",
    workspace_id: str = "pylon_ws_abc",
    timestamp: str = "2026-05-08T11:00:00Z",
    issue_id: str = "issue_001",
    title: str = "Cannot access dashboard",
    requester_email: str = "alice@acme.com",
    requester_name: str = "Alice Smith",
    author_type: str = "customer",
    author_email: str = "alice@acme.com",
    author_name: str = "Alice Smith",
    message_body: str = "Still seeing the error after clearing cache.",
) -> dict:
    return {
        "data": {
            "id": event_id,
            "type": "issue.message_added",
            "timestamp": timestamp,
            "workspace_id": workspace_id,
            "issue": {
                "id": issue_id,
                "title": title,
                "requester": {"email": requester_email, "name": requester_name},
                "messages": [
                    {
                        "id": "msg_002",
                        "body": message_body,
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


def _status_changed_body(
    *,
    event_id: str = "evt_pylon_003",
    workspace_id: str = "pylon_ws_abc",
    timestamp: str = "2026-05-08T12:00:00Z",
    issue_id: str = "issue_001",
) -> dict:
    return {
        "data": {
            "id": event_id,
            "type": "issue.status_changed",
            "timestamp": timestamp,
            "workspace_id": workspace_id,
            "issue": {
                "id": issue_id,
                "title": "Cannot access dashboard",
                "requester": {"email": "alice@acme.com", "name": "Alice Smith"},
                "messages": [],
            },
        }
    }


# ─── issue.created ────────────────────────────────────────────────────────────


def test_parse_issue_created():
    body = _issue_created_body()
    result = parse_pylon_event(body, "issue.created", _CRED_ID)

    assert result is not None
    assert result.kind == "ticket"
    assert result.direction == Direction.INBOUND
    assert result.subject == "Cannot access dashboard"
    assert result.body == "I keep getting a 403 when I log in."
    assert len(result.participants) == 1
    assert result.participants[0].email == "alice@acme.com"
    assert result.participants[0].name == "Alice Smith"
    assert result.participants[0].role == "customer"


def test_parse_issue_created_external_id_namespaced():
    body = _issue_created_body(event_id="evt_abc123")
    result = parse_pylon_event(body, "issue.created", _CRED_ID)
    assert result is not None
    assert result.external_id == "pylon:evt_abc123"


def test_parse_issue_created_thread_id_namespaced():
    body = _issue_created_body(issue_id="issue_xyz")
    result = parse_pylon_event(body, "issue.created", _CRED_ID)
    assert result is not None
    assert result.thread_id == "pylon:issue_xyz"


def test_parse_issue_created_occurred_at_utc():
    body = _issue_created_body(timestamp="2026-05-08T10:00:00Z")
    result = parse_pylon_event(body, "issue.created", _CRED_ID)
    assert result is not None
    assert result.occurred_at == datetime(2026, 5, 8, 10, 0, 0, tzinfo=UTC)


def test_parse_issue_created_no_messages_empty_body():
    body = _issue_created_body()
    body["data"]["issue"]["messages"] = []
    result = parse_pylon_event(body, "issue.created", _CRED_ID)
    assert result is not None
    assert result.body == ""


# ─── issue.message_added — customer message (INBOUND) ───────────────────────


def test_parse_issue_message_added_customer():
    body = _issue_message_added_body(
        author_type="customer",
        author_email="alice@acme.com",
        message_body="Still seeing the error.",
    )
    result = parse_pylon_event(body, "issue.message_added", _CRED_ID)

    assert result is not None
    assert result.kind == "ticket"
    assert result.direction == Direction.INBOUND
    assert result.body == "Still seeing the error."
    assert result.participants[0].email == "alice@acme.com"
    assert result.participants[0].role == "customer"


def test_parse_issue_message_added_external_id_namespaced():
    body = _issue_message_added_body(event_id="evt_msg_xyz")
    result = parse_pylon_event(body, "issue.message_added", _CRED_ID)
    assert result is not None
    assert result.external_id == "pylon:evt_msg_xyz"


# ─── issue.message_added — agent message (OUTBOUND) ─────────────────────────


def test_parse_issue_message_added_agent_is_outbound():
    body = _issue_message_added_body(
        author_type="agent",
        author_email="support@mycompany.com",
        author_name="Support Agent",
        # requester is the customer
        requester_email="alice@acme.com",
        requester_name="Alice Smith",
        message_body="We have fixed the issue.",
    )
    result = parse_pylon_event(body, "issue.message_added", _CRED_ID)

    assert result is not None
    assert result.direction == Direction.OUTBOUND
    assert result.body == "We have fixed the issue."
    # For agent messages, the customer is the requester
    assert result.participants[0].email == "alice@acme.com"
    assert result.participants[0].role == "customer"


# ─── issue.status_changed — recognized but skipped ───────────────────────────


def test_parse_status_changed_returns_none():
    body = _status_changed_body()
    result = parse_pylon_event(body, "issue.status_changed", _CRED_ID)
    assert result is None


# ─── Unknown event type → None ───────────────────────────────────────────────


def test_parse_unknown_type_returns_none():
    body = {
        "data": {
            "id": "evt_unknown_001",
            "type": "issue.assigned",
            "timestamp": "2026-05-08T10:00:00Z",
            "workspace_id": "pylon_ws_abc",
            "issue": {"id": "issue_001"},
        }
    }
    result = parse_pylon_event(body, "issue.assigned", _CRED_ID)
    assert result is None


def test_parse_another_unknown_type_returns_none():
    body = {
        "data": {
            "id": "evt_sla_001",
            "type": "sla.breached",
            "timestamp": "2026-05-08T10:00:00Z",
            "workspace_id": "pylon_ws_abc",
            "issue": {"id": "issue_001"},
        }
    }
    result = parse_pylon_event(body, "sla.breached", _CRED_ID)
    assert result is None


# ─── Malformed payloads ───────────────────────────────────────────────────────


def test_parse_missing_data_key_raises():
    body = {"id": "evt_001", "type": "issue.created"}
    with pytest.raises((ValueError, KeyError)):
        parse_pylon_event(body, "issue.created", _CRED_ID)


def test_parse_issue_created_missing_requester_raises():
    body = _issue_created_body()
    del body["data"]["issue"]["requester"]
    with pytest.raises(ValueError, match=r"issue\.created payload malformed"):
        parse_pylon_event(body, "issue.created", _CRED_ID)


def test_parse_issue_created_missing_requester_email_raises():
    body = _issue_created_body()
    del body["data"]["issue"]["requester"]["email"]
    with pytest.raises(ValueError, match=r"issue\.created payload malformed"):
        parse_pylon_event(body, "issue.created", _CRED_ID)


def test_parse_issue_message_added_no_messages_raises():
    body = _issue_message_added_body()
    body["data"]["issue"]["messages"] = []
    with pytest.raises(ValueError, match="no messages"):
        parse_pylon_event(body, "issue.message_added", _CRED_ID)


def test_parse_issue_message_added_missing_author_email_raises():
    body = _issue_message_added_body()
    del body["data"]["issue"]["messages"][0]["author"]["email"]
    with pytest.raises(ValueError, match=r"issue\.message_added payload malformed"):
        parse_pylon_event(body, "issue.message_added", _CRED_ID)


# ─── Metadata ─────────────────────────────────────────────────────────────────


def test_issue_created_metadata_contains_issue_id_and_type():
    body = _issue_created_body(issue_id="issue_meta_test")
    result = parse_pylon_event(body, "issue.created", _CRED_ID)
    assert result is not None
    assert result.metadata["pylon_issue_id"] == "issue_meta_test"
    assert result.metadata["pylon_event_type"] == "issue.created"


def test_issue_message_added_metadata_contains_author_type():
    body = _issue_message_added_body(author_type="agent")
    result = parse_pylon_event(body, "issue.message_added", _CRED_ID)
    assert result is not None
    assert result.metadata["pylon_author_type"] == "agent"
