"""Diff committed narrative baselines against current Supabase state (Phase 4c).

Companion to ``scripts/capture_narrative_baselines.py``. Reads the JSON
baselines under ``fixtures/narrative-baselines/<workspace_slug>/`` and
compares the deterministic fields (engagement, engagement_rationale,
overall_health_score, dim_scores) against the current live state of
each account's most-recent narrative.

Drift in deterministic fields is treated as a regression signal — engagement
is a pure function of signal count + window + contact diversity, and the
rationale string follows directly. Any drift means the input fixtures
changed, the scoring code changed, or a baseline file was edited by hand.

LLM-produced fields (sentiment, narrative text) are NOT compared — they
drift within a band by design due to LLM non-determinism, and the audit
harness is what catches semantic regressions in those.

Usage::

    uv run python scripts/check_narrative_baselines.py --workspace-slug elicit

(``.env`` is loaded automatically via python-dotenv — no ``--env-file`` flag needed.)

Exit codes::

    0 — all baselines match the live state on deterministic fields
    1 — drift detected (printed as a structured diff)
    2 — runtime error (workspace missing, etc.)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load .env from the repo root if present so the script can be run as
# `uv run python scripts/check_narrative_baselines.py ...` without `--env-file`.
# A no-op if the file does not exist; existing process env vars win.
load_dotenv()

logger = logging.getLogger(__name__)

_BASELINE_ROOT = Path("fixtures/narrative-baselines")


def _load_baselines(baseline_dir: Path) -> dict[str, dict]:
    """Map account_slug -> baseline dict."""
    out: dict[str, dict] = {}
    for path in sorted(baseline_dir.glob("*.json")):
        data = json.loads(path.read_text())
        slug = data["account"]["slug"]
        if slug in out:
            raise SystemExit(f"duplicate baseline for slug {slug!r}: {path}")
        out[slug] = data
    return out


def _fetch_live_state(sb: Any, workspace_slug: str) -> dict[str, dict]:
    """Map account_slug -> live state dict mirroring the baseline shape on deterministic fields."""
    ws = sb.table("workspaces").select("id").eq("slug", workspace_slug).execute()
    if not ws.data:
        raise SystemExit(f"workspace not found: {workspace_slug}")
    workspace_id = ws.data[0]["id"]

    narratives = (
        sb.table("narratives")
        .select("id,account_id,engagement,engagement_rationale")
        .eq("workspace_id", workspace_id)
        .is_("superseded_at", "null")
        .execute()
        .data
        or []
    )

    out: dict[str, dict] = {}
    for n in narratives:
        acct = (
            sb.table("accounts")
            .select("slug,overall_health_score")
            .eq("id", n["account_id"])
            .execute()
            .data[0]
        )
        dscores_raw = (
            sb.table("account_dimension_scores")
            .select("dimension_id,score")
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
                .select("dimension_type,weight")
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
        out[acct["slug"]] = {
            "engagement": n["engagement"],
            "engagement_rationale": n["engagement_rationale"],
            "overall_health_score": acct["overall_health_score"],
            "dim_scores": dim_scores,
        }
    return out


def _diff_one(slug: str, baseline: dict, live: dict) -> list[str]:
    """Return a list of human-readable diff lines for one account, or [] if matched."""
    diffs: list[str] = []
    det = baseline["deterministic"]
    if det["engagement"] != live["engagement"]:
        diffs.append(f"  engagement: baseline={det['engagement']} live={live['engagement']}")
    if det["engagement_rationale"] != live["engagement_rationale"]:
        diffs.append(
            f"  engagement_rationale:\n    baseline: {det['engagement_rationale']!r}\n"
            f"    live:     {live['engagement_rationale']!r}"
        )
    if det["overall_health_score"] != live["overall_health_score"]:
        diffs.append(
            f"  overall_health_score: baseline={det['overall_health_score']} "
            f"live={live['overall_health_score']}"
        )
    if baseline["dim_scores"] != live["dim_scores"]:
        diffs.append(
            "  dim_scores:\n"
            f"    baseline: {baseline['dim_scores']}\n"
            f"    live: {live['dim_scores']}"
        )
    if diffs:
        return [f"DRIFT in {slug}:", *diffs]
    return []


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-slug",
        required=True,
        help="Workspace whose narratives to check",
    )
    parser.add_argument(
        "--baseline-dir",
        default=None,
        help="Override the baseline directory "
        "(default: fixtures/narrative-baselines/<workspace_slug>)",
    )
    args = parser.parse_args(argv)

    baseline_dir = (
        Path(args.baseline_dir) if args.baseline_dir else _BASELINE_ROOT / args.workspace_slug
    )
    if not baseline_dir.exists():
        print(f"baseline directory not found: {baseline_dir}")
        return 2

    baselines = _load_baselines(baseline_dir)
    if not baselines:
        print(f"no baseline files in {baseline_dir}")
        return 2

    from supabase import create_client  # type: ignore[import-untyped]

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    live = _fetch_live_state(sb, args.workspace_slug)

    missing_in_live = sorted(set(baselines) - set(live))
    new_in_live = sorted(set(live) - set(baselines))
    common = sorted(set(baselines) & set(live))

    drift_lines: list[str] = []
    for slug in common:
        drift_lines.extend(_diff_one(slug, baselines[slug], live[slug]))

    if missing_in_live:
        drift_lines.append(f"BASELINE EXISTS but no live narrative: {missing_in_live}")
    if new_in_live:
        drift_lines.append(f"NEW LIVE NARRATIVE without baseline: {new_in_live}")

    if drift_lines:
        print("\n".join(drift_lines))
        drifted = sum(1 for line in drift_lines if line.startswith("DRIFT"))
        print(f"\n{len(common)} accounts compared, {drifted} drifted")
        return 1
    print(f"OK — {len(common)}/{len(baselines)} accounts match deterministic fields")
    return 0


if __name__ == "__main__":
    sys.exit(main())
