"""Capture narrative + scoring snapshots as committed baselines (Phase 4c, planning report §4.5).

Reads the current state of each active narrative in the named workspace,
plus its dimension scores, account overall_health_score, and most-recent
audit verdict. Writes one JSON file per account to
``fixtures/narrative-baselines/<workspace_slug>/<account_slug>.json``.

**Refuses to capture if any active narrative does not have a most-recent
audit verdict of overall_passed=true.** The point of a snapshot baseline
is to lock in audit-clean output as a regression anchor — capturing a
failing narrative would defeat the purpose. Run the audit harness to
green first, then capture.

Run manually after each prompt-tuning iteration that lands and audits clean:

    uv run python scripts/capture_narrative_baselines.py --workspace-slug quantas-labs

(``.env`` is loaded automatically via python-dotenv — no ``--env-file`` flag needed.)

The captured baselines are diffed during code review on any PR that touches
narrative-relevant code (config/prompts/narrative.v1.md, src/pipeline/generator.py,
src/pipeline/confidence.py, src/pipeline/health.py, dimension config). Drift
in deterministic fields (engagement, engagement_rationale, overall_health_score)
is a regression signal; drift in LLM-produced fields (sentiment, narrative text)
is expected within a band and reviewed visually.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load .env from the repo root if present so the script can be run as
# `uv run python scripts/capture_narrative_baselines.py ...` without `--env-file`.
# A no-op if the file does not exist; existing process env vars win.
load_dotenv()

logger = logging.getLogger(__name__)

_BASELINE_DIR = Path("fixtures/narrative-baselines")


def _fetch_workspace_state(sb: Any, workspace_slug: str) -> list[dict]:
    """Return one dict per active narrative with everything needed for the baseline file."""
    ws = sb.table("workspaces").select("id,slug,name").eq("slug", workspace_slug).execute()
    if not ws.data:
        raise SystemExit(f"workspace not found: {workspace_slug}")
    workspace_id = ws.data[0]["id"]

    narratives = (
        sb.table("narratives")
        .select(
            "id,workspace_id,account_id,engagement,sentiment,engagement_rationale,"
            "narrative,signal_window_start,signal_window_end,signals_considered,"
            "prompt_version,generated_at"
        )
        .eq("workspace_id", workspace_id)
        .is_("superseded_at", "null")
        .execute()
        .data
        or []
    )

    out: list[dict] = []
    for n in narratives:
        acct = (
            sb.table("accounts")
            .select("slug,vertical,status,overall_health_score")
            .eq("id", n["account_id"])
            .execute()
            .data[0]
        )
        dscores_raw = (
            sb.table("account_dimension_scores")
            .select("dimension_id,score,scored_at")
            .eq("account_id", n["account_id"])
            .is_("superseded_at", "null")
            .execute()
            .data
            or []
        )
        dim_scores: list[dict] = []
        for d in dscores_raw:
            cfg = (
                sb.table("health_dimension_configs")
                .select("dimension_type,weight,enabled")
                .eq("id", d["dimension_id"])
                .execute()
                .data[0]
            )
            dim_scores.append(
                {
                    "dimension_type": cfg["dimension_type"],
                    "weight": cfg["weight"],
                    "score": d["score"],
                }
            )
        dim_scores.sort(key=lambda x: x["dimension_type"])

        last_audit = (
            sb.table("narrative_audit_runs")
            .select("audit_run_id,overall_passed,audited_at,score_summary")
            .eq("narrative_id", n["id"])
            .order("audited_at", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )

        out.append(
            {
                "narrative": n,
                "account": acct,
                "dim_scores": dim_scores,
                "last_audit": last_audit[0] if last_audit else None,
            }
        )
    return out


def _baseline_dict(state: dict) -> dict:
    """Shape the state record into the on-disk baseline JSON."""
    n = state["narrative"]
    a = state["account"]
    audit = state["last_audit"]
    return {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "account": {
            "slug": a["slug"],
            "vertical": a["vertical"],
            "status": a["status"],
            "overall_health_score": a["overall_health_score"],
        },
        # Deterministic fields — assertions in tests/test_narrative_baselines.py
        # treat drift in these as a regression signal.
        "deterministic": {
            "engagement": n["engagement"],
            "engagement_rationale": n["engagement_rationale"],
            "overall_health_score": a["overall_health_score"],
        },
        # LLM-produced fields — captured for human diff review during code
        # review on narrative-touching PRs. Not asserted byte-equal in tests
        # because LLM output is non-deterministic within a band.
        "llm_produced": {
            "sentiment": n["sentiment"],
            "narrative": n["narrative"],
        },
        # Dimension scores — sorted by dimension_type for stable diffs.
        "dim_scores": state["dim_scores"],
        # Window metadata — useful for diffing context but not asserted.
        "window": {
            "signal_window_start": n["signal_window_start"],
            "signal_window_end": n["signal_window_end"],
            "signal_count": len(n.get("signals_considered") or []),
        },
        # Provenance — what audit run / prompt produced this baseline.
        "provenance": {
            "prompt_version": n["prompt_version"],
            "generated_at": n["generated_at"],
            "audit_run_id": audit["audit_run_id"] if audit else None,
            "audit_overall_passed": audit["overall_passed"] if audit else None,
            "audited_at": audit["audited_at"] if audit else None,
        },
    }


def _enforce_audit_clean(states: list[dict]) -> None:
    """Refuse to capture if any narrative does not have a passing audit on record."""
    failures: list[str] = []
    for s in states:
        slug = s["account"]["slug"]
        audit = s["last_audit"]
        if audit is None:
            failures.append(f"{slug}: no audit run on record")
        elif not audit["overall_passed"]:
            failures.append(
                f"{slug}: most-recent audit failed (audit_run_id={audit['audit_run_id']})"
            )
    if failures:
        joined = "\n  ".join(failures)
        raise SystemExit(
            "Refusing to capture baselines — one or more narratives are not audit-clean:\n  "
            + joined
            + "\n\nRun `scripts/audit_narratives.py --write-db` until 5/5 PASS, "
            + "then re-run capture."
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-slug",
        required=True,
        help="Workspace whose narratives to capture",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: fixtures/narrative-baselines/<workspace_slug>)",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir) if args.out_dir else _BASELINE_DIR / args.workspace_slug

    from supabase import create_client  # type: ignore[import-untyped]

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    states = _fetch_workspace_state(sb, args.workspace_slug)
    if not states:
        logger.warning("No active narratives found for workspace %s", args.workspace_slug)
        return 0

    _enforce_audit_clean(states)

    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for s in states:
        slug = s["account"]["slug"]
        baseline = _baseline_dict(s)
        path = out_dir / f"{slug}.json"
        path.write_text(json.dumps(baseline, indent=2, sort_keys=False) + "\n")
        logger.info("wrote %s", path)
        written += 1

    print(f"Captured {written} baseline file(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
