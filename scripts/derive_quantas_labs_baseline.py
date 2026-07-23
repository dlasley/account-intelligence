"""One-shot baseline derivation tool for Phase 2c (ADR-015 §Test Req 8).

Run once to capture routing-distribution + sender-domain set from the 63
hand-authored Quantas Labs fixtures.  Output is YAML-formatted expected_routing
ready to paste into fixtures/synthetic-scenarios/quantas-labs-baseline.yaml.

NOT a regular test or CI target — throw-away script, run once.

Usage:
    uv run python scripts/derive_quantas_labs_baseline.py
"""

import json
import sys
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make sure repo root is on sys.path when invoked from the project root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domain.account import Account, AccountStatus
from src.domain.contact import Contact
from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType
from src.domain.workspace import Workspace
from src.pipeline.run import UNMATCHED_ACCOUNT_SLUG, process_event

# quantas-labs pilot data moved to .private/; these fixtures are NOT present in the tracked
# tree (mirrors the skip reason in tests/synthetic/test_quantas_labs_equivalence.py). This
# one-shot tool only runs against the maintainer's local copy — a fresh public clone will
# raise FileNotFoundError here, which is expected.
_FIXTURES_ROOT = Path("fixtures/quantas-labs-shaped")
_ACCOUNTS_DIR = _FIXTURES_ROOT / "accounts"
_NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def _load_workspace() -> tuple[Workspace, uuid.UUID]:
    ws_data = json.loads((_FIXTURES_ROOT / "workspace.json").read_text())
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
    return workspace, ws_id


def _load_accounts(ws_id: uuid.UUID) -> list[Account]:
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
            crm_slug=None,
            status=AccountStatus(a.get("status", "active")),
            last_narrative_generated_at=None,
            created_at=_NOW,
            updated_at=_NOW,
            deleted_at=None,
        )
        accounts.append(account)

    accounts.append(
        Account(
            id=uuid.uuid5(uuid.NAMESPACE_DNS, f"{ws_id}:{UNMATCHED_ACCOUNT_SLUG}"),
            workspace_id=ws_id,
            slug=UNMATCHED_ACCOUNT_SLUG,
            name="Unmatched",
            primary_domain=None,
            additional_domains=[],
            vertical=None,
            crm_slug=None,
            status=AccountStatus.ACTIVE,
            last_narrative_generated_at=None,
            created_at=_NOW,
            updated_at=_NOW,
            deleted_at=None,
        )
    )
    return accounts


def _make_raw_event(ws_id: uuid.UUID, payload: dict) -> RawInboundEvent:
    return RawInboundEvent(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        received_at=_NOW,
        source_type=SourceType.JSON_FIXTURE,
        raw_payload=json.dumps(payload),
        parse_status=ParseStatus.PENDING,
        signal_id=None,
        error_detail=None,
        processed_at=None,
    )


def _iter_fixture_payloads() -> list[tuple[str, dict]]:
    """Yield (account_slug, payload_dict) for all 63 account-level fixtures in order."""
    account_slugs = ["cdc", "formation-bio", "harvard", "jnj", "shionogi"]
    results = []
    for slug in account_slugs:
        sig_dir = _FIXTURES_ROOT / "signals" / slug
        for f in sorted(sig_dir.glob("*.json")):
            payload = json.loads(f.read_text())
            results.append((slug, payload))
    return results


def main() -> None:
    workspace, ws_id = _load_workspace()
    accounts = _load_accounts(ws_id)
    client = MagicMock()

    # Per-account accumulators
    routing_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sender_domains: dict[str, set[str]] = defaultdict(set)
    thread_map: dict[str, list[uuid.UUID]] = defaultdict(list)
    # signal_id → Signal so update_signal_routing's stub can look up thread_id
    # post-routing (mirrors test_quantas_labs_equivalence.py — same code path so the
    # baseline counts use the same mock semantics the test asserts against).
    signals_by_id: dict[uuid.UUID, Signal] = {}

    id_to_slug = {a.id: a.slug for a in accounts if a.slug != UNMATCHED_ACCOUNT_SLUG}

    def _upsert_contact_stub(client_arg: object, contact: Contact) -> Contact:
        return contact

    def _insert_signal_stub(client_arg: object, signal: Signal) -> tuple[Signal, bool]:
        signal.id = uuid.uuid5(uuid.NAMESPACE_DNS, f"baseline:{signal.external_id}")
        signals_by_id[signal.id] = signal
        return signal, False

    def _update_signal_routing_stub(
        client_arg: object,
        signal_id: uuid.UUID,
        account_id: uuid.UUID | None,
        routing_method: RoutingMethod | None,
        routing_confidence: float | None,
        routing_warning: str | None,
    ) -> None:
        sig = signals_by_id.get(signal_id)
        if sig is not None and sig.thread_id and account_id is not None:
            if account_id not in thread_map[sig.thread_id]:
                thread_map[sig.thread_id].append(account_id)

    def _get_signals_by_thread_id_stub(
        client_arg: object, workspace_id_arg: object, thread_id: str
    ) -> list[Signal]:
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

    def _upsert_account_stub(client_arg: object, account: Account) -> Account:
        if not any(a.id == account.id for a in accounts):
            accounts.append(account)
            id_to_slug[account.id] = account.slug
        return account

    fixture_items = _iter_fixture_payloads()

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
        for account_slug, payload in fixture_items:
            raw_event = _make_raw_event(ws_id, payload)
            signal = process_event(raw_event, workspace, accounts, client)
            if signal.account_id and signal.account_id in id_to_slug:
                acc_slug = id_to_slug[signal.account_id]
                routing_counts[acc_slug][str(signal.routing_method)] += 1
            from_email = payload.get("from_email", "")
            if "@" in from_email:
                domain = from_email.split("@")[-1].lower()
                # Track under the account the signal was expected for (fixture slug)
                sender_domains[account_slug].add(domain)

    # Emit YAML block
    account_order = ["cdc", "formation-bio", "harvard", "jnj", "shionogi"]
    print("expected_routing:")
    for slug in account_order:
        counts = dict(routing_counts.get(slug, {}))
        total = sum(counts.values())
        domains = sorted(sender_domains.get(slug, set()))
        print(f"  - account_slug: {slug}")
        print(f"    signal_count: {total}")
        print("    routing_distribution:")
        for method, count in sorted(counts.items()):
            print(f"      {method}: {count}")
        print("    sender_domains:")
        for d in domains:
            print(f"      - {d}")


if __name__ == "__main__":
    main()
