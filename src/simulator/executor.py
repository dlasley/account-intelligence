"""Batch executor for the trajectory simulator.

Processes pending TrajectoryEntry rows (those with ``generated_at=None``) by:
  1. Expanding each entry into a (week_start, target_health) curve via the
     trajectory primitive.
  2. Synthesising signals for each week via ``week_to_signal_plan`` /
     ``plan_to_scenario``.
  3. Running signals through the production pipeline (``run_scenario``) with
     ``now_anchor=week_start`` so timestamps land at the historical date.
  4. Generating one narrative per week against that week's signal slice (per-week
     mode — the simulator's canonical shape for trajectory charts and daily briefing
     inbox use cases).
  5. Auditing each per-week narrative.
  6. Stamping ``generated_at`` on the entry after its last week completes.

The simulator's purpose is per-week historical narrative generation.
Current-snapshot narratives (one narrative per account at the current cumulative
window) are the production scheduler's responsibility, not the simulator's.

Failure handling:
  - On a per-week failure, retry up to ``config.max_retries`` times then skip.
  - A failed entry keeps ``generated_at=None`` so re-running the CLI retries it.
  - 23505 (unique constraint violation) on signal insert is treated as a no-op
    (same as the normalizer dedup path) — safe to encounter on re-runs.

Import boundary: this module must NOT import ``src.db.*`` directly.
All DB writes happen through the production pipeline or via the ``client`` object
passed by the caller.
"""

from __future__ import annotations

import logging
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass  # supabase.Client imported lazily to keep module importable without all deps

from src.simulator.primitives import primitive_to_curve
from src.simulator.signal_synthesis import plan_to_scenario, week_to_signal_plan
from src.simulator.spec import TrajectoryEntry, TrajectorySpec, load_spec, save_spec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Opus 4.6 pricing constants (update when model changes)
# ---------------------------------------------------------------------------

_OPUS_4_6_PRICING = {
    "input_per_m": 15.0,   # USD per million input tokens
    "output_per_m": 75.0,  # USD per million output tokens
    "cached_per_m": 1.50,  # USD per million cached input tokens
}


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass
class ExecutorConfig:
    """Runtime knobs for a single executor run.

    Attributes:
        workspace_slug: workspace to target.
        spec_path: absolute (or repo-root-relative) path to the trajectory YAML.
        accounts_filter: if not None, only these account slugs are processed.
        weeks_limit: if not None, process at most this many weeks per entry.
        dry_run: synthesise signals and compute health; skip all LLM calls.
        model: override the narrative model (e.g. ``claude-sonnet-4-6``).
        audit_final_week: if True, audit only the final week's narrative per entry.
        audit_all_weeks: if True, audit every per-week narrative (overrides
            audit_final_week).
        max_retries: per-week retry count before skipping (default 1).
        force: skip the existing-production-narratives warning.
    """

    workspace_slug: str
    spec_path: Path
    accounts_filter: list[str] | None = None
    weeks_limit: int | None = None
    dry_run: bool = False
    model: str | None = None
    audit_final_week: bool = True
    audit_all_weeks: bool = False
    max_retries: int = 1
    force: bool = False


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ExecutionResult:
    """Summary of a completed executor run.

    Attributes:
        entries_processed: number of entries that completed successfully.
        entries_skipped: entries skipped due to ``accounts_filter``.
        entries_failed: entries that hit max retries and were left pending.
        weeks_total: sum of weeks processed across all entries.
        narratives_generated: number of ``generate_narrative`` calls made
            (one per week in per-week mode).
        audit_passed: count of audit runs that passed overall.
        audit_failed: count of audit runs that failed at least one hard gate.
        cost_usd: estimated LLM cost (Anthropic + OpenAI) for this run.
        spec_path: path where the spec was written.
    """

    entries_processed: int = 0
    entries_skipped: int = 0
    entries_failed: int = 0
    weeks_total: int = 0
    narratives_generated: int = 0
    audit_passed: int = 0
    audit_failed: int = 0
    cost_usd: float = 0.0
    spec_path: Path = field(default_factory=lambda: Path("."))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _week_end(week_start: date) -> date:
    """Return the last day of the 7-day window starting at week_start."""
    return week_start + timedelta(days=6)


def _narrative_timestamp(week_start: date) -> datetime:
    """Return week_end 23:00 UTC as a tz-aware datetime.

    23:00 on the week's last day is one hour before midnight — a natural
    "end of business" timestamp for a narrative generated at the close of
    a week's signal window.
    """
    we = _week_end(week_start)
    return datetime(we.year, we.month, we.day, tzinfo=UTC) + timedelta(hours=23)


def _is_23505(exc: Exception) -> bool:
    """Return True if *exc* is (or wraps) a PostgreSQL 23505 unique-violation."""
    msg = str(exc)
    return "23505" in msg or "unique" in msg.lower()


# ---------------------------------------------------------------------------
# Account/workspace loading helpers (DB calls via client, no src.db import)
# ---------------------------------------------------------------------------


def _load_workspace_and_accounts(
    workspace_slug: str,
    client: Any,
) -> tuple[Any, uuid.UUID, list[Any]]:
    """Return (workspace, workspace_id, accounts) for the given slug.

    Uses the supabase client directly (service_role context) to avoid importing
    src.db in this module.  The caller may also pass pre-loaded values; this
    function is a convenience for the CLI path.
    """
    from src.db.accounts import get_accounts_for_workspace
    from src.db.workspaces import get_workspace_by_slug

    workspace = get_workspace_by_slug(client, workspace_slug)
    if workspace is None:
        raise SystemExit(f"Workspace not found: {workspace_slug!r}")
    workspace_id: uuid.UUID = workspace.id
    accounts = get_accounts_for_workspace(client, workspace_id)
    return workspace, workspace_id, accounts


def _account_meta(
    accounts: list[Any],
    account_slug: str,
) -> tuple[Any | None, str, str]:
    """Return (account, account_name, primary_domain) for account_slug."""
    for acc in accounts:
        if acc.slug == account_slug:
            return acc, acc.name, acc.primary_domain or f"{acc.slug}.com"
    return None, account_slug.replace("-", " ").title(), f"{account_slug}.com"


def _warn_existing_production_narratives(
    workspace_slug: str,
    spec: TrajectorySpec,
    client: Any,
    force: bool,
) -> None:
    """Log a warning when production (non-simulated) narratives already exist.

    Only fires when not in dry-run mode and not force-suppressed.  A warning
    (not an abort) per the ADR risk table — the author decides whether to
    proceed.
    """
    if force:
        return
    try:
        # Look up workspace_id
        ws_resp = (
            client.table("workspaces")
            .select("id")
            .eq("slug", workspace_slug)
            .single()
            .execute()
        )
        ws_id = ws_resp.data.get("id") if ws_resp.data else None
        if not ws_id:
            return

        # Quick existence check — does any narrative exist for this workspace?
        # We check against the accounts in the spec only.
        account_slugs = list(spec.trajectories.keys())
        acc_resp = (
            client.table("accounts")
            .select("id,slug")
            .eq("workspace_id", ws_id)
            .in_("slug", account_slugs)
            .execute()
        )
        acc_ids = [r["id"] for r in (acc_resp.data or [])]
        if not acc_ids:
            return

        narr_resp = (
            client.table("narratives")
            .select("id")
            .in_("account_id", acc_ids)
            .limit(1)
            .execute()
        )
        if narr_resp.data:
            logger.warning(
                "workspace=%s already has production narratives for accounts %s. "
                "Re-running will supersede them.  Pass --force to suppress this warning.",
                workspace_slug,
                account_slugs,
            )
    except Exception:
        # Non-fatal — observational check; never block execution
        pass


# ---------------------------------------------------------------------------
# Per-week signal ingestion
# ---------------------------------------------------------------------------


def _ingest_week(
    scenario: Any,  # ScenarioSpec
    workspace_id: uuid.UUID,
    workspace: Any,
    accounts: list[Any],
    client: Any,
    week_start: date,
) -> list[Any]:
    """Run the scenario through the production pipeline and return signals.

    Catches 23505 at the individual event level (signal dedup) and treats it
    as a no-op — consistent with the normalizer dedup path.
    """
    from src.synthetic.orchestrator import run_scenario

    now_anchor = datetime(week_start.year, week_start.month, week_start.day, tzinfo=UTC)
    try:
        signals = run_scenario(
            scenario,
            workspace_id,
            workspace,
            accounts,
            client,
            now_anchor=now_anchor,
        )
        return signals
    except Exception as exc:
        if _is_23505(exc):
            logger.info("23505 dedup on signal insert — treating as no-op and continuing")
            return []
        raise


# ---------------------------------------------------------------------------
# Narrative generation wrapper
# ---------------------------------------------------------------------------


def _generate_narrative_for_week(
    account: Any,
    week_signals: list[Any],
    narrative_time: datetime,
    config: Any,
    workspace_slug: str,
    client: Any,
    model_override: str | None,
) -> tuple[Any, int, int, int]:
    """Call the production narrative generator with a per-week signal slice.

    ``week_signals`` is the slice of signals for this specific week (signals with
    occurred_at in [week_start, week_end]).  The generator writes the narrative to
    DB, then we backdate ``generated_at`` to ``narrative_time`` (week_end 23:00 UTC)
    so the trajectory chart sees narratives in historical order.

    Returns ``(narrative, input_tokens, output_tokens, cached_tokens)``.
    """
    import anthropic as anthropic_sdk

    from src.db.contacts import get_contacts_for_account
    from src.db.narratives import get_current_narrative
    from src.pipeline.generator import generate_narrative

    client_ai = anthropic_sdk.Anthropic()

    # Use a config copy with model override if requested.
    effective_config = config
    if model_override and hasattr(config, "narrative_generation"):
        import copy

        effective_config = copy.deepcopy(config)
        effective_config.narrative_generation.model = model_override

    contacts = {
        c.id: c
        for c in get_contacts_for_account(client, account.workspace_id, account.id)
    }
    prior = get_current_narrative(client, account.workspace_id, account.id)

    result = generate_narrative(
        account=account,
        signals=week_signals,
        contacts=contacts,
        prior_narrative=prior,
        config=effective_config,
        workspace_slug=workspace_slug,
        client_db=client,
        client_anthropic=client_ai,
    )

    # Backdate generated_at on the narrative row to narrative_time (week_end 23:00 UTC).
    # The production generator sets generated_at=now() (wall clock); for the trajectory
    # time-series we need the historical timestamp so the chart renders in order.
    # This is the only DB write outside the production pipeline in this module.
    try:
        (
            client.table("narratives")
            .update({"generated_at": narrative_time.isoformat()})
            .eq("id", str(result.narrative.id))
            .execute()
        )
    except Exception:
        logger.warning(
            "Failed to backdate generated_at on narrative %s — wall-clock timestamp will persist",
            result.narrative.id,
            exc_info=True,
        )

    return (
        result.narrative,
        result.input_tokens,
        result.output_tokens,
        result.cached_tokens,
    )


# ---------------------------------------------------------------------------
# Audit wrapper
# ---------------------------------------------------------------------------


def _audit_narrative(
    narrative: Any,
    workspace_id: uuid.UUID,
    client: Any,
    audit_run_id: str,
    dry_run: bool = False,
) -> bool:
    """Run the audit harness for a single narrative. Returns overall_passed."""
    try:
        # Import lazily — openai dep may not be installed in all envs
        import sys

        _repo_root = Path(__file__).parent.parent.parent
        if str(_repo_root) not in sys.path:
            sys.path.insert(0, str(_repo_root))

        from scripts.audit_narratives import (  # type: ignore[import-not-found]
            AuditContext,
            GateOutcome,
            _set_supabase_client,
            audit_one_narrative,
            evaluate_gate,
            fetch_audit_context,
        )

        _set_supabase_client(client)

        account_id = getattr(narrative, "account_id", None)

        if account_id:
            audit_ctx = fetch_audit_context(
                narrative=narrative,
                workspace_id=workspace_id,
                account_id=uuid.UUID(str(account_id)),
                client=client,
            )
        else:
            audit_ctx = AuditContext(
                signals=[],
                contacts=[],
                account_meta={},
                dimension_configs=[],
                product_usage_config={},
                workspace_slug="unknown",
            )

        result = audit_one_narrative(
            narrative=narrative,
            context=audit_ctx,
            audit_run_id=audit_run_id,
            # audit_source must satisfy DB CHECK: ci|nightly|manual.
            # Simulator-origin is encoded in the audit_run_id's `manual_simulator_<ts>`
            # prefix, preserving distinguishability without requiring a new DB value.
            audit_source="manual",
            workspace_id=workspace_id,
            dry_run=dry_run,
        )
        gate: GateOutcome = evaluate_gate(result)
        if not gate.overall_passed:
            logger.warning(
                "Audit hard-gate failure for narrative %s — hard_failures=%d; "
                "narrative written to DB, simulator continuing.",
                narrative.id,
                gate.hard_gate_failures,
            )
        return gate.overall_passed
    except Exception:
        logger.warning("Audit harness failed — skipping audit", exc_info=True)
        return True  # non-fatal; don't abort the entry


# ---------------------------------------------------------------------------
# Per-week signal synthesis + narrative generation
# ---------------------------------------------------------------------------


def _process_week(
    *,
    entry: TrajectoryEntry,
    account: Any,
    account_slug: str,
    account_name: str,
    primary_domain: str,
    workspace: Any,
    workspace_id: uuid.UUID,
    workspace_slug: str,
    all_accounts: list[Any],
    config: Any,
    client: Any,
    result: ExecutionResult,
    audit_run_id: str,
    week_index: int,
    week_start: date,
    target_health: int,
    dry_run: bool,
    frequency_multiplier: float,
    model_override: str | None,
    should_audit: bool,
    is_final_week: bool,
) -> None:
    """Execute one week: synthesise signals AND generate a per-week narrative.

    Per-week is the simulator's canonical shape:
      - Signals synthesised for [week_start, week_end].
      - Narrative generated against that week's signal slice with now=week_end.
      - ``generated_at`` backdated to week_end 23:00 UTC for trajectory ordering.

    ``should_audit`` controls whether to audit the generated narrative.
    ``is_final_week`` is informational (available for logging); both signal synthesis
    and narrative generation run on every week regardless.

    Current-snapshot narratives (one per account at the current cumulative window)
    are the production scheduler's responsibility — not generated here.
    """
    log_prefix = f"account={account_slug} entry={entry.id} week_start={week_start}"
    logger.info(
        "%s target_health=%d status=generating",
        log_prefix,
        target_health,
    )

    # 1. Build the signal plan + scenario for this week.
    plan = week_to_signal_plan(
        account_slug=account_slug,
        week_start=week_start,
        target_health=target_health,
        entry_seed=entry.seed,
        week_index=week_index,
        frequency_multiplier=frequency_multiplier,
    )
    scenario = plan_to_scenario(
        plan=plan,
        workspace_slug=workspace_slug,
        account_name=account_name,
        primary_domain=primary_domain,
        base_timestamp=datetime(week_start.year, week_start.month, week_start.day, tzinfo=UTC),
    )

    if dry_run:
        # In dry-run mode: print the plan, skip all DB writes.
        print(
            f"[dry-run] {account_slug} | {week_start} | target_health={target_health} "
            f"| email={plan.email_count} | product={plan.product_count} "
            f"| axes={plan.axes_overrides}"
        )
        result.narratives_generated += 1
        return

    # 2. Ingest signals through the production pipeline.
    logger.info("%s status=ingesting_signals", log_prefix)
    week_signals = _ingest_week(
        scenario=scenario,
        workspace_id=workspace_id,
        workspace=workspace,
        accounts=all_accounts,
        client=client,
        week_start=week_start,
    )
    logger.info("%s signals_ingested=%d", log_prefix, len(week_signals))

    # 3. Generate per-week narrative against this week's signal slice.
    if account is None:
        raise RuntimeError(
            f"account '{account_slug}' not found in workspace {workspace_slug}"
        )

    narrative_time = _narrative_timestamp(week_start)
    logger.info(
        "%s status=narrative_generation narrative_time=%s signals=%d",
        log_prefix,
        narrative_time.isoformat(),
        len(week_signals),
    )

    narrative, input_tok, output_tok, cached_tok = _generate_narrative_for_week(
        account=account,
        week_signals=week_signals,
        narrative_time=narrative_time,
        config=config,
        workspace_slug=workspace_slug,
        client=client,
        model_override=model_override,
    )
    result.narratives_generated += 1

    # Accumulate cost at Opus 4.6 pricing.
    input_cost = input_tok / 1_000_000 * _OPUS_4_6_PRICING["input_per_m"]
    output_cost = output_tok / 1_000_000 * _OPUS_4_6_PRICING["output_per_m"]
    cached_cost = cached_tok / 1_000_000 * _OPUS_4_6_PRICING["cached_per_m"]
    result.cost_usd += input_cost + output_cost + cached_cost

    logger.info(
        "%s status=narrative_complete narrative_id=%s engagement=%s",
        log_prefix,
        narrative.id,
        narrative.engagement,
    )

    # 4. Optionally audit.
    if should_audit:
        logger.info("%s status=auditing", log_prefix)
        passed = _audit_narrative(
            narrative=narrative,
            workspace_id=workspace_id,
            client=client,
            audit_run_id=audit_run_id,
            dry_run=dry_run,
        )
        if passed:
            result.audit_passed += 1
        else:
            result.audit_failed += 1


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------


def execute_trajectory(
    spec: TrajectorySpec,
    *,
    accounts: list[str] | None = None,
    weeks: int | None = None,
    dry_run: bool = False,
    model: str | None = None,
    audit_all: bool = False,
    _client: Any = None,  # injected in tests; real path uses get_client()
) -> ExecutionResult:
    """Execute all pending entries in *spec*, returning a summary.

    This is the functional core — the CLI wraps it.  ``spec`` is mutated in-place
    (``generated_at`` fields are stamped) and the caller is responsible for saving
    it to disk if desired (the CLI calls ``save_spec`` after each entry).

    Per-week mode: each (account, week) pair produces one narrative generated
    against that week's signal slice.

    Args:
        spec: trajectory spec loaded from YAML.
        accounts: if not None, limit execution to these account slugs.
        weeks: cap the number of weeks processed per entry.
        dry_run: skip LLM calls; compute deterministic scores only.
        model: narrative model override.
        audit_all: audit every per-week narrative (default: final week only).
        _client: supabase client (injected in tests; production path calls get_client()).
    """
    from src.config.loader import load_config

    if _client is None:
        from src.db.client import get_client

        client = get_client()
    else:
        client = _client

    workspace_slug = spec.workspace_slug
    config = load_config(workspace_slug)

    workspace, workspace_id, all_accounts = _load_workspace_and_accounts(
        workspace_slug, client
    )

    if not dry_run:
        _warn_existing_production_narratives(workspace_slug, spec, client, force=False)

    result = ExecutionResult(spec_path=Path("."))
    audit_run_id = f"manual_simulator_{int(time.time())}"

    for account_slug, entries in spec.trajectories.items():
        if accounts is not None and account_slug not in accounts:
            result.entries_skipped += 1
            continue

        account, account_name, primary_domain = _account_meta(all_accounts, account_slug)

        # Sort pending entries by start_date (ascending).
        pending = [e for e in entries if e.generated_at is None]
        pending.sort(key=lambda e: e.start_date)
        if not pending:
            continue

        for entry in pending:
            success = _execute_entry(
                entry=entry,
                account=account,
                account_slug=account_slug,
                account_name=account_name,
                primary_domain=primary_domain,
                workspace=workspace,
                workspace_id=workspace_id,
                workspace_slug=workspace_slug,
                all_accounts=all_accounts,
                config=config,
                client=client,
                result=result,
                audit_run_id=audit_run_id,
                weeks_limit=weeks,
                dry_run=dry_run,
                model_override=model,
                max_retries=1,
                audit_all=audit_all,
            )
            if success:
                result.entries_processed += 1
            else:
                result.entries_failed += 1

    return result


def _execute_entry(
    *,
    entry: TrajectoryEntry,
    account: Any,
    account_slug: str,
    account_name: str,
    primary_domain: str,
    workspace: Any,
    workspace_id: uuid.UUID,
    workspace_slug: str,
    all_accounts: list[Any],
    config: Any,
    client: Any,
    result: ExecutionResult,
    audit_run_id: str,
    weeks_limit: int | None,
    dry_run: bool,
    model_override: str | None,
    max_retries: int,
    audit_all: bool,
) -> bool:
    """Process a single entry. Returns True on success, False on fatal failure."""
    curve = primitive_to_curve(
        entry.primitive,
        entry.params.model_extra or {},
        entry.start_date,
        entry.end_date,
        entry.seed,
    )

    effective_weeks = curve[:weeks_limit] if weeks_limit is not None else curve
    n_weeks = len(effective_weeks)

    frequency_multiplier = float(getattr(account, "frequency_multiplier", 1.0)) if account else 1.0

    for week_index, (week_start, target_health) in enumerate(effective_weeks):
        log_prefix = f"account={account_slug} entry={entry.id} week={week_index + 1}/{n_weeks}"
        is_final_week = week_index == n_weeks - 1
        # Audit: always audit if audit_all; otherwise audit only the final week.
        should_audit = audit_all or is_final_week

        success = False
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                _process_week(
                    entry=entry,
                    account=account,
                    account_slug=account_slug,
                    account_name=account_name,
                    primary_domain=primary_domain,
                    workspace=workspace,
                    workspace_id=workspace_id,
                    workspace_slug=workspace_slug,
                    all_accounts=all_accounts,
                    config=config,
                    client=client,
                    result=result,
                    audit_run_id=audit_run_id,
                    week_index=week_index,
                    week_start=week_start,
                    target_health=target_health,
                    dry_run=dry_run,
                    frequency_multiplier=frequency_multiplier,
                    model_override=model_override,
                    should_audit=should_audit,
                    is_final_week=is_final_week,
                )
                success = True
                break
            except Exception as exc:
                last_exc = exc
                if _is_23505(exc):
                    logger.info("%s 23505 dedup — treating as success", log_prefix)
                    success = True
                    break
                if attempt < max_retries:
                    logger.warning(
                        "%s attempt %d failed: %s — retrying",
                        log_prefix,
                        attempt + 1,
                        exc,
                    )
                else:
                    logger.error(
                        "%s failed after %d attempt(s): %s\n%s",
                        log_prefix,
                        attempt + 1,
                        exc,
                        traceback.format_exc(),
                    )

        if not success:
            # Entry failed — leave generated_at=None so re-run retries it.
            logger.error(
                "account=%s entry=%s FAILED (last error: %s) — entry left pending",
                account_slug,
                entry.id,
                last_exc,
            )
            return False

        result.weeks_total += 1

    entry.generated_at = datetime.now(UTC)
    return True


def run(config: ExecutorConfig, client: Any) -> ExecutionResult:
    """Load spec from *config.spec_path*, run the executor, save the spec.

    This is the top-level entry point called by ``scripts/simulate_history.py``.
    The spec is saved to disk after every successfully completed entry so that
    a crash mid-run leaves the file in a consistent resumable state.

    Per-week mode: each (account, week) produces one narrative generated against
    that week's signal slice.  Current-snapshot narratives come from the production
    narrative scheduler — not from this function.
    """
    spec = load_spec(config.spec_path)

    result = ExecutionResult(spec_path=config.spec_path)

    from src.config.loader import load_config

    workspace_config = load_config(config.workspace_slug)

    workspace, workspace_id, all_accounts = _load_workspace_and_accounts(
        config.workspace_slug, client
    )

    if not config.dry_run:
        _warn_existing_production_narratives(
            config.workspace_slug, spec, client, force=config.force
        )

    audit_run_id = f"manual_simulator_{int(time.time())}"
    should_audit_all = config.audit_all_weeks

    for account_slug, entries in spec.trajectories.items():
        if config.accounts_filter is not None and account_slug not in config.accounts_filter:
            result.entries_skipped += 1
            continue

        account, account_name, primary_domain = _account_meta(all_accounts, account_slug)
        frequency_multiplier = (
            float(getattr(account, "frequency_multiplier", 1.0)) if account else 1.0
        )

        pending = [e for e in entries if e.generated_at is None or config.force]
        pending.sort(key=lambda e: e.start_date)
        if not pending:
            continue

        for entry in pending:
            curve = primitive_to_curve(
                entry.primitive,
                entry.params.model_extra or {},
                entry.start_date,
                entry.end_date,
                entry.seed,
            )
            effective_weeks = (
                curve[: config.weeks_limit] if config.weeks_limit is not None else curve
            )
            n_weeks = len(effective_weeks)

            entry_success = True
            for week_index, (week_start, target_health) in enumerate(effective_weeks):
                log_prefix = (
                    f"account={account_slug} entry={entry.id} week={week_index + 1}/{n_weeks}"
                )
                is_final_week = week_index == n_weeks - 1
                # Audit: always audit if audit_all_weeks; otherwise audit only the final week.
                should_audit = should_audit_all or (config.audit_final_week and is_final_week)

                week_ok = False
                last_exc: Exception | None = None
                for attempt in range(config.max_retries + 1):
                    try:
                        _process_week(
                            entry=entry,
                            account=account,
                            account_slug=account_slug,
                            account_name=account_name,
                            primary_domain=primary_domain,
                            workspace=workspace,
                            workspace_id=workspace_id,
                            workspace_slug=config.workspace_slug,
                            all_accounts=all_accounts,
                            config=workspace_config,
                            client=client,
                            result=result,
                            audit_run_id=audit_run_id,
                            week_index=week_index,
                            week_start=week_start,
                            target_health=target_health,
                            dry_run=config.dry_run,
                            frequency_multiplier=frequency_multiplier,
                            model_override=config.model,
                            should_audit=should_audit,
                            is_final_week=is_final_week,
                        )
                        week_ok = True
                        break
                    except Exception as exc:
                        last_exc = exc
                        if _is_23505(exc):
                            logger.info("%s 23505 dedup — treating as success", log_prefix)
                            week_ok = True
                            break
                        if attempt < config.max_retries:
                            logger.warning(
                                "%s attempt %d failed: %s — retrying",
                                log_prefix,
                                attempt + 1,
                                exc,
                            )
                        else:
                            logger.error(
                                "%s failed after %d attempt(s): %s",
                                log_prefix,
                                attempt + 1,
                                exc,
                            )

                if not week_ok:
                    logger.error(
                        "account=%s entry=%s FAILED (week=%d, error=%s) — entry left pending",
                        account_slug,
                        entry.id,
                        week_index + 1,
                        last_exc,
                    )
                    entry_success = False
                    break

                result.weeks_total += 1

            if not entry_success:
                result.entries_failed += 1
                continue

            # Stamp entry as complete and save (resumability: crash mid-run leaves
            # completed entries stamped so re-run skips them).
            entry.generated_at = datetime.now(UTC)
            save_spec(spec, config.spec_path)
            result.entries_processed += 1

    logger.info(
        "simulator run complete accounts=%d narratives=%d cost_usd=%.2f errors=%d",
        result.entries_processed + result.entries_failed,
        result.narratives_generated,
        result.cost_usd,
        result.entries_failed,
    )

    return result


# ---------------------------------------------------------------------------
# --revert-from implementation
# ---------------------------------------------------------------------------


def revert_from(
    workspace_slug: str,
    spec_path: Path,
    revert_date: date,
    client: Any,
) -> None:
    """Delete historical simulation data on or after *revert_date* and prune the spec.

    Deletes rows from 4 tables matching ``workspace_id`` + affected accounts +
    date predicate, then removes YAML entries with ``start_date >= revert_date``
    from the spec file.

    Tables:
        - ``signals``              (predicate on ``occurred_at``)
        - ``narratives``           (predicate on ``generated_at``)
        - ``account_dimension_scores``  (predicate on ``scored_at``)
        - ``account_health_snapshots``  (predicate on ``computed_at``)

    All deletes use the service_role client directly (bypasses RLS), consistent
    with the ADR-019 single-mutation-surface pattern for internal tooling.
    """
    spec = load_spec(spec_path)
    if spec.workspace_slug != workspace_slug:
        raise ValueError(
            f"Spec workspace_slug {spec.workspace_slug!r} != --workspace {workspace_slug!r}"
        )

    # Look up workspace_id.
    ws_resp = (
        client.table("workspaces").select("id").eq("slug", workspace_slug).single().execute()
    )
    ws_data = ws_resp.data
    if not ws_data:
        raise SystemExit(f"Workspace not found: {workspace_slug!r}")
    ws_id: str = ws_data["id"]

    account_slugs = list(spec.trajectories.keys())
    if not account_slugs:
        logger.warning("No accounts in spec — nothing to revert.")
        return

    # Fetch account IDs for the target accounts.
    acc_resp = (
        client.table("accounts")
        .select("id,slug")
        .eq("workspace_id", ws_id)
        .in_("slug", account_slugs)
        .execute()
    )
    acc_rows = acc_resp.data or []
    acc_ids = [r["id"] for r in acc_rows]
    if not acc_ids:
        logger.warning("No matching accounts found in DB for workspace=%s — no-op.", workspace_slug)
        return

    revert_iso = revert_date.isoformat()

    # Count rows before deleting (for logging).
    deleted_counts: dict[str, int] = {}

    def _delete_since(table: str, ts_col: str) -> None:
        resp = (
            client.table(table)
            .delete()
            .in_("account_id", acc_ids)
            .gte(ts_col, revert_iso)
            .execute()
        )
        deleted_counts[table] = len(resp.data or [])

    # Fetch narrative IDs that would be deleted so we can cascade to child audit
    # tables before deleting the narratives parent row.  PostgREST does not
    # support subquery filters, so we resolve the ID set here first.
    narr_ids_resp = (
        client.table("narratives")
        .select("id")
        .in_("account_id", acc_ids)
        .gte("generated_at", revert_iso)
        .execute()
    )
    narr_ids = [r["id"] for r in (narr_ids_resp.data or [])]

    # Delete FK children of narratives before the parent row.
    if narr_ids:
        for audit_child in ("narrative_audits", "narrative_audit_runs"):
            resp = (
                client.table(audit_child)
                .delete()
                .in_("narrative_id", narr_ids)
                .execute()
            )
            deleted_counts[audit_child] = len(resp.data or [])
    else:
        deleted_counts["narrative_audits"] = 0
        deleted_counts["narrative_audit_runs"] = 0

    _delete_since("signals", "occurred_at")
    _delete_since("narratives", "generated_at")
    _delete_since("account_dimension_scores", "scored_at")
    _delete_since("account_health_snapshots", "computed_at")

    logger.info(
        "revert_from=%s deleted: narrative_audits=%d narrative_audit_runs=%d "
        "signals=%d narratives=%d dimension_scores=%d health_snapshots=%d",
        revert_iso,
        deleted_counts.get("narrative_audits", 0),
        deleted_counts.get("narrative_audit_runs", 0),
        deleted_counts.get("signals", 0),
        deleted_counts.get("narratives", 0),
        deleted_counts.get("account_dimension_scores", 0),
        deleted_counts.get("account_health_snapshots", 0),
    )

    # Prune YAML entries with start_date >= revert_date.
    entries_removed = 0
    for slug in list(spec.trajectories.keys()):
        original = spec.trajectories[slug]
        kept = [e for e in original if e.start_date < revert_date]
        entries_removed += len(original) - len(kept)
        spec.trajectories[slug] = kept

    save_spec(spec, spec_path)
    logger.info(
        "Removed %d YAML entries with start_date >= %s from %s",
        entries_removed,
        revert_iso,
        spec_path,
    )

    print(
        f"revert_from={revert_iso}: "
        f"narrative_audits={deleted_counts.get('narrative_audits', 0)} "
        f"narrative_audit_runs={deleted_counts.get('narrative_audit_runs', 0)} "
        f"signals={deleted_counts.get('signals', 0)} "
        f"narratives={deleted_counts.get('narratives', 0)} "
        f"dimension_scores={deleted_counts.get('account_dimension_scores', 0)} "
        f"health_snapshots={deleted_counts.get('account_health_snapshots', 0)} "
        f"yaml_entries_removed={entries_removed}"
    )
