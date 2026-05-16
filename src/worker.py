import argparse
import asyncio
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_log_level = getattr(logging, os.environ.get("LOG_LEVEL", "WARNING").upper(), logging.WARNING)
logging.basicConfig(level=_log_level, format="%(levelname)s %(name)s %(message)s")


def _fixtures_root() -> Path:
    root = os.environ.get("FIXTURES_ROOT")
    if root:
        return Path(root)
    return Path(__file__).parent.parent / "fixtures"


def _ingest_fixtures(scenario: str) -> None:
    from src.signals.fixture_source import JsonFixtureSource

    scenario_path = _fixtures_root() / scenario
    if not scenario_path.exists():
        raise SystemExit(f"Scenario not found: {scenario_path}")

    workspace_file = scenario_path / "workspace.json"
    if not workspace_file.exists():
        raise SystemExit(f"workspace.json not found in {scenario_path}")

    workspace_data = json.loads(workspace_file.read_text())
    workspace_id = uuid.uuid5(uuid.NAMESPACE_DNS, workspace_data["slug"])

    source = JsonFixtureSource(scenario_path)
    since = datetime.min.replace(tzinfo=UTC)
    events = asyncio.run(source.fetch(workspace_id=workspace_id, since=since))

    header = f"{'external_id':<45} {'direction':<10} {'from_email':<40} {'occurred_at'}"
    print(header)
    print("-" * len(header))
    for event in events:
        payload = json.loads(event.raw_payload)
        print(
            f"{payload.get('external_id', 'N/A'):<45} "
            f"{payload.get('direction', 'N/A'):<10} "
            f"{payload.get('from_email', 'N/A'):<40} "
            f"{payload.get('occurred_at', 'N/A')}"
        )
    print(f"\nLoaded {len(events)} events from scenario '{scenario}'.")


def _shift_events_to_recent(
    events: list,
    shift_days: int,
    now: datetime,
    scenario_path: Path,
) -> list:
    """Shift event timestamps per-account so each account's latest signal lands ~shift_days ago.

    For each account, finds its maximum occurred_at, computes
    delta = (now - shift_days) - account_max, then adds that delta to every
    event for that account.  Within-account relative spacing is preserved.
    Each account's signals are independently anchored so all 12 accounts appear
    "currently active" regardless of the orchestrator's sequential time advance.

    Account membership is read from the materialised signals directory so no
    routing state is needed at this point.

    The shift is applied at ingest time only — it does NOT alter the materialised
    fixture JSON on disk.

    DEPRECATED — pre-simulator pragmatic fix. The trajectory simulator
    (ADR-021, ``src/simulator/executor.py``) solves time-anchoring at the
    correct layer via the ``now_anchor`` parameter to ``yield_events``,
    which threads through without on-disk coupling to the materialised
    signals directory. New trajectory-style workflows should use the
    simulator. This helper remains for the ``process-fixtures`` one-shot
    CLI path until that command itself is reconsidered.
    """
    import dataclasses
    import json as _json
    from datetime import timedelta

    from src.domain.raw_inbound_event import RawInboundEvent

    target = now - timedelta(days=shift_days)

    # Build account→event-index mapping from the signals directory.
    # The fixture source returns events in the same order as the on-disk files,
    # and we can identify account by the signals/<slug>/ subdirectory.
    signals_dir = scenario_path / "signals"
    # Build a map from external_id → account_slug using on-disk files
    ext_id_to_slug: dict[str, str] = {}
    if signals_dir.exists():
        for acct_dir in signals_dir.iterdir():
            if not acct_dir.is_dir():
                continue
            slug = acct_dir.name
            for sig_file in sorted(acct_dir.glob("*.json")):
                try:
                    d = _json.loads(sig_file.read_text())
                    eid = d.get("external_id") or d.get("event_id")
                    if eid:
                        ext_id_to_slug[eid] = slug
                except Exception:
                    pass

    def _get_id(event: object) -> str | None:
        if isinstance(event, RawInboundEvent):
            d = _json.loads(event.raw_payload)
            return d.get("external_id")
        elif isinstance(event, dict):
            return event.get("event_id") or event.get("external_id")
        return None

    def _get_ts(event: object) -> datetime | None:
        if isinstance(event, RawInboundEvent):
            d = _json.loads(event.raw_payload)
            ts_str = d.get("occurred_at")
        elif isinstance(event, dict):
            ts_str = event.get("occurred_at")
        else:
            return None
        if ts_str:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return None

    # Compute per-account max timestamp
    slug_max: dict[str, datetime] = {}
    for event in events:
        eid = _get_id(event)
        slug = ext_id_to_slug.get(eid, "") if eid else ""
        ts = _get_ts(event)
        if slug and ts:
            if slug not in slug_max or ts > slug_max[slug]:
                slug_max[slug] = ts

    if not slug_max:
        return events

    # Compute per-account delta
    slug_delta = {slug: target - max_ts for slug, max_ts in slug_max.items()}

    shifted: list = []
    for event in events:
        eid = _get_id(event)
        slug = ext_id_to_slug.get(eid, "") if eid else ""
        delta = slug_delta.get(slug)

        if delta is None or abs(delta.total_seconds()) < 60:
            shifted.append(event)
            continue

        if isinstance(event, RawInboundEvent):
            payload = _json.loads(event.raw_payload)
            ts_str = payload.get("occurred_at")
            if ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                payload["occurred_at"] = (ts + delta).strftime("%Y-%m-%dT%H:%M:%SZ")
            shifted.append(
                dataclasses.replace(
                    event,
                    raw_payload=_json.dumps(payload),
                    received_at=event.received_at + delta,
                )
            )
        elif isinstance(event, dict):
            event = dict(event)
            ts_str = event.get("occurred_at")
            if ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                event["occurred_at"] = (ts + delta).strftime("%Y-%m-%dT%H:%M:%SZ")
            shifted.append(event)
        else:
            shifted.append(event)

    return shifted


def _process_fixtures(scenario: str, shift_to_recent: int | None = None) -> None:
    from src.config.loader import load_config
    from src.db.accounts import get_accounts_for_workspace, upsert_account
    from src.db.client import get_client
    from src.db.dimension_configs import seed_dimension_configs
    from src.db.organizations import upsert_organization
    from src.db.raw_inbound_events import insert_raw_event
    from src.db.workspaces import upsert_workspace
    from src.domain.account import Account, AccountStatus
    from src.domain.organization import Organization
    from src.domain.workspace import Workspace
    from src.pipeline.product_event import ProductEvent, normalize_product_event
    from src.pipeline.run import UNMATCHED_ACCOUNT_SLUG, process_event
    from src.signals.fixture_source import JsonFixtureSource
    from src.synthetic.orchestrator import _process_structured_signal_dict

    scenario_path = _fixtures_root() / scenario
    if not scenario_path.exists():
        raise SystemExit(f"Scenario not found: {scenario_path}")

    client = get_client()
    now = datetime.now(UTC)

    # --- Seed organization ---
    org_data = json.loads((scenario_path / "organization.json").read_text())
    org_id = uuid.uuid5(uuid.NAMESPACE_DNS, org_data["slug"])
    org = Organization(
        id=org_id,
        slug=org_data["slug"],
        name=org_data["name"],
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
    org = upsert_organization(client, org)
    print(f"Organization: {org.name} ({org.id})")

    # --- Seed workspace ---
    ws_data = json.loads((scenario_path / "workspace.json").read_text())
    ws_id = uuid.uuid5(uuid.NAMESPACE_DNS, ws_data["slug"])
    workspace = Workspace(
        id=ws_id,
        organization_id=org.id,
        slug=ws_data["slug"],
        name=ws_data["name"],
        internal_domains=tuple(ws_data.get("internal_domains", [])),
        crm_url_template=None,
        crm_portal_id=None,
        outbound_sender_email=None,
        outbound_sender_name=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
    workspace = upsert_workspace(client, workspace)
    print(f"Workspace:    {workspace.name} ({workspace.id})")

    # --- Seed dimension configs ---
    config = load_config(ws_data["slug"])
    seed_dimension_configs(client, workspace.id, config.health_scoring.dimensions)
    print(f"Dimension configs seeded ({len(config.health_scoring.dimensions)} dimensions).")

    # --- Seed named accounts ---
    accounts_dir = scenario_path / "accounts"
    for acc_file in sorted(accounts_dir.glob("*.json")):
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
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        upsert_account(client, account)

    # --- Seed candidate accounts ---
    candidates_dir = scenario_path / "candidates"
    if candidates_dir.exists():
        for cand_file in sorted(candidates_dir.glob("*.json")):
            c = json.loads(cand_file.read_text())
            candidate = Account(
                id=uuid.uuid5(uuid.NAMESPACE_DNS, f"{ws_id}:{c['slug']}"),
                workspace_id=ws_id,
                slug=c["slug"],
                name=c["name"],
                primary_domain=c.get("primary_domain"),
                additional_domains=c.get("additional_domains", []),
                vertical=c.get("vertical"),
                crm_record_id=None,
                status=AccountStatus.CANDIDATE,
                last_narrative_generated_at=None,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            upsert_account(client, candidate)

    # --- Ensure _unmatched pseudo-account ---
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
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
    upsert_account(client, unmatched)

    # --- Fetch events ---
    source = JsonFixtureSource(scenario_path)
    since = datetime.min.replace(tzinfo=UTC)
    raw_events = asyncio.run(source.fetch(workspace_id=ws_id, since=since))

    # Partition before shift so we can shift email and product independently.
    # Product event files (written by materialise.py) have "event_name" instead of "from_email".
    # Structured-signal files (pylon_ticket, plain_ticket, granola_note) are vendor-shaped
    # dicts: Granola has "id" starting with "not_"; Pylon has a "data" key; Plain has a
    # top-level "type" key — none of which match email or product shapes.
    email_events_raw = []
    product_payloads_raw: list[dict] = []
    structured_payloads_raw: list[dict] = []
    for event in raw_events:
        payload = json.loads(event.raw_payload)
        if "event_name" in payload and "from_email" not in payload:
            product_payloads_raw.append(payload)
        elif (
            payload.get("id", "").startswith("not_")  # Granola note
            or "data" in payload  # Pylon ticket envelope
            or (  # Plain ticket: top-level "type", no email/product keys
                "type" in payload
                and "from_email" not in payload
                and "event_name" not in payload
            )
        ):
            structured_payloads_raw.append(payload)
        else:
            email_events_raw.append(event)

    # --- Apply --shift-to-recent if requested ---
    # Structured signals (ticket/granola) are not shifted — they lack the external_id
    # → account-slug mapping that _shift_events_to_recent relies on, and the demo
    # health scoring is driven by email + product signal recency.
    if shift_to_recent is not None:
        print(
            "WARNING: --shift-to-recent is deprecated. The trajectory simulator "
            "(src/simulator/executor.py, ADR-021) uses now_anchor= to solve this "
            "at the correct layer without on-disk coupling. This flag remains for "
            "the process-fixtures one-shot path until that command is reconsidered."
        )
        all_mixed = _shift_events_to_recent(
            [*email_events_raw, *product_payloads_raw],
            shift_to_recent,
            now,
            scenario_path,
        )
        email_events = [e for e in all_mixed if not isinstance(e, dict)]
        product_payloads = [e for e in all_mixed if isinstance(e, dict)]
        print(
            f"[shift-to-recent={shift_to_recent}] Timestamps shifted so latest signal "
            f"lands ~{shift_to_recent} day(s) before now."
        )
    else:
        email_events = email_events_raw
        product_payloads = product_payloads_raw

    print(
        f"\nFetched {len(raw_events)} fixture events "
        f"({len(email_events)} email, {len(product_payloads)} product, "
        f"{len(structured_payloads_raw)} structured). "
        f"Inserting email events to raw_inbound_events..."
    )

    for event in email_events:
        insert_raw_event(client, event)

    # --- Load all workspace accounts for routing ---
    accounts = get_accounts_for_workspace(client, ws_id)
    print(f"Loaded {len(accounts)} accounts for routing.\n")

    # --- Process email events ---
    header = f"{'external_id':<45} {'routing_method':<24} {'confidence':<11} {'account_slug'}"
    print(header)
    print("-" * len(header))

    processed = 0
    for event in email_events:
        try:
            signal = process_event(event, workspace, accounts, client)
            account_slug = next((a.slug for a in accounts if a.id == signal.account_id), "?")
            print(
                f"{signal.external_id:<45} "
                f"{signal.routing_method:<24} "
                f"{signal.routing_confidence:<11.2f} "
                f"{account_slug}"
            )
            processed += 1
        except Exception as exc:
            payload = json.loads(event.raw_payload)
            print(f"ERROR {payload.get('external_id', '?')}: {exc}")

    # --- Process product events ---
    if product_payloads:
        print(f"\nProcessing {len(product_payloads)} product events...")
        null_api_key_id = uuid.UUID(int=0)
        product_processed = 0
        for payload in product_payloads:
            try:
                occurred_at_str = payload.get("occurred_at")
                occurred_at = (
                    datetime.fromisoformat(occurred_at_str) if occurred_at_str else None
                )
                product_event = ProductEvent(
                    contact_email=payload.get("contact_email"),
                    event_name=payload["event_name"],
                    event_properties=payload.get("event_properties", {}),
                    event_id=payload.get("event_id"),
                    occurred_at=occurred_at,
                )
                result = normalize_product_event(
                    product_event, ws_id, workspace.name, null_api_key_id, client
                )
                account_slug = next(
                    (a.slug for a in accounts if a.id == result.signal.account_id), "?"
                )
                print(
                    f"{result.signal.external_id:<45} "
                    f"{result.signal.routing_method:<24} "
                    f"{result.signal.routing_confidence:<11.2f} "
                    f"{account_slug}"
                )
                product_processed += 1
            except Exception as exc:
                print(f"ERROR product_event {payload.get('event_id', '?')}: {exc}")
        print(f"\nProcessed {product_processed}/{len(product_payloads)} product events.")

    # --- Process structured signals (pylon_ticket, plain_ticket, granola_note) ---
    if structured_payloads_raw:
        print(f"\nProcessing {len(structured_payloads_raw)} structured signals...")
        struct_processed = 0
        for payload in structured_payloads_raw:
            try:
                signal = _process_structured_signal_dict(payload, ws_id, workspace.name, client)
                if signal is None:
                    # Adapter returned None (unhandled event type — skip silently)
                    continue
                account_slug = next(
                    (a.slug for a in accounts if a.id == signal.account_id), "?"
                )
                print(
                    f"{signal.external_id:<45} "
                    f"{signal.routing_method:<24} "
                    f"{signal.routing_confidence:<11.2f} "
                    f"{account_slug}"
                )
                struct_processed += 1
            except Exception as exc:
                ext_id = payload.get("id", payload.get("external_id", "?"))
                print(f"ERROR structured_signal {ext_id}: {exc}")
        print(f"\nProcessed {struct_processed}/{len(structured_payloads_raw)} structured signals.")

    print(f"\nProcessed {processed}/{len(email_events)} email events.")


def _generate_narratives(
    workspace_slug: str,
    all_accounts: bool,
    account_slug: str | None,
    max_jobs: int,
) -> None:
    from src.observability.llm import setup_llm_observability

    # Initialize LLM observability before the Anthropic client is constructed so the
    # instrumentor wraps the client constructor.  No-ops when POSTHOG_API_KEY is unset
    # or POSTHOG_LLM_OBSERVABILITY_ENABLED=false — safe to call unconditionally.
    setup_llm_observability()

    import anthropic as anthropic_sdk

    from src.config.loader import load_config
    from src.db.accounts import get_account_by_slug, get_accounts_for_workspace
    from src.db.client import get_client
    from src.db.contacts import get_contacts_for_account
    from src.db.narratives import get_current_narrative
    from src.db.regen_jobs import get_pending_jobs, recover_stale_jobs, update_job_status
    from src.db.signals import get_signals_for_account
    from src.db.workspaces import get_workspace_by_slug
    from src.domain.account import AccountStatus
    from src.domain.regen_job import RegenJobStatus
    from src.pipeline.generator import generate_narrative
    from src.pipeline.run import UNMATCHED_ACCOUNT_SLUG

    client = get_client()
    client_ai = anthropic_sdk.Anthropic()

    workspace = get_workspace_by_slug(client, workspace_slug)
    if not workspace:
        raise SystemExit(f"Workspace not found: {workspace_slug}")

    config = load_config(workspace_slug)

    header = (
        f"{'account':<20} {'eng':<5} {'sent':<5} {'signals':<9} {'cached_tok':<12} {'generated_at'}"
    )
    print(header)
    print("-" * len(header))

    def _run_for_account(account) -> None:
        signals = get_signals_for_account(client, account.workspace_id, account.id)
        # Widen to the full account contact roster so the VALID CONTACTS whitelist
        # in the narrative prompt covers recipients/mentioned contacts, not just
        # signal authors. get_contacts_by_ids still covers authors for the signal
        # list; this broader fetch feeds _render_valid_contact_list. OQ1 resolution.
        contacts = {
            c.id: c
            for c in get_contacts_for_account(client, account.workspace_id, account.id)
        }
        prior = get_current_narrative(client, account.workspace_id, account.id)
        result = generate_narrative(
            account=account,
            signals=signals,
            contacts=contacts,
            prior_narrative=prior,
            config=config,
            workspace_slug=workspace_slug,
            client_db=client,
            client_anthropic=client_ai,
        )
        n = result.narrative
        sent_str = str(n.sentiment) if n.sentiment is not None else "—"
        print(
            f"{account.slug:<20} {n.engagement:<5} {sent_str:<5} {len(n.signals_considered):<9} "
            f"{result.cached_tokens:<12} {n.generated_at.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )

    if all_accounts or account_slug:
        if account_slug:
            acc = get_account_by_slug(client, workspace.id, account_slug)
            if not acc:
                raise SystemExit(f"Account not found: {account_slug}")
            targets = [acc]
        else:
            all_accs = get_accounts_for_workspace(client, workspace.id)
            targets = [
                a
                for a in all_accs
                if a.status == AccountStatus.ACTIVE and a.slug != UNMATCHED_ACCOUNT_SLUG
            ]

        generated = 0
        for account in targets:
            try:
                _run_for_account(account)
                generated += 1
            except Exception as exc:
                print(f"ERROR {account.slug}: {exc}")
        print(f"\nGenerated {generated}/{len(targets)} narratives.")

    else:
        # Job queue mode
        recover_stale_jobs(client)

        jobs = get_pending_jobs(client, workspace.id, limit=max_jobs)
        if not jobs:
            print("No pending narrative regen jobs.")
            return

        all_accs = {a.id: a for a in get_accounts_for_workspace(client, workspace.id)}
        done, failed = 0, 0
        for job in jobs:
            account = all_accs.get(job.account_id)
            if not account:
                update_job_status(client, job.id, RegenJobStatus.FAILED)
                failed += 1
                continue
            update_job_status(client, job.id, RegenJobStatus.RUNNING)
            try:
                _run_for_account(account)
                update_job_status(client, job.id, RegenJobStatus.DONE)
                done += 1
            except Exception as exc:
                print(f"ERROR {account.slug}: {exc}")
                update_job_status(client, job.id, RegenJobStatus.FAILED)
                failed += 1
        print(f"\nProcessed {done} jobs, {failed} failed.")




def _synthesise_fixtures(
    scenario_path: str,
    out_dir: str | None,
    seed_override: int | None,
    dry_run: bool,
) -> None:
    from src.synthetic.materialise import write_scenario_to_disk
    from src.synthetic.orchestrator import load_scenario

    path = Path(scenario_path)
    scenario = load_scenario(path)

    if out_dir:
        effective_out = Path(out_dir)
    else:
        effective_out = _fixtures_root() / "synthetic" / scenario.name

    manifest = write_scenario_to_disk(
        scenario,
        path,
        effective_out,
        seed_override=seed_override,
        dry_run=dry_run,
    )

    if not dry_run:
        print(f"Materialised {manifest['signal_count']} signals to {effective_out}")
        print(f"Scenario: {manifest['scenario_name']}  seed={manifest['scenario_seed']}")
        print(f"Accounts: {manifest['account_slugs']}")
        print(f"Input hash: {manifest['scenario_input_hash'][:16]}...")


def _serve(port: int, host: str = "0.0.0.0") -> None:
    import uvicorn

    import src.analytics as analytics
    from src.observability.llm import setup_llm_observability
    from src.server.app import create_app

    posthog_enabled = os.environ.get("POSTHOG_ENABLED", "false").lower() == "true"
    if posthog_enabled:
        setup_llm_observability()

    app = create_app()
    try:
        uvicorn.run(app, host=host, port=port)
    finally:
        if posthog_enabled:
            analytics.flush()


def main(args: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Customer Success Platform worker")
    subparsers = parser.add_subparsers(dest="command")

    ingest_parser = subparsers.add_parser(
        "ingest-fixtures", help="Load fixture scenario and print summary (no DB)"
    )
    ingest_parser.add_argument(
        "--scenario", required=True, help="Fixture scenario name (e.g. elicit-shaped)"
    )

    process_parser = subparsers.add_parser(
        "process-fixtures", help="Run full pipeline against Supabase (requires .env)"
    )
    process_parser.add_argument(
        "--scenario", required=True, help="Fixture scenario name (e.g. elicit-shaped)"
    )
    process_parser.add_argument(
        "--shift-to-recent",
        type=int,
        default=None,
        metavar="N",
        help=(
            "[DEPRECATED] Shift all signal timestamps at ingest time so the latest signal "
            "lands ~N days before now.  Per-account relative spacing is preserved. "
            "Does not alter fixture files on disk.  Prefer the trajectory simulator's "
            "now_anchor parameter (ADR-021, src/simulator/executor.py) for new workflows."
        ),
    )

    gen_parser = subparsers.add_parser(
        "generate-narratives",
        help="Generate account narratives via Claude API (requires .env + ANTHROPIC_API_KEY)",
    )
    gen_parser.add_argument("--workspace-slug", required=True)
    gen_parser.add_argument(
        "--all",
        dest="all_accounts",
        action="store_true",
        help="Generate for all active accounts, bypassing job queue",
    )
    gen_parser.add_argument("--account-slug", default=None, help="Generate for a single account")
    gen_parser.add_argument(
        "--max-jobs", type=int, default=20, help="Max job queue items to process"
    )

    synth_parser = subparsers.add_parser(
        "synthesise-fixtures",
        help="Generate synthetic fixture files from a scenario YAML",
    )
    synth_parser.add_argument("--scenario", required=True, help="Path to scenario YAML file")
    synth_parser.add_argument(
        "--out",
        default=None,
        help="Output directory (default: fixtures/synthetic/<scenario.name>/)",
    )
    synth_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override scenario seed for ad-hoc exploration",
    )
    synth_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without writing files",
    )

    serve_parser = subparsers.add_parser("serve", help="Start the HTTP webhook server")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument("--host", default="0.0.0.0")

    parsed = parser.parse_args(args)
    try:
        if parsed.command == "ingest-fixtures":
            _ingest_fixtures(parsed.scenario)
        elif parsed.command == "process-fixtures":
            _process_fixtures(parsed.scenario, shift_to_recent=parsed.shift_to_recent)
        elif parsed.command == "synthesise-fixtures":
            _synthesise_fixtures(
                scenario_path=parsed.scenario,
                out_dir=parsed.out,
                seed_override=parsed.seed,
                dry_run=parsed.dry_run,
            )
        elif parsed.command == "generate-narratives":
            _generate_narratives(
                workspace_slug=parsed.workspace_slug,
                all_accounts=parsed.all_accounts,
                account_slug=parsed.account_slug,
                max_jobs=parsed.max_jobs,
            )
        elif parsed.command == "serve":
            _serve(port=parsed.port, host=parsed.host)
        else:
            parser.print_help()
    finally:
        # Flush queued analytics events before CLI commands exit.
        # _serve() has its own flush in the uvicorn shutdown block; this is idempotent.
        try:
            import src.analytics as analytics

            analytics.flush()
        except Exception:
            pass


if __name__ == "__main__":
    main()
