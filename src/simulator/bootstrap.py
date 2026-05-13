"""Bootstrap subcommand for the trajectory simulator.

Inspects current workspace state (overall_health_score + dimension scores) and
proposes a trajectory spec as a starting point for human authoring.  The output
is a YAML file the author can review, adjust, and then pass to the executor.

See ADR-021 §D13 (inputs) and §D14 (heuristic) for the full design rationale.

Entry point: ``bootstrap_workspace(config, client)``
             called from ``scripts/simulate_history.py --bootstrap``.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.simulator.author import derive_seed
from src.simulator.spec import (
    TrajectoryEntry,
    TrajectoryParams,
    TrajectorySpec,
    check_collision,
    generate_entry_id,
    load_spec,
    save_spec,
    spec_path_for_workspace,
)

if TYPE_CHECKING:
    pass  # supabase.Client typed as Any to avoid a hard import

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration + result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BootstrapConfig:
    """Configuration for the bootstrap subcommand."""

    workspace_slug: str
    weeks: int = 4  # default entry duration: 4 weeks back from today
    out_path: Path | None = None  # default: spec_path_for_workspace(workspace_slug)
    force: bool = False  # overwrite / merge into existing file if present


@dataclass
class BootstrapResult:
    """Counts returned from bootstrap_workspace after a successful run."""

    proposed_count: int  # entries written to the spec
    skipped_deleted: int  # accounts skipped (soft-deleted)
    skipped_no_health: int  # accounts with NULL overall_health_score that used fallback
    skipped_covered: int  # accounts whose date range was already fully covered
    skipped_system: int  # accounts skipped because slug starts with '_' (system pseudo-accounts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _clamp(v: int, lo: int = 1, hi: int = 100) -> int:
    return max(lo, min(hi, v))


def _propose_entry(
    account_slug: str,
    overall_health: int | None,
    dim_scores: list[int],  # list of latest active scores; empty = no scores available
    status: str,
    existing_entries: list[TrajectoryEntry],
    start_date: date,
    end_date: date,
    workspace_slug: str,
) -> TrajectoryEntry | None:
    """Apply the D14 heuristic and return a TrajectoryEntry, or None to skip.

    Deletion is handled upstream (callers must filter out deleted_at IS NOT NULL).
    This function handles: NULL health, candidate status, no dim scores, and the
    five health-band rules.

    Seed derivation follows ADR-021 §D14:
        abs(hash(f"{workspace_slug}:{account_slug}:{start_date.isoformat()}")) % 9_999_999

    The start_date component is load-bearing — two entries for the same account at
    different start_dates must produce different seeds to avoid sharing signal-axis
    draws.  See ADR-021 §Consequences.
    """
    # Collision detection: if the full proposed range is already covered, skip.
    verdict = check_collision(existing_entries, start_date, end_date)
    if verdict.collides:
        # The proposed range overlaps an existing entry.  If continuation logic
        # has already shifted start_date past the collision, this shouldn't fire.
        # If it still fires (e.g. force=True with a full-coverage spec), skip.
        if verdict.recommend_start is not None and verdict.recommend_start > end_date:
            return None

    seed = derive_seed(workspace_slug, account_slug, start_date)
    entry_id = generate_entry_id()

    # ── Candidate status: always recovering (D14) ────────────────────────────
    if status == "candidate":
        return TrajectoryEntry(
            id=entry_id,
            start_date=start_date,
            end_date=end_date,
            primitive="recovering",
            params=TrajectoryParams(
                **{"start_health": 40, "end_health": 60, "slope_shape": "linear"}
            ),
            seed=seed,
            generated_at=None,
        )

    # ── NULL health score: stable at neutral fallback (D14) ─────────────────
    if overall_health is None:
        return TrajectoryEntry(
            id=entry_id,
            start_date=start_date,
            end_date=end_date,
            primitive="stable",
            params=TrajectoryParams(**{"target_band": [50, 70]}),
            seed=seed,
            generated_at=None,
        )

    h = overall_health

    # ── Divergence (D14): max - min of active dim scores ────────────────────
    divergence = (max(dim_scores) - min(dim_scores)) if len(dim_scores) >= 2 else 0

    # ── Health-band table (D14) ──────────────────────────────────────────────
    if h >= 75:
        # Healthy
        primitive = "stable"
        lo = _clamp(h - 5)
        hi = _clamp(h + 5)
        params: dict[str, Any] = {"target_band": [lo, hi]}

    elif 50 <= h <= 74:
        if divergence < 30:
            # Moderate-stable
            primitive = "stable"
            lo = _clamp(h - 7)
            hi = _clamp(h + 7)
            params = {"target_band": [lo, hi]}
        else:
            # Moderate-divergent
            primitive = "declining"
            params = {
                "start_health": _clamp(h + 15),
                "end_health": _clamp(h),
                "slope_shape": "linear",
            }

    elif 30 <= h <= 49:
        # At-risk
        primitive = "declining"
        params = {
            "start_health": _clamp(h + 25),
            "end_health": _clamp(h),
            "slope_shape": "linear",
        }

    else:
        # Severe: h < 30
        midpoint = start_date + timedelta(days=(end_date - start_date).days // 2)
        primitive = "cliff"
        lo_post = _clamp(h - 5)
        hi_post = _clamp(h + 5)
        params = {
            "cliff_date": midpoint.isoformat(),
            "pre_band": [55, 65],
            "post_band": [lo_post, hi_post],
        }

    return TrajectoryEntry(
        id=entry_id,
        start_date=start_date,
        end_date=end_date,
        primitive=primitive,
        params=TrajectoryParams(**params),
        seed=seed,
        generated_at=None,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def bootstrap_workspace(
    config: BootstrapConfig,
    client: Any,
) -> BootstrapResult:
    """Inspect current workspace state and write a proposed trajectory spec.

    Steps:
    1. Resolve output path; check for existing file (respect force flag).
    2. Load existing spec if present (for continuation start dates).
    3. Query accounts for the workspace (one query).
    4. Query latest active dimension scores for all accounts (one query).
    5. Apply the D14 heuristic for each non-deleted account.
    6. Merge proposed entries into the spec.
    7. Write to disk.
    8. Return BootstrapResult with counts.
    """
    out_path = config.out_path or spec_path_for_workspace(config.workspace_slug)

    # ── Resolve target dates (weeks back from today) ─────────────────────────
    today = date.today()
    default_start = today - timedelta(weeks=config.weeks)
    default_end = today - timedelta(days=1)

    # ── Load existing spec ───────────────────────────────────────────────────
    existing_spec: TrajectorySpec | None = None
    if out_path.exists():
        if not config.force:
            print(
                f"Error: {out_path} already exists.\n"
                f"Use --force to overwrite, or --out path/to/other.yaml to write elsewhere.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            existing_spec = load_spec(out_path)
        except Exception as exc:
            logger.warning("Could not load existing spec %s: %s — treating as empty", out_path, exc)
            existing_spec = None

    # ── Query 1: accounts for workspace ─────────────────────────────────────
    account_rows = _query_accounts(config.workspace_slug, client)

    # ── Query 2: latest active dimension scores for all accounts ────────────
    dim_scores_by_slug = _query_dimension_scores(config.workspace_slug, client)

    # ── Counters ─────────────────────────────────────────────────────────────
    proposed_count = 0
    skipped_deleted = 0
    skipped_no_health = 0
    skipped_covered = 0
    skipped_system = 0

    new_trajectories: dict[str, list[TrajectoryEntry]] = {}

    for row in account_rows:
        slug: str = row["slug"]
        status: str = row.get("status") or "active"
        deleted_at = row.get("deleted_at")
        overall_health: int | None = row.get("overall_health_score")

        # ── Skip soft-deleted (D14) ──────────────────────────────────────────
        if deleted_at is not None:
            logger.info("SKIP account=%s reason=soft_deleted", slug)
            skipped_deleted += 1
            continue

        # ── Skip system pseudo-accounts (_unmatched and any future _*-prefixed slugs) ──
        if slug.startswith("_"):
            logger.info("SKIP account=%s reason=system_slug", slug)
            skipped_system += 1
            continue

        # ── Resolve start / end dates with continuation logic (D13 input 3) ─
        existing_entries: list[TrajectoryEntry] = []
        if existing_spec and slug in existing_spec.trajectories:
            existing_entries = existing_spec.trajectories[slug]

        entry_start, entry_end = _resolve_dates(
            existing_entries, default_start, default_end, config.weeks
        )
        if entry_start is None:
            # Date range fully covered — skip.
            logger.info(
                "SKIP account=%s reason=date_range_fully_covered "
                "recommend='--revert-from or edit spec'",
                slug,
            )
            skipped_covered += 1
            continue

        # ── Track NULL health (warning, still proposes entry) ────────────────
        if overall_health is None:
            logger.warning(
                "WARN account=%s reason=no_health_score fallback=stable[50,70]", slug
            )
            skipped_no_health += 1

        # ── Apply D14 heuristic ───────────────────────────────────────────────
        dim_scores: list[int] = dim_scores_by_slug.get(slug, [])
        entry = _propose_entry(
            account_slug=slug,
            overall_health=overall_health,
            dim_scores=dim_scores,
            status=status,
            existing_entries=existing_entries,
            start_date=entry_start,
            end_date=entry_end,
            workspace_slug=config.workspace_slug,
        )

        if entry is None:
            skipped_covered += 1
            continue

        new_trajectories.setdefault(slug, []).append(entry)
        proposed_count += 1

    # ── Merge new entries into existing spec ─────────────────────────────────
    if existing_spec is not None:
        # Merge: add new entries to each account's list; preserve existing entries.
        merged: dict[str, list[TrajectoryEntry]] = dict(existing_spec.trajectories)
        for slug, entries in new_trajectories.items():
            merged.setdefault(slug, []).extend(entries)
        spec = TrajectorySpec(workspace_slug=config.workspace_slug, trajectories=merged)
    else:
        spec = TrajectorySpec(workspace_slug=config.workspace_slug, trajectories=new_trajectories)

    # ── Write to disk ────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_spec(spec, out_path)
    logger.info("Wrote %s (%d proposed entries)", out_path, proposed_count)

    return BootstrapResult(
        proposed_count=proposed_count,
        skipped_deleted=skipped_deleted,
        skipped_no_health=skipped_no_health,
        skipped_covered=skipped_covered,
        skipped_system=skipped_system,
    )


# ---------------------------------------------------------------------------
# DB helpers (client-based, not src.db imports — ADR-021 §D10 boundary)
# ---------------------------------------------------------------------------


def _query_accounts(workspace_slug: str, client: Any) -> list[dict]:
    """Return rows for all accounts in the workspace (including deleted).

    Columns: slug, status, overall_health_score, deleted_at.
    Soft-deleted accounts are included so the bootstrapper can count them in
    skipped_deleted — the filter happens in bootstrap_workspace().
    """
    try:
        ws_resp = (
            client.table("workspaces")
            .select("id")
            .eq("slug", workspace_slug)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error("Could not look up workspace %r: %s", workspace_slug, exc)
        return []

    ws_data = getattr(ws_resp, "data", None)
    if not ws_data:
        logger.error("Workspace not found: %r", workspace_slug)
        return []

    ws_id = ws_data.get("id")
    if not ws_id:
        logger.error("Workspace id missing for %r", workspace_slug)
        return []

    try:
        acc_resp = (
            client.table("accounts")
            .select("slug,status,overall_health_score,deleted_at")
            .eq("workspace_id", ws_id)
            .order("slug")
            .execute()
        )
    except Exception as exc:
        logger.error("Failed to load accounts for workspace %r: %s", workspace_slug, exc)
        return []

    return acc_resp.data or []


def _query_dimension_scores(workspace_slug: str, client: Any) -> dict[str, list[int]]:
    """Return a mapping of account_slug -> list[score] for non-superseded dim scores.

    Issues a single query joining account_dimension_scores to accounts and
    workspaces — NOT a per-account loop (avoids N+1).

    Returns an empty dict on any error (bootstrapper falls back to divergence=0).
    """
    try:
        resp = client.rpc(
            "get_bootstrap_dimension_scores",
            {"p_workspace_slug": workspace_slug},
        ).execute()
    except Exception:
        # Fall back to raw table query if the RPC doesn't exist yet.
        try:
            resp = (
                client.table("account_dimension_scores")
                .select(
                    "score,"
                    "accounts!inner(slug,"
                    "workspaces!inner(slug))"
                )
                .eq("accounts.workspaces.slug", workspace_slug)
                .is_("superseded_at", "null")
                .execute()
            )
        except Exception as inner_exc:
            logger.warning(
                "Failed to load dimension scores for workspace %r: %s — divergence=0 for all",
                workspace_slug,
                inner_exc,
            )
            return {}

    rows = getattr(resp, "data", None) or []

    # The RPC returns rows with {account_slug, score}; the fallback table query
    # returns nested objects.  Handle both shapes.
    result: dict[str, list[int]] = {}
    for row in rows:
        if "account_slug" in row:
            slug = row["account_slug"]
        elif "accounts" in row and isinstance(row["accounts"], dict):
            slug = row["accounts"]["slug"]
        else:
            continue
        score = row.get("score")
        if score is not None:
            result.setdefault(slug, []).append(int(score))

    return result


def _resolve_dates(
    existing_entries: list[TrajectoryEntry],
    default_start: date,
    default_end: date,
    weeks: int,
) -> tuple[date | None, date]:
    """Return (entry_start, entry_end) for a new entry given existing entries.

    Continuation logic (D13 input 3):
    - If existing entries are present, start the new entry the day after the
      latest existing end_date.
    - The end_date is always start + weeks*7 - 1 day (entry spans exactly `weeks` weeks).
    - If the continuation start is already past default_end (i.e., the proposed
      default range is fully covered), return (None, ...) to signal skip.

    Args:
        existing_entries: all existing entries for the account.
        default_start: today - weeks*7 (the default start when no existing entries).
        default_end: today - 1 day.
        weeks: number of weeks to cover in the new entry.

    Returns:
        (start, end) if a new entry should be proposed.
        (None, ...) if the proposed range is already covered and should be skipped.
    """
    if not existing_entries:
        return default_start, default_end

    latest_end = max(e.end_date for e in existing_entries)
    continuation_start = latest_end + timedelta(days=1)

    # The "proposed default range" is [default_start, default_end].
    # If the continuation start already covers the whole default range (i.e. all
    # days up to default_end already have entries), skip.
    if continuation_start > default_end:
        return None, default_end

    continuation_end = continuation_start + timedelta(weeks=weeks) - timedelta(days=1)
    return continuation_start, continuation_end


# ---------------------------------------------------------------------------
# Rich summary display
# ---------------------------------------------------------------------------


def print_bootstrap_summary(
    result: BootstrapResult,
    workspace_slug: str,
    out_path: Path,
    console: Console,
) -> None:
    """Print a Rich summary table after a successful bootstrap run."""
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column("Label", style="dim", no_wrap=True)
    t.add_column("Value", no_wrap=True)

    t.add_row("Proposed entries", str(result.proposed_count))
    t.add_row("Skipped (deleted)", str(result.skipped_deleted))
    t.add_row("Skipped (system slug)", str(result.skipped_system))
    t.add_row("Skipped (covered)", str(result.skipped_covered))
    t.add_row("Fallback (no health score)", str(result.skipped_no_health))

    console.print(
        Panel(
            t,
            title=f"[bold]Bootstrap complete: [cyan]{workspace_slug}[/cyan][/bold]",
            expand=False,
        )
    )
    console.print()
    console.print(f"Wrote: [bold]{out_path}[/bold]")
    console.print()
    console.print("Review the spec, adjust any params, then run:")
    run_cmd = f"uv run python scripts/simulate_history.py --workspace {workspace_slug} --dry-run"
    console.print(f"  [dim]{run_cmd}[/dim]")
    console.print()
