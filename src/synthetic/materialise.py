"""Materialise synthetic scenarios to disk.

Writes a directory tree matching the elicit-shaped layout so JsonFixtureSource
can consume the output without modification:

    <out>/
        manifest.json
        workspace.json
        organization.json
        accounts/
            <account-slug>.json
        signals/
            <account-slug>/
                000.json
                001.json
                ...

See ADR-015 §D5c for the manifest.json schema.
Dependency constraint: no src.db imports. See ADR-015 §D6.
"""

import hashlib
import importlib.metadata
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from src.domain.raw_inbound_event import RawInboundEvent
from src.pipeline.product_event import ProductEvent
from src.synthetic.scenario import ScenarioSpec


def _generator_version() -> str:
    """Read package version from pyproject.toml metadata."""
    try:
        return importlib.metadata.version("account-intelligence")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _scenario_hash(scenario_path: Path) -> str:
    """SHA-256 of the raw scenario YAML bytes."""
    return hashlib.sha256(scenario_path.read_bytes()).hexdigest()


def _default_out_dir(scenario: ScenarioSpec, fixtures_root: Path) -> Path:
    return fixtures_root / "synthetic" / scenario.name


def write_scenario_to_disk(
    scenario: ScenarioSpec,
    scenario_path: Path,
    out_dir: Path,
    seed_override: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Materialise the scenario to `out_dir`.

    Returns the manifest dict (written to disk unless dry_run=True).

    If `seed_override` is provided, a fresh ScenarioSpec is derived with that seed
    so that the manifest records the actual seed used.
    """
    effective_seed = seed_override if seed_override is not None else scenario.seed

    # If seed was overridden, rebuild the scenario with the new seed so yield_events
    # uses the override and the manifest records it accurately.
    if seed_override is not None and seed_override != scenario.seed:
        scenario = scenario.model_copy(update={"seed": seed_override})

    workspace_id = uuid.uuid5(uuid.NAMESPACE_DNS, scenario.workspace_slug)

    # Walk the orchestrator's generator once and partition by account slug.
    # No generator-loop duplication: yield_events already encodes spec ordering,
    # silence handling, and source-type dispatch.
    from src.synthetic.orchestrator import yield_events

    events_by_account: dict[str, list[RawInboundEvent | ProductEvent | dict]] = {}
    all_events: list[RawInboundEvent | ProductEvent | dict] = []
    account_slugs: list[str] = []

    for slug, event in yield_events(scenario, workspace_id):
        if slug not in events_by_account:
            events_by_account[slug] = []
            account_slugs.append(slug)
        events_by_account[slug].append(event)
        all_events.append(event)

    # Build manifest
    input_hash = _scenario_hash(scenario_path)
    # manifest.json carries deterministic state (must be byte-identical across runs
    # for the same scenario+seed per ADR-015 §D5). Wall-clock metadata lives in
    # last_run.json so manifest stays reproducible.
    manifest = {
        "schema_version": 1,
        "scenario_name": scenario.name,
        "scenario_seed": effective_seed,
        "scenario_input_hash": input_hash,
        "generator_version": _generator_version(),
        "signal_count": len(all_events),
        "account_slugs": account_slugs,
    }
    last_run = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario_name": scenario.name,
        "scenario_seed": effective_seed,
    }

    if dry_run:
        print(f"[dry-run] Would write {len(all_events)} signals to {out_dir}")
        print(f"[dry-run] Accounts: {account_slugs}")
        print(f"[dry-run] Manifest: {json.dumps(manifest, indent=2)}")
        return manifest

    # --- Write files ---
    out_dir.mkdir(parents=True, exist_ok=True)

    # manifest.json (reproducible) + last_run.json (wall-clock; debug only)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out_dir / "last_run.json").write_text(json.dumps(last_run, indent=2) + "\n")

    # workspace.json
    org_slug = scenario.workspace_slug
    workspace_json = {
        "slug": scenario.workspace_slug,
        "name": scenario.workspace_slug.replace("-", " ").title(),
        "organization_slug": org_slug,
        "internal_domains": [],
    }
    (out_dir / "workspace.json").write_text(json.dumps(workspace_json, indent=2) + "\n")

    # organization.json
    org_json = {"slug": org_slug, "name": org_slug.replace("-", " ").title()}
    (out_dir / "organization.json").write_text(json.dumps(org_json, indent=2) + "\n")

    # accounts/
    accounts_dir = out_dir / "accounts"
    accounts_dir.mkdir(exist_ok=True)
    for acc_spec in scenario.accounts:
        acc_json = {
            "slug": acc_spec.slug,
            "name": acc_spec.name,
            "primary_domain": acc_spec.primary_domain,
            "additional_domains": acc_spec.additional_domains,
            "vertical": acc_spec.vertical,
            "status": acc_spec.status,
        }
        (accounts_dir / f"{acc_spec.slug}.json").write_text(json.dumps(acc_json, indent=2) + "\n")

    # signals/<account-slug>/NNN.json
    signals_dir = out_dir / "signals"
    signals_dir.mkdir(exist_ok=True)
    for slug, account_events in events_by_account.items():
        acct_dir = signals_dir / slug
        acct_dir.mkdir(exist_ok=True)
        for file_index, event in enumerate(account_events):
            filename = f"{file_index:03d}.json"
            if isinstance(event, ProductEvent):
                # Serialise as native ingest shape so fixtures round-trip through POST /event
                payload = {
                    "event_name": event.event_name,
                    "contact_email": event.contact_email,
                    "event_properties": event.event_properties,
                    "event_id": event.event_id,
                    "occurred_at": (
                        event.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                        if event.occurred_at is not None
                        else None
                    ),
                }
            elif isinstance(event, dict):
                # Structured-signal modalities (plain_ticket, pylon_ticket, granola_note)
                # yield vendor-shaped dicts directly — write as-is.
                payload = event
            else:
                payload = json.loads(event.raw_payload)
            (acct_dir / filename).write_text(json.dumps(payload, indent=2) + "\n")

    return manifest


def read_manifest(out_dir: Path) -> dict:
    """Read and return the manifest.json from a materialised scenario directory."""
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest.json in {out_dir}")
    return json.loads(manifest_path.read_text())
