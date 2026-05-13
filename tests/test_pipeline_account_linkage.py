"""
ADR-013: Contact-account linkage on ingest.

Tests for:
- get_account_by_email_domain helper (unit)
- Inbound email path via normalizer.normalize()
- Product-event auto_discovery path via product_event.normalize_product_event()

DB calls are patched at the call site. No live Supabase connection required.
"""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from src.db.accounts import get_account_by_email_domain
from src.domain.contact import Contact
from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
from src.domain.signal import RoutingMethod, SourceType
from src.pipeline.normalizer import normalize
from src.pipeline.product_event import ProductEvent, normalize_product_event

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_WS_ID = uuid5(NAMESPACE_DNS, "linkage-test")
_ACCOUNT_ID = uuid4()
_ACCOUNT_ID_2 = uuid4()
_INTERNAL_DOMAINS = ["example.com"]
_WS_NAME = "Test Workspace"
_API_KEY_ID = uuid4()

_PAYLOAD_BASE = {
    "external_id": "linkage-test-001",
    "source_type": "json_fixture",
    "direction": "inbound",
    "channel": "email",
    "occurred_at": "2026-05-02T10:00:00Z",
    "subject": "Linkage test",
    "body": "Hello, this is a linkage test.",
    "from_email": "alice@formationbio.com",
    "from_name": "Alice",
    "to_emails": ["example@signal.example.com"],
    "thread_id": "thread-linkage-001",
    "in_reply_to": None,
}


def _make_raw_event(payload: dict | None = None) -> RawInboundEvent:
    p = payload or _PAYLOAD_BASE
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


def _contact_with_account(email: str, account_id: UUID | None = _ACCOUNT_ID) -> Contact:
    now = datetime.now(UTC)
    return Contact(
        id=uuid5(NAMESPACE_DNS, f"{_WS_ID}:{email.lower()}"),
        workspace_id=_WS_ID,
        account_id=account_id,
        email=email.lower(),
        display_name=None,
        is_internal=False,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


# ---------------------------------------------------------------------------
# Test 7 (helper unit test): get_account_by_email_domain
# ---------------------------------------------------------------------------


class TestGetAccountByEmailDomain:
    """Unit tests for the domain-lookup helper. Mocks the Supabase client chain."""

    def _make_client(self, rows: list[dict]) -> MagicMock:
        """Return a mock Supabase client whose table().select()... chain returns `rows`."""
        mock_resp = MagicMock()
        mock_resp.data = rows

        mock_client = MagicMock()
        # Every chained call (table, select, eq, is_, or_, execute) returns itself
        # until execute(), which returns mock_resp.
        chain = MagicMock()
        chain.execute.return_value = mock_resp
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.is_.return_value = chain
        chain.or_.return_value = chain
        mock_client.table.return_value = chain
        return mock_client

    def test_single_match_returns_account_id(self):
        account_id = uuid4()
        client = self._make_client([{"id": str(account_id)}])
        result = get_account_by_email_domain(client, _WS_ID, "alice@formationbio.com")
        assert result == account_id

    def test_zero_match_returns_none(self):
        client = self._make_client([])
        result = get_account_by_email_domain(client, _WS_ID, "alice@gmail.com")
        assert result is None

    def test_multi_match_returns_none(self):
        client = self._make_client([{"id": str(uuid4())}, {"id": str(uuid4())}])
        result = get_account_by_email_domain(client, _WS_ID, "alice@sharedomain.com")
        assert result is None

    def test_missing_at_sign_returns_none(self):
        client = self._make_client([])
        result = get_account_by_email_domain(client, _WS_ID, "not-an-email")
        assert result is None
        # Should not hit the DB for a malformed input

    def test_malformed_domain_short_circuits_before_query(self):
        """Domains containing PostgREST filter metacharacters (commas, braces, dots-as-
        operators) are rejected before reaching the or_() interpolation. Without this
        guard, a crafted email could inject additional filter clauses and escape the
        workspace_id scope. See ADR-013 + 2026-05-02 code review.
        """
        client = self._make_client([])
        crafted = [
            "a@evil.com,workspace_id.eq.00000000-0000-0000-0000-000000000000",
            "a@evil.com,additional_domains.cs.{anything}",
            "a@{injection}.com",
            "a@evil com",  # space
            "a@-leading-dash.com",
            "a@trailing-dash-.com",
            "a@.com",
            "a@",
            "a@x",  # single-label, no dot
        ]
        for email in crafted:
            assert get_account_by_email_domain(client, _WS_ID, email) is None, email
        # Confirm none of these reached the DB layer.
        client.table.assert_not_called()
        client.table.assert_not_called()

    def test_get_account_filters_by_workspace_id(self):
        """Regression guard: get_account_by_email_domain must apply workspace_id scope.

        The self-referential chain mock accepts any call sequence, so this test
        asserts on call_args_list to confirm .eq("workspace_id", ...) was invoked.
        Without this guard, dropping the workspace_id filter would not cause any
        other test to fail.
        """
        ws_id = uuid5(NAMESPACE_DNS, "linkage-ws-filter-test")
        account_id = uuid4()
        client = self._make_client([{"id": str(account_id)}])

        get_account_by_email_domain(client, ws_id, "alice@example.com")

        # Collect all positional arg tuples from every .eq() call on the chain.
        chain = client.table.return_value
        eq_call_args = [call[0] for call in chain.eq.call_args_list]
        eq_columns = [args[0] for args in eq_call_args if args]
        assert "workspace_id" in eq_columns, (
            f"get_account_by_email_domain did not call .eq('workspace_id', ...). "
            f"Columns filtered: {eq_columns!r}"
        )
        ws_id_filtered = [
            args[1] for args in eq_call_args if args and args[0] == "workspace_id"
        ]
        assert str(ws_id) in ws_id_filtered, (
            f"workspace_id filter value mismatch. Got: {ws_id_filtered!r}"
        )

    def test_get_account_filters_deleted_at_is_null(self):
        """Regression guard: get_account_by_email_domain must exclude soft-deleted accounts."""
        ws_id = uuid5(NAMESPACE_DNS, "linkage-ws-deleted-test")
        client = self._make_client([])

        get_account_by_email_domain(client, ws_id, "alice@example.com")

        chain = client.table.return_value
        is_call_args = [call[0] for call in chain.is_.call_args_list]
        assert any(
            args and args[0] == "deleted_at" and args[1] == "null"
            for args in is_call_args
        ), (
            f"get_account_by_email_domain did not call .is_('deleted_at', 'null'). "
            f"is_() calls: {is_call_args!r}"
        )


# ---------------------------------------------------------------------------
# Test 1: Inbound email — domain matches primary_domain → account_id set
# ---------------------------------------------------------------------------


def test_inbound_domain_match_contact_gets_account_id():
    """Signal from a domain-matched email → contact row has non-NULL account_id."""
    event = _make_raw_event()

    captured = {}

    def fake_upsert(client, contact):
        captured["contact"] = contact
        return contact

    with (
        patch(
            "src.pipeline.normalizer.get_account_by_email_domain",
            return_value=_ACCOUNT_ID,
        ),
        patch("src.pipeline.normalizer.upsert_contact", side_effect=fake_upsert),
        patch(
            "src.pipeline.normalizer.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.normalizer.insert_audit_event"),
    ):
        result = normalize(event, _WS_ID, _INTERNAL_DOMAINS, client=None)

    assert result.author_contact is not None
    assert result.author_contact.account_id == _ACCOUNT_ID
    assert captured["contact"].account_id == _ACCOUNT_ID


# ---------------------------------------------------------------------------
# Test 2: Inbound email — domain matches additional_domains → account_id set
# (The helper returns the same UUID regardless of which column matched;
#  we confirm that a non-None return from the helper flows through correctly.)
# ---------------------------------------------------------------------------


def test_inbound_additional_domain_match_contact_gets_account_id():
    """Domain match via additional_domains also sets account_id on the contact."""
    alt_account_id = uuid4()
    event = _make_raw_event(dict(_PAYLOAD_BASE, from_email="bob@subsidiaryco.com"))

    captured = {}

    def fake_upsert(client, contact):
        captured["contact"] = contact
        return contact

    with (
        patch(
            "src.pipeline.normalizer.get_account_by_email_domain",
            return_value=alt_account_id,
        ),
        patch("src.pipeline.normalizer.upsert_contact", side_effect=fake_upsert),
        patch(
            "src.pipeline.normalizer.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.normalizer.insert_audit_event"),
    ):
        result = normalize(event, _WS_ID, _INTERNAL_DOMAINS, client=None)

    assert result.author_contact.account_id == alt_account_id


# ---------------------------------------------------------------------------
# Test: Inbound email — zero match → account_id IS None
# ---------------------------------------------------------------------------


def test_inbound_no_domain_match_contact_account_id_is_none():
    """Zero domain match (e.g. gmail.com) → contact's account_id is None."""
    event = _make_raw_event(dict(_PAYLOAD_BASE, from_email="bob@gmail.com"))

    captured = {}

    def fake_upsert(client, contact):
        captured["contact"] = contact
        return contact

    with (
        patch(
            "src.pipeline.normalizer.get_account_by_email_domain",
            return_value=None,
        ),
        patch("src.pipeline.normalizer.upsert_contact", side_effect=fake_upsert),
        patch(
            "src.pipeline.normalizer.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.normalizer.insert_audit_event"),
    ):
        result = normalize(event, _WS_ID, _INTERNAL_DOMAINS, client=None)

    assert result.author_contact.account_id is None
    assert captured["contact"].account_id is None


# ---------------------------------------------------------------------------
# Test: Inbound email — multi-match → account_id IS None
# ---------------------------------------------------------------------------


def test_inbound_multi_domain_match_contact_account_id_is_none():
    """Multi-match (2+ accounts share the domain) → contact's account_id is None."""
    event = _make_raw_event(dict(_PAYLOAD_BASE, from_email="alice@sharedomain.com"))

    captured = {}

    def fake_upsert(client, contact):
        captured["contact"] = contact
        return contact

    with (
        patch(
            "src.pipeline.normalizer.get_account_by_email_domain",
            return_value=None,  # helper returns None on multi-match
        ),
        patch("src.pipeline.normalizer.upsert_contact", side_effect=fake_upsert),
        patch(
            "src.pipeline.normalizer.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.normalizer.insert_audit_event"),
    ):
        result = normalize(event, _WS_ID, _INTERNAL_DOMAINS, client=None)

    assert result.author_contact.account_id is None


# ---------------------------------------------------------------------------
# Test 3: Inbound email — existing contact with non-NULL account_id is not clobbered
# ---------------------------------------------------------------------------


def test_inbound_existing_account_id_not_clobbered():
    """Existing contact with non-NULL account_id is NOT overwritten when a new signal arrives.

    Simulates the RPC path: upsert_contact calls upsert_contact_safe which uses COALESCE.
    We verify that when get_account_by_email_domain returns None (no new match),
    the contact passed to upsert still has account_id=None (the Python object is None),
    but the DB-layer RPC would preserve the existing value via COALESCE.

    More concretely: we mock upsert_contact to return a contact WITH account_id set
    (simulating the DB preserving the existing value), and assert the returned contact
    from normalize() has the preserved account_id.
    """
    event = _make_raw_event(dict(_PAYLOAD_BASE, from_email="alice@formationbio.com"))

    # Simulate: DB preserves existing account_id via COALESCE even when caller passes NULL
    existing_contact = _contact_with_account("alice@formationbio.com", _ACCOUNT_ID)

    with (
        patch(
            "src.pipeline.normalizer.get_account_by_email_domain",
            return_value=None,  # domain lookup finds nothing new
        ),
        patch(
            "src.pipeline.normalizer.upsert_contact",
            return_value=existing_contact,  # DB returns the row with preserved account_id
        ) as mock_upsert,
        patch(
            "src.pipeline.normalizer.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.normalizer.insert_audit_event"),
    ):
        result = normalize(event, _WS_ID, _INTERNAL_DOMAINS, client=None)

    # The contact returned from normalize() has the DB-preserved account_id
    assert result.author_contact.account_id == _ACCOUNT_ID
    # upsert_contact is called for author + recipient(s)
    assert mock_upsert.call_count >= 1


# ---------------------------------------------------------------------------
# Test 4: Product event auto_discovery — domain match → account_id set
# ---------------------------------------------------------------------------


def test_product_event_auto_discovery_domain_match_sets_account_id():
    """Auto-discovery product event from domain-matched email → contact has account_id."""
    event = ProductEvent(
        contact_email="newuser@formationbio.com",
        event_name="feature_activated",
        event_properties={},
    )

    captured = {}

    def fake_upsert(client, contact):
        captured["contact"] = contact
        return contact

    with (
        patch("src.pipeline.product_event.get_contact_by_email", return_value=None),
        patch(
            "src.pipeline.product_event.get_account_by_email_domain",
            return_value=_ACCOUNT_ID,
        ),
        patch("src.pipeline.product_event.upsert_contact", side_effect=fake_upsert),
        patch(
            "src.pipeline.product_event.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.product_event.insert_audit_event"),
    ):
        result = normalize_product_event(event, _WS_ID, _WS_NAME, _API_KEY_ID, client=None)

    assert result.signal.routing_method == RoutingMethod.AUTO_DISCOVERY
    assert captured["contact"].account_id == _ACCOUNT_ID


# ---------------------------------------------------------------------------
# Test: Product event auto_discovery — zero match → account_id IS None
# ---------------------------------------------------------------------------


def test_product_event_auto_discovery_no_match_account_id_is_none():
    """Auto-discovery with no domain match → contact account_id is None."""
    event = ProductEvent(
        contact_email="newuser@gmail.com",
        event_name="signup",
        event_properties={},
    )

    captured = {}

    def fake_upsert(client, contact):
        captured["contact"] = contact
        return contact

    with (
        patch("src.pipeline.product_event.get_contact_by_email", return_value=None),
        patch(
            "src.pipeline.product_event.get_account_by_email_domain",
            return_value=None,
        ),
        patch("src.pipeline.product_event.upsert_contact", side_effect=fake_upsert),
        patch(
            "src.pipeline.product_event.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.product_event.insert_audit_event"),
    ):
        result = normalize_product_event(event, _WS_ID, _WS_NAME, _API_KEY_ID, client=None)

    assert result.signal.routing_method == RoutingMethod.AUTO_DISCOVERY
    assert captured["contact"].account_id is None


# ---------------------------------------------------------------------------
# Test 5: Product event auto_discovery — existing contact with non-NULL account_id preserved
# ---------------------------------------------------------------------------


def test_product_event_existing_account_id_preserved_on_reingest():
    """Existing contact with non-NULL account_id is preserved on re-ingest.

    When get_contact_by_email returns an existing contact (api_key_identity path),
    account_id is inherited from the existing contact. The get_account_by_email_domain
    helper is NOT called for this branch.
    """
    event = ProductEvent(
        contact_email="priya@formationbio.com",
        event_name="feature_activated",
        event_properties={},
    )
    existing = _contact_with_account("priya@formationbio.com", _ACCOUNT_ID)

    with (
        patch("src.pipeline.product_event.get_contact_by_email", return_value=existing),
        patch("src.pipeline.product_event.get_account_by_email_domain") as mock_domain_lookup,
        patch("src.pipeline.product_event.upsert_contact") as mock_upsert,
        patch(
            "src.pipeline.product_event.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.product_event.insert_audit_event"),
    ):
        result = normalize_product_event(event, _WS_ID, _WS_NAME, _API_KEY_ID, client=None)

    # api_key_identity path: account_id inherited from existing contact
    assert result.signal.routing_method == RoutingMethod.API_KEY_IDENTITY
    assert result.signal.account_id == _ACCOUNT_ID
    # Domain lookup and upsert are NOT called for the api_key_identity branch
    mock_domain_lookup.assert_not_called()
    mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# upsert_contact RPC response-shape regression
# ---------------------------------------------------------------------------


class TestUpsertContactRpcResponseShape:
    """Regression for production failure 2026-05-02: supabase-py .rpc() with a
    RETURNS <row_type> function returns the row as a dict directly, NOT wrapped
    in [dict] like .table().upsert() does. The original ADR-013 implementation
    indexed result.data[0] which raised KeyError: 0 in production.
    """

    def _row(self) -> dict:
        return {
            "id": str(uuid4()),
            "workspace_id": str(_WS_ID),
            "account_id": None,
            "email": "alice@example.com",
            "display_name": None,
            "is_internal": False,
            "created_at": "2026-05-02T00:00:00+00:00",
            "updated_at": "2026-05-02T00:00:00+00:00",
            "deleted_at": None,
        }

    def _make_client(self, data) -> MagicMock:
        from unittest.mock import MagicMock as MM

        resp = MM()
        resp.data = data
        chain = MM()
        chain.execute.return_value = resp
        client = MM()
        client.rpc.return_value = chain
        return client

    def test_handles_dict_response_from_rpc(self):
        """Live supabase-py shape for RETURNS contacts: result.data is a dict."""
        from src.db.contacts import upsert_contact

        contact = Contact(
            id=uuid4(),
            workspace_id=_WS_ID,
            account_id=None,
            email="alice@example.com",
            display_name=None,
            is_internal=False,
            created_at=datetime(2026, 5, 2, tzinfo=UTC),
            updated_at=datetime(2026, 5, 2, tzinfo=UTC),
            deleted_at=None,
        )
        client = self._make_client(self._row())
        out = upsert_contact(client, contact)
        assert out.email == "alice@example.com"

    def test_handles_list_response_from_upsert(self):
        """Test-mock shape used elsewhere: result.data is [dict]. Both shapes work."""
        from src.db.contacts import upsert_contact

        contact = Contact(
            id=uuid4(),
            workspace_id=_WS_ID,
            account_id=None,
            email="alice@example.com",
            display_name=None,
            is_internal=False,
            created_at=datetime(2026, 5, 2, tzinfo=UTC),
            updated_at=datetime(2026, 5, 2, tzinfo=UTC),
            deleted_at=None,
        )
        client = self._make_client([self._row()])
        out = upsert_contact(client, contact)
        assert out.email == "alice@example.com"
