"""TDD: failing tests for Phase 2c elicit-baseline equivalence.

Asserts that the synthetic scenario `elicit-baseline.yaml` reproduces the
routing decisions produced by the 63 hand-authored Elicit account fixtures.
Equivalence criterion (ADR-015 §Req 8):
  - signal count per account (exact)
  - routing-method distribution per account (exact)
  - sender-domain set per account (order-insensitive equality)

ImportError on `ExpectedSignalSpec` is the expected red state until the coder
adds the class to `src.synthetic.scenario`.
"""

import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from src.domain.account import Account, AccountStatus
from src.domain.contact import Contact
from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType
from src.domain.workspace import Workspace
from src.pipeline.run import UNMATCHED_ACCOUNT_SLUG
from src.synthetic.orchestrator import load_scenario, run_scenario

# This import is intentionally the red line: ExpectedSignalSpec does not exist yet.
from src.synthetic.scenario import ExpectedSignalSpec  # noqa: F401

_ACCOUNTS_DIR = Path("fixtures/elicit-shaped/accounts")
_SCENARIO_PATH = Path("fixtures/synthetic-scenarios/elicit-baseline.yaml")
_NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

if not _ACCOUNTS_DIR.exists() or not _SCENARIO_PATH.exists():
    pytest.skip(
        "elicit pilot data moved to .private/; not present in tracked tree",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Module-level helper: structured diff for routing-distribution failures
# ---------------------------------------------------------------------------


def _routing_diff(account_slug: str, expected_dist: dict, actual_dist: dict) -> str:
    lines = [f"Account: {account_slug}"]
    all_methods = sorted(set(expected_dist) | set(actual_dist))
    for m in all_methods:
        exp = expected_dist.get(m, 0)
        act = actual_dist.get(m, 0)
        marker = "  OK" if exp == act else f"  DIVERGE (expected {exp}, got {act})"
        lines.append(f"  {m}: {act}{marker}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared fixture: scenario + workspace + accounts + run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scenario_run():
    """Load the elicit-baseline scenario, construct workspace + accounts,
    execute run_scenario under mocked DB calls, and return:
        (scenario, signals, thread_map)

    If the YAML doesn't exist yet the test is skipped — the import of
    ExpectedSignalSpec above will have already failed (red state) before
    this fixture is reached, so the skip path is only hit after coder
    adds the class but before authoring the YAML.
    """
    if not _SCENARIO_PATH.exists():
        pytest.skip(f"Scenario file not yet authored: {_SCENARIO_PATH}")

    scenario = load_scenario(_SCENARIO_PATH)

    # Workspace mirrors process-fixtures logic (src/worker.py:93-109)
    ws_data = json.loads((Path("fixtures/elicit-shaped") / "workspace.json").read_text())
    ws_id = uuid.uuid5(uuid.NAMESPACE_DNS, ws_data["slug"])
    org_id = uuid.uuid5(uuid.NAMESPACE_DNS, ws_data["organization_slug"])
    workspace = Workspace(
        id=ws_id,
        organization_id=org_id,
        slug=ws_data["slug"],
        name=ws_data["name"],
        internal_domains=tuple(ws_data.get("internal_domains", [])),
        crm_url_template=None,
        crm_portal_id=None,
        outbound_sender_email=None,
        outbound_sender_name=None,
        created_at=_NOW,
        updated_at=_NOW,
        deleted_at=None,
    )

    # Accounts: read from fixtures/elicit-shaped/accounts/*.json (src/worker.py:117-136)
    accounts: list[Account] = []
    for acc_file in sorted(_ACCOUNTS_DIR.glob("*.json")):
        a = json.loads(acc_file.read_text())
        account = Account(
            id=uuid.uuid5(uuid.NAMESPACE_DNS, f"{ws_id}:{a['slug']}"),
            workspace_id=ws_id,
            slug=a["slug"],
            name=a["name"],
            primary_domain=a.get("primary_domain"),
            additional_domains=a.get("additional_domains", []),
            vertical=a.get("vertical"),
            crm_record_id=None,
            status=AccountStatus(a.get("status", "active")),
            last_narrative_generated_at=None,
            created_at=_NOW,
            updated_at=_NOW,
            deleted_at=None,
        )
        accounts.append(account)

    # _unmatched pseudo-account (src/worker.py:160-176)
    unmatched = Account(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, f"{ws_id}:{UNMATCHED_ACCOUNT_SLUG}"),
        workspace_id=ws_id,
        slug=UNMATCHED_ACCOUNT_SLUG,
        name="Unmatched",
        primary_domain=None,
        additional_domains=[],
        vertical=None,
        crm_record_id=None,
        status=AccountStatus.ACTIVE,
        last_narrative_generated_at=None,
        created_at=_NOW,
        updated_at=_NOW,
        deleted_at=None,
    )
    accounts.append(unmatched)

    client = MagicMock()

    # Thread-map accumulator: thread_id -> list of account_ids seen so far.
    # Populated after each signal is returned; read by the get_signals_by_thread_id mock.
    thread_map: dict[str, list[UUID]] = defaultdict(list)
    # signal_id → Signal so update_signal_routing's side effect can look up the
    # thread_id when routing fires (after insert_signal). Required because
    # update_signal_routing's args don't include thread_id.
    signals_by_id: dict[UUID, Signal] = {}

    def _make_contact_stub(contact: Contact) -> Contact:
        """Return a minimal Contact with the email from the incoming object."""
        return contact

    def _insert_signal_stub(client_arg, signal: Signal):
        """Assign a deterministic ID and register the signal so the routing-side
        update can find it. Routing fields (account_id, routing_method) are not
        yet populated here — they are set by update_signal_routing after the
        router decides."""
        signal.id = uuid.uuid5(uuid.NAMESPACE_DNS, f"elicit:{signal.external_id}")
        signals_by_id[signal.id] = signal
        return signal, False

    def _update_signal_routing_stub(
        client_arg,
        signal_id: UUID,
        account_id,  # UUID | None
        routing_method,  # RoutingMethod | None
        routing_confidence,  # float | None
        routing_warning,  # str | None
    ):
        """Fire after the router decides. Populates thread_map from the
        post-routing (signal_id, account_id) pair so subsequent signals in the
        same batch can THREAD_INHERIT correctly."""
        sig = signals_by_id.get(signal_id)
        if sig is not None and sig.thread_id and account_id is not None:
            if account_id not in thread_map[sig.thread_id]:
                thread_map[sig.thread_id].append(account_id)

    def _get_signals_by_thread_id_stub(client_arg, workspace_id_arg, thread_id: str):
        """Return synthetic Signal stubs sufficient for _stage4_thread_inherit."""
        account_ids = thread_map.get(thread_id, [])
        stubs = []
        for acc_id in account_ids:
            stub = Signal(
                id=uuid.uuid4(),
                workspace_id=ws_id,
                account_id=acc_id,
                source_type=SourceType.JSON_FIXTURE,
                external_id=f"stub-{thread_id}-{acc_id}",
                thread_id=thread_id,
                direction=Direction.INBOUND,
                channel=Channel.EMAIL,
                occurred_at=_NOW,
                created_at=_NOW,
                updated_at=_NOW,
                subject=None,
                body="stub",
                author_contact_id=None,
                recipient_contact_ids=[],
                routing_method=RoutingMethod.THREAD_INHERIT,
                routing_confidence=1.0,
                routing_warning=None,
                deleted_at=None,
            )
            stubs.append(stub)
        return stubs

    def _upsert_account_stub(client_arg, account: Account) -> Account:
        """Capture auto-discovered accounts into the running accounts list."""
        if not any(a.id == account.id for a in accounts):
            accounts.append(account)
        return account

    def _upsert_contact_stub(client_arg, contact: Contact) -> Contact:
        return _make_contact_stub(contact)

    with (
        patch("src.pipeline.normalizer.upsert_contact", side_effect=_upsert_contact_stub),
        patch("src.pipeline.normalizer.insert_signal", side_effect=_insert_signal_stub),
        patch(
            "src.pipeline.run.get_signals_by_thread_id",
            side_effect=_get_signals_by_thread_id_stub,
        ),
        patch("src.pipeline.run.upsert_account", side_effect=_upsert_account_stub),
        patch("src.pipeline.run.mark_processed"),
        patch("src.pipeline.run.mark_failed"),
        patch("src.pipeline.run.schedule_regen"),
        patch("src.pipeline.run.insert_audit_event"),
        patch("src.pipeline.normalizer.get_account_by_email_domain", return_value=None),
        patch("src.pipeline.normalizer.insert_audit_event"),
        patch("src.analytics.track"),
        patch(
            "src.pipeline.run.update_signal_routing",
            side_effect=_update_signal_routing_stub,
        ),
    ):
        signals = run_scenario(scenario, ws_id, workspace, accounts, client)

    # account_id → slug, derived from the same ws_id used for the run.
    # Computing it here (rather than in each test) avoids 3x redundant disk reads
    # and keeps ws_id consistent with the workspace the run actually used.
    id_to_slug: dict[UUID, str] = {a.id: a.slug for a in accounts}

    return scenario, signals, thread_map, id_to_slug


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestElicitSignalCounts:
    def test_elicit_signal_counts(self, scenario_run):
        """Per-account signal count must match expected_routing baseline (exact)."""
        scenario, signals, _thread_map, id_to_slug = scenario_run

        account_signal_counts: dict[str, int] = defaultdict(int)
        for sig in signals:
            if sig.account_id and sig.account_id in id_to_slug:
                account_signal_counts[id_to_slug[sig.account_id]] += 1

        for expected in scenario.expected_routing:
            actual_count = account_signal_counts.get(expected.account_slug, 0)
            assert actual_count == expected.signal_count, (
                f"Account {expected.account_slug}: expected {expected.signal_count} signals, "
                f"got {actual_count}"
            )


class TestElicitRoutingDistribution:
    def test_elicit_routing_distribution(self, scenario_run):
        """Per-account routing-method distribution must match expected_routing baseline."""
        scenario, signals, _thread_map, id_to_slug = scenario_run

        account_routing: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for sig in signals:
            if sig.account_id and sig.account_id in id_to_slug and sig.routing_method:
                slug = id_to_slug[sig.account_id]
                account_routing[slug][str(sig.routing_method)] += 1

        for expected in scenario.expected_routing:
            actual_dist = dict(account_routing.get(expected.account_slug, {}))
            expected_dist = expected.routing_distribution
            assert actual_dist == expected_dist, _routing_diff(
                expected.account_slug, expected_dist, actual_dist
            )


class TestElicitSenderDomains:
    def test_elicit_sender_domains(self, scenario_run):
        """Per-account unique sender email domains must match expected_routing baseline."""
        scenario, _signals, _thread_map, _id_to_slug = scenario_run

        # Re-run yield_events (pure, deterministic, cheap) and parse each event's
        # raw_payload for from_email domains. Cleaner than threading per-signal
        # capture state through the run_scenario fixture.
        from src.domain.raw_inbound_event import RawInboundEvent
        from src.synthetic.orchestrator import yield_events

        ws_id_full = uuid.uuid5(uuid.NAMESPACE_DNS, "elicit")
        account_sender_domains: dict[str, set[str]] = defaultdict(set)

        for slug, event in yield_events(scenario, ws_id_full):
            if not isinstance(event, RawInboundEvent):
                continue
            payload = json.loads(event.raw_payload)
            from_email = payload.get("from_email", "")
            if "@" in from_email:
                domain = from_email.split("@")[-1].lower()
                account_sender_domains[slug].add(domain)

        for expected in scenario.expected_routing:
            actual_domains = account_sender_domains.get(expected.account_slug, set())
            expected_domains = set(expected.sender_domains)
            assert actual_domains == expected_domains, (
                f"Account {expected.account_slug}: sender domain mismatch.\n"
                f"  Expected: {sorted(expected_domains)}\n"
                f"  Actual:   {sorted(actual_domains)}"
            )


class TestElicitCrossAccountThreadInherit:
    def test_elicit_cross_account_thread_inherit(self, scenario_run):
        """Signals with thread_id == 'thread-cross-001' must appear under both
        formation-bio and jnj after processing (thread-map accumulator picked up
        both accounts)."""
        _scenario, _signals, thread_map, _id_to_slug = scenario_run

        cross_thread_id = "thread-cross-001"
        ws_id = uuid.uuid5(uuid.NAMESPACE_DNS, "elicit")
        formation_bio_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"{ws_id}:formation-bio")
        jnj_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"{ws_id}:jnj")

        account_ids_in_thread = set(thread_map.get(cross_thread_id, []))

        assert formation_bio_id in account_ids_in_thread, (
            f"formation-bio not found in thread-cross-001 accumulator. "
            f"Got account_ids: {account_ids_in_thread}"
        )
        assert jnj_id in account_ids_in_thread, (
            f"jnj not found in thread-cross-001 accumulator. "
            f"Got account_ids: {account_ids_in_thread}"
        )
