"""Unit tests for src.pipeline.structured_signal.normalize_structured_signal.

DB calls are patched at the call site. No live Supabase required.
"""

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.domain.contact import Contact
from src.domain.signal import Channel, Direction, RoutingMethod, SourceType
from src.pipeline.structured_signal import (
    SignalParticipant,
    StructuredSignalInput,
    normalize_structured_signal,
)

_WS_ID = uuid4()
_WS_NAME = "Acme Corp"
_CRED_ID = uuid4()
_ACCOUNT_ID = uuid4()


def _now() -> datetime:
    return datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)


def _existing_contact(*, account_id=_ACCOUNT_ID, email="alice@example.com") -> Contact:
    now = datetime.now(UTC)
    return Contact(
        id=uuid4(),
        workspace_id=_WS_ID,
        account_id=account_id,
        email=email,
        display_name=None,
        is_internal=False,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def _make_input(**kwargs) -> StructuredSignalInput:
    defaults = dict(
        external_id="plain:evt_001",
        kind="ticket",
        occurred_at=_now(),
        body="Customer needs help with onboarding",
        participants=[SignalParticipant(email="alice@example.com", role="customer")],
        subject="Help request",
        direction=Direction.INBOUND,
        thread_id="thread_001",
        metadata={},
    )
    defaults.update(kwargs)
    return StructuredSignalInput(**defaults)


def _fake_insert(_, signal):
    return signal, False


def _fake_upsert(_, contact):
    return contact


# ─── Routing tests ────────────────────────────────────────────────────────────


def test_normalize_known_contact():
    """Participant email matches existing Contact -> API_KEY_IDENTITY, account_id inherited."""
    contact = _existing_contact()

    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", return_value=contact),
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch("src.pipeline.structured_signal.insert_audit_event"),
    ):
        result = normalize_structured_signal(
            _make_input(),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "plain_webhook_secret",
            client=None,
        )

    assert result.duplicate is False
    assert result.signal.routing_method == RoutingMethod.API_KEY_IDENTITY
    assert result.signal.routing_confidence == 1.0
    assert result.signal.account_id == _ACCOUNT_ID
    assert result.signal.author_contact_id == contact.id


def test_normalize_new_contact_domain_match():
    """New email, domain matches account -> AUTO_DISCOVERY, account_id set."""
    discovered = uuid4()

    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", return_value=None),
        patch(
            "src.pipeline.structured_signal.get_account_by_email_domain",
            return_value=discovered,
        ),
        patch("src.pipeline.structured_signal.upsert_contact", side_effect=_fake_upsert),
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch("src.pipeline.structured_signal.insert_audit_event"),
    ):
        result = normalize_structured_signal(
            _make_input(),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "plain_webhook_secret",
            client=None,
        )

    assert result.signal.routing_method == RoutingMethod.AUTO_DISCOVERY
    assert result.signal.routing_confidence == 0.3
    assert result.signal.account_id == discovered


def test_normalize_new_contact_no_domain():
    """New email, no domain match -> AUTO_DISCOVERY, account_id = None."""
    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", return_value=None),
        patch("src.pipeline.structured_signal.get_account_by_email_domain", return_value=None),
        patch("src.pipeline.structured_signal.upsert_contact", side_effect=_fake_upsert),
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch("src.pipeline.structured_signal.insert_audit_event"),
    ):
        result = normalize_structured_signal(
            _make_input(),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "plain_webhook_secret",
            client=None,
        )

    assert result.signal.routing_method == RoutingMethod.AUTO_DISCOVERY
    assert result.signal.account_id is None


def test_normalize_no_participants():
    """Empty participants list -> UNMATCHED, account_id = None."""
    with (
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch("src.pipeline.structured_signal.insert_audit_event"),
    ):
        result = normalize_structured_signal(
            _make_input(participants=[]),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "plain_webhook_secret",
            client=None,
        )

    assert result.signal.routing_method == RoutingMethod.UNMATCHED
    assert result.signal.routing_confidence == 0.0
    assert result.signal.account_id is None
    assert result.signal.author_contact_id is None


def test_normalize_dedup():
    """Second call with same external_id returns duplicate=True, does not re-audit."""
    contact = _existing_contact()

    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", return_value=contact),
        patch(
            "src.pipeline.structured_signal.insert_signal",
            side_effect=lambda _, signal: (signal, True),
        ),
        patch("src.pipeline.structured_signal.insert_audit_event") as mock_audit,
    ):
        result = normalize_structured_signal(
            _make_input(),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "plain_webhook_secret",
            client=None,
        )

    assert result.duplicate is True
    mock_audit.assert_not_called()


# ─── Inference tests ──────────────────────────────────────────────────────────


def test_source_type_inferred_plain():
    """plain_webhook_secret credential_kind -> SourceType.PLAIN_TICKET."""
    contact = _existing_contact()

    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", return_value=contact),
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch("src.pipeline.structured_signal.insert_audit_event"),
    ):
        result = normalize_structured_signal(
            _make_input(),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "plain_webhook_secret",
            client=None,
        )

    assert result.signal.source_type == SourceType.PLAIN_TICKET


def test_source_type_inferred_granola():
    """granola_api_key credential_kind -> SourceType.GRANOLA_NOTE."""
    contact = _existing_contact()

    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", return_value=contact),
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch("src.pipeline.structured_signal.insert_audit_event"),
    ):
        result = normalize_structured_signal(
            _make_input(kind="meeting_note", direction=Direction.INTERNAL),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "granola_api_key",
            client=None,
        )

    assert result.signal.source_type == SourceType.GRANOLA_NOTE


def test_channel_inferred_ticket():
    """kind='ticket' -> Channel.TICKET."""
    contact = _existing_contact()

    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", return_value=contact),
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch("src.pipeline.structured_signal.insert_audit_event"),
    ):
        result = normalize_structured_signal(
            _make_input(kind="ticket"),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "plain_webhook_secret",
            client=None,
        )

    assert result.signal.channel == Channel.TICKET


def test_channel_inferred_meeting_note():
    """kind='meeting_note' -> Channel.MEETING_NOTE."""
    contact = _existing_contact()

    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", return_value=contact),
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch("src.pipeline.structured_signal.insert_audit_event"),
    ):
        result = normalize_structured_signal(
            _make_input(kind="meeting_note", direction=Direction.INTERNAL),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "granola_api_key",
            client=None,
        )

    assert result.signal.channel == Channel.MEETING_NOTE


# ─── Author selection tests ───────────────────────────────────────────────────


def test_first_customer_role_is_author():
    """When multiple participants, first role='customer' becomes author."""
    internal_c = _existing_contact(email="csm@acme.com")
    customer_c = _existing_contact(email="alice@example.com")

    def _fake_get(_, ws_id, email):
        if "acme" in email:
            return internal_c
        return customer_c

    participants = [
        SignalParticipant(email="csm@acme.com", role="internal"),
        SignalParticipant(email="alice@example.com", role="customer"),
    ]

    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", side_effect=_fake_get),
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch("src.pipeline.structured_signal.insert_audit_event"),
    ):
        result = normalize_structured_signal(
            _make_input(participants=participants),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "plain_webhook_secret",
            client=None,
        )

    assert result.signal.author_contact_id == customer_c.id
    assert internal_c.id in result.signal.recipient_contact_ids


def test_first_participant_is_author_when_no_customer_role():
    """When no participant has role='customer', first participant is author."""
    c1 = _existing_contact(email="csm@acme.com")
    c2 = _existing_contact(email="pm@acme.com")

    def _fake_get(_, ws_id, email):
        if "csm" in email:
            return c1
        return c2

    participants = [
        SignalParticipant(email="csm@acme.com", role="internal"),
        SignalParticipant(email="pm@acme.com", role="internal"),
    ]

    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", side_effect=_fake_get),
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch("src.pipeline.structured_signal.insert_audit_event"),
    ):
        result = normalize_structured_signal(
            _make_input(
                participants=participants, kind="meeting_note", direction=Direction.INTERNAL
            ),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "granola_api_key",
            client=None,
        )

    assert result.signal.author_contact_id == c1.id
    assert c2.id in result.signal.recipient_contact_ids


# ─── Audit fire-and-log test ──────────────────────────────────────────────────


def test_audit_event_failure_does_not_propagate():
    """An exception in insert_audit_event must not fail signal ingestion."""
    contact = _existing_contact()

    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", return_value=contact),
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch(
            "src.pipeline.structured_signal.insert_audit_event",
            side_effect=RuntimeError("db down"),
        ),
    ):
        # Should not raise
        result = normalize_structured_signal(
            _make_input(),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "plain_webhook_secret",
            client=None,
        )

    assert result.duplicate is False


# ─── Signal metadata propagation ─────────────────────────────────────────────


def test_signal_metadata_stored():
    """metadata from StructuredSignalInput is stored in signal.signal_metadata."""
    contact = _existing_contact()
    meta = {"plain_thread_id": "thread_xyz", "plain_event_type": "email.received"}

    with (
        patch("src.pipeline.structured_signal.get_contact_by_email", return_value=contact),
        patch("src.pipeline.structured_signal.insert_signal", side_effect=_fake_insert),
        patch("src.pipeline.structured_signal.insert_audit_event"),
    ):
        result = normalize_structured_signal(
            _make_input(metadata=meta),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "plain_webhook_secret",
            client=None,
        )

    assert result.signal.signal_metadata == meta


# ─── Unknown kind / credential raises ────────────────────────────────────────


def test_unknown_credential_kind_raises():
    with pytest.raises(ValueError, match="unknown credential_kind"):
        normalize_structured_signal(
            _make_input(),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "unknown_vendor",
            client=None,
        )


def test_unknown_input_kind_raises():
    with pytest.raises(ValueError, match="unknown structured signal kind"):
        normalize_structured_signal(
            _make_input(kind="sms"),
            _WS_ID,
            _WS_NAME,
            _CRED_ID,
            "plain_webhook_secret",
            client=None,
        )
