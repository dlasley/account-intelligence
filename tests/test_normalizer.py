"""
Normalizer unit tests — DB calls are patched; no Supabase connection required.
"""

import json
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import NAMESPACE_DNS, uuid4, uuid5

import pytest
from pydantic import ValidationError

from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
from src.domain.signal import SourceType
from src.pipeline.normalizer import InboundPayload, normalize

_WS_ID = uuid5(NAMESPACE_DNS, "elicit")
_INTERNAL_DOMAINS = ["elicit.org"]

_PAYLOAD = {
    "external_id": "fixture-test-001",
    "source_type": "json_fixture",
    "direction": "inbound",
    "channel": "email",
    "occurred_at": "2026-03-15T10:23:00Z",
    "subject": "Test signal",
    "body": "Hello, this is a test.",
    "from_email": "priya.sharma@formationbio.com",
    "from_name": "Priya Sharma",
    "to_emails": ["elicit@signal.example.com"],
    "thread_id": "thread-test-001",
    "in_reply_to": None,
}


def _make_event(payload: dict | None = None) -> RawInboundEvent:
    p = payload or _PAYLOAD
    return RawInboundEvent(
        id=uuid4(),
        workspace_id=_WS_ID,
        received_at=datetime.now(UTC),
        source_type=SourceType.JSON_FIXTURE,
        raw_payload=json.dumps(p),
        parse_status=ParseStatus.PENDING,
        signal_id=None,
        error_detail=None,
        processed_at=None,
    )


def _run_normalize(event: RawInboundEvent, internal_domains=None):
    """Patch all DB calls and run normalize(), returning the result."""
    domains = internal_domains if internal_domains is not None else _INTERNAL_DOMAINS
    with (
        patch("src.pipeline.normalizer.get_account_by_email_domain", return_value=None),
        patch("src.pipeline.normalizer.upsert_contact", side_effect=lambda _c, contact: contact),
        patch(
            "src.pipeline.normalizer.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.normalizer.insert_audit_event"),
    ):
        return normalize(event, _WS_ID, domains, client=None)


def test_normalize_returns_signal_with_correct_external_id():
    result = _run_normalize(_make_event())
    assert result.signal.external_id == "fixture-test-001"


def test_normalize_author_contact_extracted():
    result = _run_normalize(_make_event())
    assert result.author_contact.email == "priya.sharma@formationbio.com"
    assert result.author_contact.display_name == "Priya Sharma"


def test_normalize_recipient_contacts_extracted():
    result = _run_normalize(_make_event())
    assert len(result.recipient_contacts) == 1
    assert result.recipient_contacts[0].email == "elicit@signal.example.com"


def test_normalize_is_internal_false_for_external_sender():
    result = _run_normalize(_make_event())
    assert result.author_contact.is_internal is False


def test_normalize_internal_sender_author_contact_is_none():
    # Internal sender → outbound signal; no contact record is created for the CSM
    payload = dict(_PAYLOAD, from_email="engineer@elicit.org")
    result = _run_normalize(_make_event(payload))
    assert result.author_contact is None


def test_normalize_occurred_at_is_tz_aware():
    result = _run_normalize(_make_event())
    assert result.signal.occurred_at.tzinfo is not None


def test_normalize_occurred_at_value():
    result = _run_normalize(_make_event())
    assert result.signal.occurred_at == datetime(2026, 3, 15, 10, 23, 0, tzinfo=UTC)


def test_normalize_workspace_id_set():
    result = _run_normalize(_make_event())
    assert result.signal.workspace_id == _WS_ID


def test_normalize_account_id_is_none_before_routing():
    result = _run_normalize(_make_event())
    assert result.signal.account_id is None


def test_normalize_routing_fields_are_none_before_routing():
    result = _run_normalize(_make_event())
    assert result.signal.routing_method is None
    assert result.signal.routing_confidence is None
    assert result.signal.routing_warning is None


def test_normalize_upserts_one_contact_per_recipient():
    payload = dict(_PAYLOAD, to_emails=["a@elicit.org", "b@elicit.org"])
    with (
        patch("src.pipeline.normalizer.get_account_by_email_domain", return_value=None),
        patch(
            "src.pipeline.normalizer.upsert_contact",
            side_effect=lambda _c, contact: contact,
        ) as mock_upsert,
        patch(
            "src.pipeline.normalizer.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.normalizer.insert_audit_event"),
    ):
        normalize(_make_event(payload), _WS_ID, _INTERNAL_DOMAINS, client=None)
    # 1 author + 2 recipients = 3 upsert calls
    assert mock_upsert.call_count == 3


# --- InboundPayload validation tests ---


def test_inbound_payload_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        InboundPayload.model_validate({"from_email": "a@b.com"})


def test_inbound_payload_rejects_malformed_email():
    with pytest.raises(ValidationError):
        InboundPayload.model_validate(dict(_PAYLOAD, from_email="not-an-email"))


def test_inbound_payload_parses_valid_payload():
    p = InboundPayload.model_validate(_PAYLOAD)
    assert p.from_email == "priya.sharma@formationbio.com"
    assert p.body == "Hello, this is a test."
    assert p.to_emails == ["elicit@signal.example.com"]


def test_inbound_payload_rejects_empty_from_email():
    with pytest.raises(ValidationError):
        InboundPayload.model_validate(dict(_PAYLOAD, from_email=""))


def test_inbound_payload_rejects_whitespace_body():
    with pytest.raises(ValidationError):
        InboundPayload.model_validate(dict(_PAYLOAD, body="   "))
