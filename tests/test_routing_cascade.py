"""
Routing cascade unit tests — no DB, no Supabase.

All 8 routing scenarios from spec §6.2 + ADR-003 are covered.
Domain objects are constructed inline; route() is called directly.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_DNS, uuid5

import pytest

from src.domain.account import Account, AccountStatus, Vertical
from src.domain.signal import RoutingMethod
from src.domain.workspace import Workspace
from src.pipeline.router import RoutingResult, route

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_WS_ID = uuid5(NAMESPACE_DNS, "quantas-labs")
_ORG_ID = uuid5(NAMESPACE_DNS, "quantas-labs-org")
_NOW = datetime.now(UTC)

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


def _account(
    slug: str, domain: str, vertical=Vertical.LIFE_SCIENCES, extra_domains=None
) -> Account:
    return Account(
        id=uuid5(NAMESPACE_DNS, f"{_WS_ID}:{slug}"),
        workspace_id=_WS_ID,
        slug=slug,
        name=slug.title(),
        primary_domain=domain,
        additional_domains=extra_domains or [],
        vertical=vertical,
        crm_record_id=None,
        status=AccountStatus.ACTIVE,
        last_narrative_generated_at=None,
        created_at=_NOW,
        updated_at=_NOW,
        deleted_at=None,
    )


FORMATION_BIO = _account("formation-bio", "formationbio.com")
JNJ = _account("jnj", "jnj.com", extra_domains=["janssen.com"])
NOVAGEN_BIO = _account("novagen-bio", "novagenb.io", vertical=Vertical.LIFE_SCIENCES)

ACCOUNTS = [FORMATION_BIO, JNJ, NOVAGEN_BIO]

FIXTURE_DIR = Path("fixtures/quantas-labs-shaped")

if not FIXTURE_DIR.exists():
    pytest.skip(
        "quantas-labs pilot data moved to .private/; not present in tracked tree",
        allow_module_level=True,
    )

# Explicit inbound address for test isolation (avoids env-var pollution from other test files)
INBOUND_ADDRESS = "quantas-labs@signal.example.com"


def _load(path: str) -> dict:
    return json.loads((FIXTURE_DIR / path).read_text())


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload, thread_accounts, expected_method, expected_account_id",
    [
        # 1 — Clean corporate sender → header_domain matches formation-bio
        pytest.param(
            {
                "from_email": "priya.sharma@formationbio.com",
                "to_emails": ["quantas-labs@signal.example.com"],
                "thread_id": None,
            },
            {},
            RoutingMethod.HEADER_DOMAIN,
            FORMATION_BIO.id,
            id="header_domain_corporate_sender",
        ),
        # 2 — Internal sender forwards customer email → forward_parse
        pytest.param(
            _load("routing-tests/forward_parse.json"),
            {},
            RoutingMethod.FORWARD_PARSE,
            FORMATION_BIO.id,
            id="forward_parse_internal_forward",
        ),
        # 3 — Personal Gmail + known thread → thread_inherit
        pytest.param(
            {
                "from_email": "user@gmail.com",
                "to_emails": ["quantas-labs@signal.example.com"],
                "thread_id": "thread-formation-bio-002",
            },
            {"thread-formation-bio-002": [FORMATION_BIO.id]},
            RoutingMethod.THREAD_INHERIT,
            FORMATION_BIO.id,
            id="thread_inherit_gmail_known_thread",
        ),
        # 4 — New pharma domain → auto_discovery
        pytest.param(
            _load("routing-tests/auto_discovery.json"),
            {},
            RoutingMethod.AUTO_DISCOVERY,
            None,
            id="auto_discovery_new_pharma_domain",
        ),
        # 5 — Personal Gmail, no thread → unmatched
        pytest.param(
            _load("routing-tests/unmatched_gmail_1.json"),
            {},
            RoutingMethod.UNMATCHED,
            None,
            id="unmatched_personal_gmail",
        ),
        # 6 — Plus-addressed with known slug → plus_addressing
        pytest.param(
            _load("routing-tests/plus_addressed.json"),
            {},
            RoutingMethod.PLUS_ADDRESSING,
            FORMATION_BIO.id,
            id="plus_addressing_known_slug",
        ),
        # 7 — Plus-addressed unknown slug → falls through to header_domain
        pytest.param(
            _load("routing-tests/plus_addressed_unknown.json"),
            {},
            RoutingMethod.HEADER_DOMAIN,
            NOVAGEN_BIO.id,
            id="plus_addressing_unknown_slug_falls_to_header_domain",
        ),
        # 8 — Thread split-brain (two accounts in thread) → thread_inherit_split
        pytest.param(
            _load("routing-tests/thread_split.json"),
            {"thread-cross-001": [JNJ.id, FORMATION_BIO.id]},  # JnJ most recent
            RoutingMethod.THREAD_INHERIT_SPLIT,
            JNJ.id,
            id="thread_inherit_split_brain",
        ),
    ],
)
def test_routing_cascade(payload, thread_accounts, expected_method, expected_account_id):
    result = route(payload, WORKSPACE, ACCOUNTS, thread_accounts, inbound_address=INBOUND_ADDRESS)
    assert isinstance(result, RoutingResult)
    assert result.routing_method == expected_method
    assert result.account_id == expected_account_id


def test_split_brain_populates_routing_warning():
    payload = _load("routing-tests/thread_split.json")
    thread_accounts = {"thread-cross-001": [JNJ.id, FORMATION_BIO.id]}
    result = route(payload, WORKSPACE, ACCOUNTS, thread_accounts, inbound_address=INBOUND_ADDRESS)
    assert result.routing_method == RoutingMethod.THREAD_INHERIT_SPLIT
    assert result.routing_warning is not None
    assert "thread split" in result.routing_warning.lower()


def test_auto_discovery_creates_candidate():
    payload = _load("routing-tests/auto_discovery.json")
    result = route(payload, WORKSPACE, ACCOUNTS, {}, inbound_address=INBOUND_ADDRESS)
    assert result.routing_method == RoutingMethod.AUTO_DISCOVERY
    assert result.new_candidate is not None
    assert result.new_candidate.status == AccountStatus.CANDIDATE
    assert result.new_candidate.primary_domain == "recursionpharma.com"


def test_plus_addressing_unknown_slug_falls_through():
    """Unknown slug in plus-address should fall through, NOT error."""
    payload = {
        "from_email": "user@formationbio.com",
        "to_emails": ["quantas-labs+nonexistent@signal.example.com"],
        "thread_id": None,
    }
    result = route(payload, WORKSPACE, ACCOUNTS, {}, inbound_address=INBOUND_ADDRESS)
    # Falls through to header_domain because formationbio.com matches formation-bio
    assert result.routing_method == RoutingMethod.HEADER_DOMAIN
    assert result.account_id == FORMATION_BIO.id


def test_subdomain_match():
    """Subdomain of a known account domain should match via header_domain."""
    payload = {
        "from_email": "researcher@labs.formationbio.com",
        "to_emails": ["quantas-labs@signal.example.com"],
        "thread_id": None,
    }
    result = route(payload, WORKSPACE, ACCOUNTS, {}, inbound_address=INBOUND_ADDRESS)
    assert result.routing_method == RoutingMethod.HEADER_DOMAIN
    assert result.account_id == FORMATION_BIO.id


def test_internal_sender_no_forward_is_unmatched():
    """Internal sender sending only to the workspace inbound address falls through to unmatched."""
    payload = {
        "from_email": "engineer@quantaslabs.com",
        "to_emails": ["quantas-labs@signal.example.com"],
        "body": "Just a regular internal note, no forwarded content.",
        "thread_id": None,
    }
    result = route(payload, WORKSPACE, ACCOUNTS, {}, inbound_address=INBOUND_ADDRESS)
    assert result.routing_method == RoutingMethod.UNMATCHED
