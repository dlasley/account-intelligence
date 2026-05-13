"""Fast targeted regression validator for the per-week narrative generation path.

WHAT THIS DOES
--------------
Generates per-week narratives for a configurable list of accounts in a single
workspace, audits each via the production audit harness, and reports pass/fail
per (account, week).

The simulator (scripts/simulate_history.py) is the canonical end-to-end per-week
generation path; this script is a faster alternative when you want to validate
specific accounts without running the full simulator stack.

WHEN TO USE
-----------
- Regression check after a prompt edit affecting per-week generation
  (e.g., changes to render_product_usage_trajectory or narrative.v1.md)
- Targeted re-audit of accounts that previously failed
- Cheap-and-fast sanity check during prompt iteration (~$0.003-$0.005 per narrative)

WHEN NOT TO USE
---------------
- For full demo data generation, use simulate_history.py instead (canonical path,
  exercises the full simulator pipeline including supersede chains and dimension scores)
- For current-snapshot narratives (one per account at the current cumulative window),
  trigger the production scheduler — that is NOT this script's job

INTERPRETING RESULTS
--------------------
- "PASS" means the narrative generated AND audited with zero hard-gate failures
- "FAIL(hallucination)" / "FAIL(faithfulness)" / etc. — genuine audit failure;
  investigate by reading the narrative_audits row for that narrative_id
- "ERROR (generation)" — narrative generation itself failed before audit could run;
  usually a JSON parse error or upstream LLM issue; distinct from audit failure

CONFIGURATION
-------------
Edit the _TARGET_ACCOUNTS constant at the top of this file to pick which accounts
to validate. Default is the 2 lowest-pass-rate accounts from the original pre-fix
audit (cascade-infra + crucible), kept as a regression baseline.

USAGE
-----
    uv run python scripts/validate_per_week.py [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from dotenv import load_dotenv

load_dotenv()

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

_WORKSPACE_SLUG = "lattice-build"

# Default accounts: the 2 lowest-pass-rate accounts from the original pre-fix audit.
# These serve as the regression baseline — if they pass, the per-week path is healthy.
# Edit this list to target other accounts.
_TARGET_ACCOUNTS = ["cascade-infra", "crucible"]

# Trajectory window from trajectory.lattice-build.yaml
_TRAJ_START = date(2026, 3, 3)
_TRAJ_END = date(2026, 5, 1)

# 9 weeks: 2026-03-03, 2026-03-10, ..., 2026-04-28 (week_start); each ends +6d
_WEEK_STARTS: list[date] = []
_d = _TRAJ_START
while _d <= _TRAJ_END:
    _WEEK_STARTS.append(_d)
    _d += timedelta(weeks=1)

# Audit run ID prefix — must match DB CHECK: ^(ci|nightly|manual)_[A-Za-z0-9_-]{1,200}$
_AUDIT_RUN_ID_PREFIX = "manual_validate_per_week"


def _week_end(week_start: date) -> date:
    return week_start + timedelta(days=6)


def _week_end_dt(week_start: date) -> datetime:
    """Return 23:00 UTC on week_end — mirrors _narrative_timestamp from executor.py."""
    we = _week_end(week_start)
    return datetime(we.year, we.month, we.day, tzinfo=UTC) + timedelta(hours=23)


def _filter_signals_for_week(
    all_signals: list[Any],
    week_start: date,
) -> list[Any]:
    """Return signals with occurred_at in [week_start 00:00 UTC, week_end 23:00 UTC]."""
    ws_dt = datetime(week_start.year, week_start.month, week_start.day, tzinfo=UTC)
    we_dt = _week_end_dt(week_start)

    result = []
    for sig in all_signals:
        occ = sig.occurred_at
        if isinstance(occ, str):
            try:
                occ = datetime.fromisoformat(occ.replace(" ", "T").replace("+00", "+00:00"))
            except ValueError:
                continue
        if not occ.tzinfo:
            occ = occ.replace(tzinfo=UTC)
        if ws_dt <= occ <= we_dt:
            result.append(sig)
    return result


def run_validation(dry_run: bool = False) -> None:
    import os

    import anthropic as anthropic_sdk

    from src.config.loader import load_config
    from src.db.accounts import get_accounts_for_workspace
    from src.db.contacts import get_contacts_for_account
    from src.db.signals import get_signals_for_account
    from src.db.workspaces import get_workspace_by_slug
    from src.pipeline.generator import generate_narrative
    from supabase import create_client

    sb_client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    config = load_config(_WORKSPACE_SLUG)
    client_ai = anthropic_sdk.Anthropic()

    workspace = get_workspace_by_slug(sb_client, _WORKSPACE_SLUG)
    if workspace is None:
        raise SystemExit(f"Workspace not found: {_WORKSPACE_SLUG!r}")
    workspace_id: uuid.UUID = workspace.id

    accounts = get_accounts_for_workspace(sb_client, workspace_id)
    account_by_slug = {a.slug: a for a in accounts}

    run_ts = int(time.time())
    audit_run_id = f"{_AUDIT_RUN_ID_PREFIX}_{run_ts}"
    print(f"audit_run_id: {audit_run_id}")
    print(f"dry_run: {dry_run}")
    print(f"weeks: {len(_WEEK_STARTS)} ({_WEEK_STARTS[0]} to {_WEEK_STARTS[-1]})")
    print()

    # Wire up audit harness DB client (uses module-level slot in audit_narratives)
    from scripts.audit_narratives import (  # type: ignore[import-not-found]
        GateOutcome,
        _set_supabase_client,
        audit_one_narrative,
        evaluate_gate,
        fetch_audit_context,
    )
    _set_supabase_client(sb_client)

    results: dict[str, dict[str, str]] = {}  # account_slug -> {week_str: pass|fail|skip}
    total_cost = 0.0

    for account_slug in _TARGET_ACCOUNTS:
        account = account_by_slug.get(account_slug)
        if account is None:
            logger.error("Account not found: %s", account_slug)
            continue

        print(f"=== {account_slug} ===")

        # Load all signals for this account once; filter per-week below
        all_signals = get_signals_for_account(sb_client, workspace_id, account.id)
        logger.info("%s: loaded %d signals total", account_slug, len(all_signals))

        contacts = {
            c.id: c
            for c in get_contacts_for_account(sb_client, workspace_id, account.id)
        }

        account_results: dict[str, str] = {}

        for week_start in _WEEK_STARTS:
            week_label = str(week_start)
            we_dt = _week_end_dt(week_start)

            # Per-week slice: only signals from this specific week
            week_signals = _filter_signals_for_week(all_signals, week_start)
            logger.info(
                "%s week=%s signals=%d",
                account_slug, week_label, len(week_signals),
            )

            if not week_signals:
                print(f"  week={week_label}: SKIP (0 signals)")
                account_results[week_label] = "skip_no_signals"
                continue

            if dry_run:
                print(f"  week={week_label}: DRY-RUN (signals={len(week_signals)})")
                account_results[week_label] = "dry_run"
                continue

            # Generate narrative anchored to week_end.
            # generate_narrative() hardcodes now=datetime.now(UTC) internally; we patch
            # so the cascade window arithmetic uses the historical per-week timestamp.
            fake_now = we_dt

            try:
                with patch(
                    "src.pipeline.generator.datetime",
                    wraps=datetime,
                ) as mock_dt:
                    mock_dt.now.return_value = fake_now

                    gen_result = generate_narrative(
                        account=account,
                        signals=week_signals,
                        contacts=contacts,
                        prior_narrative=None,  # each week stands alone
                        config=config,
                        workspace_slug=_WORKSPACE_SLUG,
                        client_db=sb_client,
                        client_anthropic=client_ai,
                    )
            except Exception as exc:
                logger.error("%s week=%s generation failed: %s", account_slug, week_label, exc)
                account_results[week_label] = "error_generation"
                print(f"  week={week_label}: ERROR (generation): {exc}")
                continue

            narrative = gen_result.narrative

            # Backdate generated_at to week_end timestamp for traceability
            try:
                sb_client.table("narratives").update(
                    {"generated_at": we_dt.isoformat()}
                ).eq("id", str(narrative.id)).execute()
            except Exception as exc:
                logger.warning("Failed to backdate narrative %s: %s", narrative.id, exc)

            # Audit the narrative
            try:
                audit_ctx = fetch_audit_context(
                    narrative=narrative,
                    workspace_id=workspace_id,
                    account_id=account.id,
                    client=sb_client,
                )
                audit_result = audit_one_narrative(
                    narrative=narrative,
                    context=audit_ctx,
                    audit_run_id=audit_run_id,
                    audit_source="manual",
                    workspace_id=workspace_id,
                    dry_run=False,
                )
                gate: GateOutcome = evaluate_gate(audit_result)
                cost = (
                    audit_result.prompt_tokens / 1_000_000 * 0.25
                    + audit_result.completion_tokens / 1_000_000 * 2.00
                )
                total_cost += cost
                failures_str = ",".join(gate.failure_criteria)
                status = "PASS" if gate.overall_passed else f"FAIL({failures_str})"
                print(
                    f"  week={week_label}: {status}"
                    f" signals={len(week_signals)}"
                    f" engagement={narrative.engagement}"
                    f" hard_failures={gate.hard_gate_failures}"
                    f" cost=${cost:.4f}"
                )
                account_results[week_label] = (
                    "pass" if gate.overall_passed else f"fail:{failures_str}"
                )
            except Exception as exc:
                logger.error("%s week=%s audit failed: %s", account_slug, week_label, exc)
                account_results[week_label] = "error_audit"
                print(f"  week={week_label}: ERROR (audit): {exc}")

        results[account_slug] = account_results

        # Per-account summary
        passed = sum(1 for v in account_results.values() if v == "pass")
        failed = sum(1 for v in account_results.values() if v.startswith("fail"))
        skipped = sum(
            1 for v in account_results.values()
            if "skip" in v or "dry_run" in v or "error" in v
        )
        print(
            f"  => {account_slug}: {passed}/{len(account_results)} pass,"
            f" {failed} fail, {skipped} skip/error"
        )
        print()

    # Final summary
    print("=" * 60)
    print("SUMMARY")
    print(f"audit_run_id: {audit_run_id}")
    print(f"total_cost: ${total_cost:.4f}")
    print()
    for account_slug, acct_results in results.items():
        passed = sum(1 for v in acct_results.values() if v == "pass")
        audited = sum(1 for v in acct_results.values() if v in ("pass",) or v.startswith("fail"))
        print(f"  {account_slug}: {passed}/{audited} pass (of {len(acct_results)} weeks attempted)")
        for week_label, outcome in sorted(acct_results.items()):
            print(f"    {week_label}: {outcome}")
    print()
    print("PASS THRESHOLD: >=7/9 on each account confirms the per-week path is healthy.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Targeted regression validator for the per-week narrative generation path."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print week/signal counts without calling Claude or GPT. No DB writes.",
    )
    args = parser.parse_args()
    run_validation(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
