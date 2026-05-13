"""Unit tests for src.pipeline.product_event.normalize_product_event.

DB calls are patched at the call site. No live Supabase required.
"""

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from src.domain.contact import Contact
from src.domain.signal import RoutingMethod, SourceType
from src.pipeline.product_event import (
    ProductEvent,
    _synthesize_body,
    _synthesize_subject,
    normalize_product_event,
)

_WS_ID = uuid4()
_WS_NAME = "ABC Corp"
_API_KEY_ID = uuid4()
_ACCOUNT_ID = uuid4()


def _existing_contact(*, account_id=_ACCOUNT_ID) -> Contact:
    now = datetime.now(UTC)
    return Contact(
        id=uuid4(),
        workspace_id=_WS_ID,
        account_id=account_id,
        email="priya@example.com",
        display_name=None,
        is_internal=False,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def _make_event(**kwargs) -> ProductEvent:
    defaults = dict(
        contact_email="priya@example.com",
        event_name="feature_activated",
        event_properties={"feature": "export"},
        event_id=None,
        occurred_at=None,
    )
    defaults.update(kwargs)
    return ProductEvent(**defaults)


def test_known_contact_routes_api_key_identity():
    event = _make_event()
    contact = _existing_contact()

    with (
        patch("src.pipeline.product_event.get_contact_by_email", return_value=contact),
        patch(
            "src.pipeline.product_event.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.product_event.insert_audit_event"),
    ):
        result = normalize_product_event(event, _WS_ID, _WS_NAME, _API_KEY_ID, client=None)

    assert result.duplicate is False
    assert result.signal.routing_method == RoutingMethod.API_KEY_IDENTITY
    assert result.signal.account_id == _ACCOUNT_ID
    assert result.signal.author_contact_id == contact.id
    assert result.signal.source_type == SourceType.PRODUCT_EVENT
    assert result.signal.event_name == "feature_activated"
    assert result.signal.event_properties == {"feature": "export"}


def test_new_contact_routes_auto_discovery():
    event = _make_event(contact_email="newperson@example.com")

    def fake_upsert(_c, contact):
        return contact

    with (
        patch("src.pipeline.product_event.get_contact_by_email", return_value=None),
        patch("src.pipeline.product_event.get_account_by_email_domain", return_value=None),
        patch("src.pipeline.product_event.upsert_contact", side_effect=fake_upsert) as mock_upsert,
        patch(
            "src.pipeline.product_event.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.product_event.insert_audit_event"),
    ):
        result = normalize_product_event(event, _WS_ID, _WS_NAME, _API_KEY_ID, client=None)

    assert result.signal.routing_method == RoutingMethod.AUTO_DISCOVERY
    assert result.signal.account_id is None
    mock_upsert.assert_called_once()


def test_no_email_routes_unmatched_no_contact_created():
    event = _make_event(contact_email=None)

    with (
        patch("src.pipeline.product_event.get_contact_by_email") as mock_get,
        patch("src.pipeline.product_event.upsert_contact") as mock_upsert,
        patch(
            "src.pipeline.product_event.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.product_event.insert_audit_event"),
    ):
        result = normalize_product_event(event, _WS_ID, _WS_NAME, _API_KEY_ID, client=None)

    assert result.signal.routing_method == RoutingMethod.UNMATCHED
    assert result.signal.account_id is None
    assert result.signal.author_contact_id is None
    mock_get.assert_not_called()
    mock_upsert.assert_not_called()


def test_duplicate_event_id_returns_duplicate_true():
    event = _make_event(event_id="client-uuid-abc")

    with (
        patch("src.pipeline.product_event.get_contact_by_email", return_value=None),
        patch("src.pipeline.product_event.get_account_by_email_domain", return_value=None),
        patch(
            "src.pipeline.product_event.upsert_contact",
            side_effect=lambda _c, contact: contact,
        ),
        patch(
            "src.pipeline.product_event.insert_signal",
            side_effect=lambda _c, signal: (signal, True),
        ),
        patch("src.pipeline.product_event.insert_audit_event") as mock_audit,
    ):
        result = normalize_product_event(event, _WS_ID, _WS_NAME, _API_KEY_ID, client=None)

    assert result.duplicate is True
    mock_audit.assert_not_called()


def test_synthesize_subject_includes_email_and_event():
    line = _synthesize_subject("priya@example.com", "feature_activated")
    assert "priya@example.com" in line
    assert '"feature_activated"' in line


def test_synthesize_subject_falls_back_to_anonymous():
    assert _synthesize_subject(None, "ping") == 'anonymous performed "ping"'


def test_synthesize_body_includes_props_and_workspace_name():
    body = _synthesize_body(
        "priya@example.com",
        "feature_activated",
        {"feature": "export", "plan": "pro"},
        "ABC Corp",
        datetime(2026, 4, 25, 14, 0, 0, tzinfo=UTC),
    )
    assert "priya@example.com" in body
    assert "feature_activated" in body
    assert "ABC Corp" in body
    assert "feature=export" in body
    assert "plan=pro" in body
