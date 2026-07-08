"""Re-anchor a demo workspace's signal timestamps so the corpus reads as recent.

WHAT
    Synthetic demo corpora are anchored to a fixed historical date window. As
    wall-clock time passes, the live demo shows "last signal 2 months ago"
    everywhere, which undercuts a real-time-intelligence pitch. This tool shifts
    every signal's ``occurred_at`` (and the absolute timestamp embedded in
    product-event bodies) forward by a single global delta so the most-recent
    signal lands a couple of days before a target date, preserving the natural
    per-account spread (which drives health differentiation).

WHEN TO USE
    Before a demo, to freshen an existing, already-audit-clean corpus WITHOUT
    regenerating content. This is the low-risk path: it keeps the known-good
    signal stories and only moves dates. Pair it with a narrative regen + audit
    (commands are printed at the end, or run with --regen).

WHEN NOT TO USE
    - When you want genuinely different stories or a new health profile: that is
      a scenario-authoring + full re-synthesis job (higher audit risk), not this.
    - On a production/customer workspace. This mutates timestamps in place.

HOW IT WORKS
    delta = target_latest - current_global_max(occurred_at)
    Every non-deleted signal's occurred_at is shifted by delta. Product-event
    bodies embed an absolute ISO timestamp ("At 2026-03-08T...") that is shifted
    by the same delta so the feed text stays consistent with the shifted date.
    A JSON snapshot (all original timestamps + bodies) is written before any
    write, so the shift is reversible by applying the inverse delta.
    ``created_at`` is intentionally NOT shifted (row-insert bookkeeping, not
    demo-visible). Narratives are NOT regenerated here (their prose cites dates,
    so they must be regenerated after the shift — see the printed next steps).

    A cruft scan warns (never auto-deletes) about signals whose subject/body
    contains a public-provider email address (gmail/outlook/etc.), which in an
    all-synthetic corpus usually means leftover manual test traffic.

USAGE
    # dry-run (default): print the plan, write nothing
    uv run python scripts/reanchor_demo_data.py --workspace-slug lattice-build

    # execute the shift, land latest signal 2 days before today
    uv run python scripts/reanchor_demo_data.py --workspace-slug lattice-build --execute

    # execute against an explicit target date, then regenerate narratives
    uv run python scripts/reanchor_demo_data.py --workspace-slug lattice-build \
        --target-latest 2026-07-13 --execute --regen
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from src.db.client import get_client  # noqa: E402

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00")
_PUBLIC_EMAIL = re.compile(
    r"@(?:gmail|googlemail|outlook|hotmail|yahoo|icloud|proton|protonmail|aol)\.com",
    re.IGNORECASE,
)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def main() -> int:
    p = argparse.ArgumentParser(description="Re-anchor demo signal timestamps to recent.")
    p.add_argument("--workspace-slug", default="lattice-build")
    p.add_argument(
        "--target-latest",
        default=None,
        help="Date the most-recent signal should land on (YYYY-MM-DD). "
        "Default: --days-before days before now.",
    )
    p.add_argument(
        "--days-before",
        type=int,
        default=2,
        help="If --target-latest is unset, land the latest signal this many days "
        "before now (default 2).",
    )
    p.add_argument("--execute", action="store_true", help="Apply writes (default: dry-run).")
    p.add_argument(
        "--regen",
        action="store_true",
        help="After --execute, shell out to generate-narratives --all.",
    )
    p.add_argument("--backup-path", default=None, help="Snapshot path (default: repo tmp).")
    args = p.parse_args()

    now = datetime.now(UTC)
    if args.target_latest:
        target = datetime.fromisoformat(args.target_latest).replace(
            hour=12, minute=0, second=0, microsecond=0, tzinfo=UTC
        )
    else:
        target = (now - timedelta(days=args.days_before)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )

    c = get_client()
    ws = c.table("workspaces").select("id").eq("slug", args.workspace_slug).execute()
    if not ws.data:
        print(f"Workspace not found: {args.workspace_slug}")
        return 1
    wsid = ws.data[0]["id"]

    sig = (
        c.table("signals")
        .select("id,account_id,source_type,occurred_at,subject,body")
        .eq("workspace_id", wsid)
        .is_("deleted_at", "null")
        .execute()
        .data
    )
    if not sig:
        print("No signals found.")
        return 1

    global_max = max(_parse(s["occurred_at"]) for s in sig)
    delta = target - global_max

    acc = c.table("accounts").select("id,slug").eq("workspace_id", wsid).execute().data
    slug_of = {a["id"]: a["slug"] for a in acc}
    per_max: dict = defaultdict(lambda: datetime.min.replace(tzinfo=UTC))
    for s in sig:
        t = _parse(s["occurred_at"])
        if t > per_max[s["account_id"]]:
            per_max[s["account_id"]] = t

    prod = [s for s in sig if s.get("source_type") == "product_event"]

    # Cruft scan (warn only).
    cruft = [
        s
        for s in sig
        if _PUBLIC_EMAIL.search((s.get("subject") or "") + " " + (s.get("body") or ""))
    ]

    print(f"MODE: {'EXECUTE' if args.execute else 'DRY-RUN'}  workspace={args.workspace_slug}")
    print(f"current global_max = {global_max.isoformat()}")
    print(f"target_latest      = {target.isoformat()}")
    print(f"delta              = {'+' if delta.days >= 0 else ''}{delta.days} days")
    print(f"signals to shift   = {len(sig)}  (product bodies: {len(prod)})")
    print("per-account max occurred_at  before -> after:")
    for aid, mx in sorted(per_max.items(), key=lambda kv: kv[1]):
        print(f"  {slug_of.get(aid, str(aid)[:8]):<24} {mx.date()} -> {(mx + delta).date()}")
    if cruft:
        print(
            f"\n  WARNING: {len(cruft)} signal(s) contain a public-provider email "
            f"(possible leftover test cruft — review, this tool will NOT delete them):"
        )
        for s in cruft[:10]:
            print(f"    {s['id']} account={slug_of.get(s['account_id'])} subj={s.get('subject')!r}")

    if not args.execute:
        print("\nDRY-RUN complete. No writes. Re-run with --execute to apply.")
        return 0

    backup_path = Path(
        args.backup_path
        or Path(__file__).parent.parent / f"reanchor_backup_{args.workspace_slug}.json"
    )
    backup_path.write_text(
        json.dumps(
            {
                "workspace_slug": args.workspace_slug,
                "delta_days": delta.days,
                "delta_seconds": delta.total_seconds(),
                "target_latest": target.isoformat(),
                "signals": [
                    {"id": s["id"], "occurred_at": s["occurred_at"], "body": s.get("body")}
                    for s in sig
                ],
            },
            indent=2,
        )
    )
    print(f"\nSnapshot backup -> {backup_path}")

    shifted = 0
    for s in sig:
        updates = {"occurred_at": (_parse(s["occurred_at"]) + delta).isoformat()}
        if s.get("source_type") == "product_event" and s.get("body"):

            def _shift(m: re.Match) -> str:
                return (_parse(m.group(0)) + delta).isoformat()

            new_body = _ISO.sub(_shift, s["body"])
            if new_body != s["body"]:
                updates["body"] = new_body
        c.table("signals").update(updates).eq("id", s["id"]).execute()
        shifted += 1
        if shifted % 100 == 0:
            print(f"  shifted {shifted}/{len(sig)}")
    print(f"shifted {shifted} signals by {delta.days:+d} days")

    print("\nNext steps (narratives cite dates — they MUST be regenerated after a shift):")
    regen_cmd = [
        "uv",
        "run",
        "python",
        "-m",
        "src.worker",
        "generate-narratives",
        "--workspace-slug",
        args.workspace_slug,
        "--all",
    ]
    audit_hint = "uv run python scripts/audit_narratives.py --write-db --audit-source manual"
    if args.regen:
        print("  running: " + " ".join(regen_cmd))
        subprocess.run(regen_cmd, check=True)
        print(f"\n  now audit (exit criterion): {audit_hint}")
    else:
        print("  " + " ".join(regen_cmd))
        print("  " + audit_hint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
