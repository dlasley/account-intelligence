"""
Tests for _stage0_outbound_bcc routing — no DB, no Supabase.
"""

from datetime import UTC, datetime
from uuid import NAMESPACE_DNS, uuid5

from src.domain.account import Account, AccountStatus
from src.domain.signal import RoutingMethod
from src.domain.workspace import Workspace
from src.pipeline.router import PERSONAL_PROVIDER_DOMAINS, _stage0_outbound_bcc, route

_WS_ID = uuid5(NAMESPACE_DNS, "quantas-labs")
_ORG_ID = uuid5(NAMESPACE_DNS, "quantas-labs-org")
_NOW = datetime.now(UTC)
_INBOUND_ADDRESS = "quantas-labs@signal.example.com"

WORKSPACE = Workspace(
    id=_WS_ID,
    organization_id=_ORG_ID,
    slug="quantas-labs",
    name="Quantas Labs",
    internal_domains=("quantaslabs.com",),
    crm_url_template=None,
    crm_portal_id=None,
    outbound_sender_email=None,
    outbound_sender_name=None,
    created_at=_NOW,
    updated_at=_NOW,
    deleted_at=None,
)


def _account(slug: str, domain: str) -> Account:
    return Account(
        id=uuid5(NAMESPACE_DNS, f"{_WS_ID}:{slug}"),
        workspace_id=_WS_ID,
        slug=slug,
        name=slug.title(),
        primary_domain=domain,
        additional_domains=[],
        vertical=None,
        crm_record_id=None,
        status=AccountStatus.ACTIVE,
        last_narrative_generated_at=None,
        created_at=_NOW,
        updated_at=_NOW,
        deleted_at=None,
    )


FORMATION_BIO = _account("formation-bio", "formationbio.com")
ACCOUNTS = [FORMATION_BIO]


# ---------------------------------------------------------------------------
# Stage 0 not triggered for external sender
# ---------------------------------------------------------------------------


def test_stage0_returns_none_for_external_sender():
    payload = {
        "from_email": "priya@formationbio.com",
        "to_emails": ["csm@quantaslabs.com"],
    }
    result = _stage0_outbound_bcc(
        payload, WORKSPACE, ACCOUNTS, PERSONAL_PROVIDER_DOMAINS, _INBOUND_ADDRESS
    )
    assert result is None


# ---------------------------------------------------------------------------
# Internal sender to known account domain → outbound_bcc with account_id
# ---------------------------------------------------------------------------


def test_stage0_routes_internal_to_known_account():
    payload = {
        "from_email": "csm@quantaslabs.com",
        "to_emails": ["priya@formationbio.com", _INBOUND_ADDRESS],
    }
    result = _stage0_outbound_bcc(
        payload, WORKSPACE, ACCOUNTS, PERSONAL_PROVIDER_DOMAINS, _INBOUND_ADDRESS
    )
    assert result is not None
    assert result.routing_method == RoutingMethod.OUTBOUND_BCC
    assert result.account_id == FORMATION_BIO.id
    assert result.routing_confidence == 0.9


# ---------------------------------------------------------------------------
# Internal sender to personal email → falls through (None)
# ---------------------------------------------------------------------------


def test_stage0_returns_none_for_personal_email_recipient():
    payload = {
        "from_email": "csm@quantaslabs.com",
        "to_emails": ["user@gmail.com", _INBOUND_ADDRESS],
    }
    result = _stage0_outbound_bcc(
        payload, WORKSPACE, ACCOUNTS, PERSONAL_PROVIDER_DOMAINS, _INBOUND_ADDRESS
    )
    assert result is None


# ---------------------------------------------------------------------------
# Internal sender to only the workspace inbound address → falls through (None)
# ---------------------------------------------------------------------------


def test_stage0_returns_none_when_only_inbound_address():
    payload = {
        "from_email": "csm@quantaslabs.com",
        "to_emails": [_INBOUND_ADDRESS],
    }
    result = _stage0_outbound_bcc(
        payload, WORKSPACE, ACCOUNTS, PERSONAL_PROVIDER_DOMAINS, _INBOUND_ADDRESS
    )
    assert result is None


# ---------------------------------------------------------------------------
# Internal sender to unknown external corporate domain → auto-discover candidate
# ---------------------------------------------------------------------------


def test_stage0_creates_candidate_for_unknown_domain():
    payload = {
        "from_email": "csm@quantaslabs.com",
        "to_emails": ["cto@newcorp.io", _INBOUND_ADDRESS],
    }
    result = _stage0_outbound_bcc(
        payload, WORKSPACE, ACCOUNTS, PERSONAL_PROVIDER_DOMAINS, _INBOUND_ADDRESS
    )
    assert result is not None
    assert result.routing_method == RoutingMethod.OUTBOUND_BCC
    assert result.routing_confidence == 0.3
    assert result.new_candidate is not None
    assert result.new_candidate.primary_domain == "newcorp.io"
    assert result.new_candidate.status == AccountStatus.CANDIDATE
    assert result.account_id is None


# ---------------------------------------------------------------------------
# Full route() call with internal sender to known account
# ---------------------------------------------------------------------------


def test_route_internal_sender_routes_as_outbound_bcc():
    payload = {
        "from_email": "csm@quantaslabs.com",
        "to_emails": ["priya@formationbio.com", _INBOUND_ADDRESS],
        "thread_id": None,
    }
    result = route(payload, WORKSPACE, ACCOUNTS, {}, inbound_address=_INBOUND_ADDRESS)
    assert result.routing_method == RoutingMethod.OUTBOUND_BCC
    assert result.account_id == FORMATION_BIO.id


# ---------------------------------------------------------------------------
# Normalizer outbound direction test — author_contact is None for internal sender
# ---------------------------------------------------------------------------


def test_normalize_outbound_signal_has_no_author_contact():
    import json
    from datetime import UTC, datetime
    from unittest.mock import patch
    from uuid import uuid4

    from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
    from src.domain.signal import Direction, SourceType
    from src.pipeline.normalizer import normalize

    ws_id = uuid5(NAMESPACE_DNS, "quantas-labs")
    payload = {
        "external_id": "outbound-test-001",
        "source_type": "outbound_email",
        "direction": "inbound",  # shared_inbox sets inbound; normalizer should override
        "channel": "email",
        "occurred_at": "2026-04-24T10:00:00Z",
        "subject": "Following up",
        "body": "Hi, just checking in.",
        "from_email": "csm@quantaslabs.com",
        "from_name": "CSM",
        "to_emails": ["priya@formationbio.com"],
    }
    event = RawInboundEvent(
        id=uuid4(),
        workspace_id=ws_id,
        received_at=datetime.now(UTC),
        source_type=SourceType.OUTBOUND_EMAIL,
        raw_payload=json.dumps(payload),
        parse_status=ParseStatus.PENDING,
        signal_id=None,
        error_detail=None,
        processed_at=None,
    )

    with (
        patch("src.pipeline.normalizer.get_account_by_email_domain", return_value=None),
        patch("src.pipeline.normalizer.upsert_contact", side_effect=lambda _c, contact: contact),
        patch(
            "src.pipeline.normalizer.insert_signal",
            side_effect=lambda _c, signal: (signal, False),
        ),
        patch("src.pipeline.normalizer.insert_audit_event"),
    ):
        result = normalize(event, ws_id, ["quantaslabs.com"], client=None)

    assert result.author_contact is None
    assert result.signal.author_contact_id is None
    assert result.signal.direction == Direction.OUTBOUND
