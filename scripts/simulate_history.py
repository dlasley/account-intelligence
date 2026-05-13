"""CLI entry point for the trajectory simulator.

Generates per-week historical narratives for the trajectory chart and daily briefing
inbox use cases.  Each (account, week) pair produces one narrative generated against
that week's signal slice — this is the simulator's single mode.

Current-snapshot narratives (one narrative per account at the current cumulative
window) are the production narrative scheduler's responsibility, not this script's.

Usage:
    # Execute all pending entries for a workspace
    uv run python scripts/simulate_history.py --workspace lattice-build

    # Dry-run (no LLM calls; prints per-week signal plans)
    uv run python scripts/simulate_history.py --workspace lattice-build --dry-run

    # Limit to 2 accounts and 3 weeks each
    uv run python scripts/simulate_history.py \\
        --workspace lattice-build \\
        --accounts crucible,phalanx-systems \\
        --weeks 3

    # Override narrative model
    uv run python scripts/simulate_history.py \\
        --workspace lattice-build \\
        --model claude-sonnet-4-6

    # Revert simulation data from a date and prune the spec
    uv run python scripts/simulate_history.py \\
        --workspace lattice-build \\
        --revert-from 2026-04-01

    # Audit every per-week narrative (not just the final week per entry)
    uv run python scripts/simulate_history.py \\
        --workspace lattice-build \\
        --audit-all

    # Point at a non-canonical spec path
    uv run python scripts/simulate_history.py \\
        --spec fixtures/synthetic-scenarios/trajectory.demo.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root if present.
load_dotenv()

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Trajectory simulator: generates per-week historical narratives for the "
            "trajectory chart and daily briefing inbox use cases. "
            "Current-snapshot narratives (single narrative per account at the current "
            "cumulative window) come from the production narrative scheduler, not this script."
        )
    )
    target = p.add_mutually_exclusive_group()
    target.add_argument(
        "--workspace",
        metavar="SLUG",
        default=None,
        help=(
            "Workspace slug — resolves to the canonical spec path "
            "fixtures/synthetic-scenarios/trajectory.<slug>.yaml"
        ),
    )
    target.add_argument(
        "--spec",
        metavar="PATH",
        default=None,
        help="Explicit path to a trajectory YAML spec (overrides --workspace).",
    )

    p.add_argument(
        "--accounts",
        metavar="SLUG[,SLUG,...]",
        default=None,
        help="Comma-separated list of account slugs to process (default: all).",
    )
    p.add_argument(
        "--weeks",
        type=int,
        default=None,
        metavar="N",
        help="Cap the number of weeks processed per pending entry.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Synthesise signals and compute health; skip all LLM calls and DB writes. "
            "Mutually exclusive with --revert-from."
        ),
    )
    p.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="Override the narrative generation model (e.g. claude-sonnet-4-6).",
    )
    p.add_argument(
        "--no-audit",
        action="store_true",
        default=False,
        help="Skip the audit harness entirely (even for the final week of each entry).",
    )
    p.add_argument(
        "--audit-all",
        action="store_true",
        default=False,
        help=(
            "Audit every per-week narrative, not just the final week per entry. "
            "Increases cost proportionally to week count."
        ),
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=1,
        metavar="N",
        help="Per-week retry count before skipping a failed entry (default: 1).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Suppress the existing-production-narratives warning.",
    )
    p.add_argument(
        "--revert-from",
        metavar="DATE",
        default=None,
        help=(
            "Delete signals, narratives, dimension scores, and health snapshots with "
            "timestamps on or after DATE (YYYY-MM-DD), remove the corresponding YAML "
            "entries, and exit.  Mutually exclusive with execution flags."
        ),
    )
    p.add_argument(
        "--author",
        action="store_true",
        default=False,
        help=(
            "Launch the interactive trajectory authoring TUI.  "
            "Prompts for date range, primitive, and params, then writes the spec "
            "and optionally kicks off the executor."
        ),
    )
    p.add_argument(
        "--bootstrap",
        action="store_true",
        default=False,
        help=(
            "Auto-generate a trajectory spec from current workspace state.  "
            "Inspects overall_health_score + dimension scores and proposes a "
            "primitive per account (ADR-021 §D13 / §D14).  Requires --workspace."
        ),
    )
    p.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help=(
            "Output path for --bootstrap (overrides canonical trajectory.<slug>.yaml path)."
        ),
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable INFO-level logging.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code (0 = success, 1 = error)."""
    _argv = argv or sys.argv[1:]
    log_level = logging.INFO if "--verbose" in _argv or "-v" in _argv else logging.WARNING
    logging.basicConfig(level=log_level, format="%(levelname)s %(name)s %(message)s")

    parser = _build_parser()
    args = parser.parse_args(argv)

    # Require either --workspace or --spec.
    if args.workspace is None and args.spec is None:
        parser.error("must specify --workspace SLUG or --spec PATH")

    # --revert-from is mutually exclusive with execution flags.
    if args.revert_from is not None:
        for flag in ("dry_run", "weeks", "model", "audit_all", "no_audit"):
            if getattr(args, flag, False):
                parser.error(f"--revert-from is mutually exclusive with --{flag.replace('_', '-')}")

    # Resolve spec path.
    workspace_slug: str  # always assigned in each branch; pyright needs the annotation
    if args.spec:
        spec_path = Path(args.spec)
        # Infer workspace_slug from spec if --workspace was not provided.
        if args.workspace is None:
            # convention: trajectory.<workspace_slug>.yaml
            stem = spec_path.stem  # e.g. "trajectory.lattice-build"
            if stem.startswith("trajectory."):
                workspace_slug = stem[len("trajectory."):]
            else:
                parser.error(
                    "--spec path does not follow the 'trajectory.<slug>.yaml' convention; "
                    "provide --workspace explicitly."
                )
                return 1  # unreachable — parser.error calls sys.exit; satisfies pyright
        else:
            workspace_slug = str(args.workspace)
    else:
        workspace_slug = str(args.workspace)
        from src.simulator.spec import spec_path_for_workspace

        spec_path = spec_path_for_workspace(workspace_slug)

    accounts_filter: list[str] | None = None
    if args.accounts:
        accounts_filter = [s.strip() for s in args.accounts.split(",") if s.strip()]

    # --bootstrap requires --workspace, not --spec.
    if getattr(args, "bootstrap", False) and args.workspace is None:
        parser.error("--bootstrap requires --workspace SLUG")

    # --bootstrap path — auto-generate spec from workspace state and exit.
    if getattr(args, "bootstrap", False):
        try:
            from rich.console import Console

            from src.db.client import get_client
            from src.simulator.bootstrap import (
                BootstrapConfig,
                bootstrap_workspace,
                print_bootstrap_summary,
            )

            out = Path(args.out) if getattr(args, "out", None) else None
            cfg = BootstrapConfig(
                workspace_slug=workspace_slug,
                weeks=args.weeks if args.weeks is not None else 4,
                out_path=out,
                force=args.force,
            )
            client = get_client()
            result = bootstrap_workspace(cfg, client)
            console = Console()
            resolved_out = out or __import__(
                "src.simulator.spec", fromlist=["spec_path_for_workspace"]
            ).spec_path_for_workspace(workspace_slug)
            print_bootstrap_summary(result, workspace_slug, resolved_out, console)
            return 0
        except SystemExit:
            raise
        except Exception as exc:
            logger.error("Bootstrap failed: %s", exc, exc_info=True)
            return 1

    # --author path — launch the interactive TUI and exit.
    if args.author:
        try:
            from rich.console import Console

            from src.db.client import get_client
            from src.simulator.author import run_author_tui

            console = Console()
            client = get_client()
            try:
                return run_author_tui(workspace_slug, spec_path, console, client)
            except KeyboardInterrupt:
                console.print()
                console.print("[dim]Aborted, no changes written.[/dim]")
                return 0
        except Exception as exc:
            logger.error("Author TUI error: %s", exc, exc_info=True)
            return 1

    # --revert-from path — delete + prune and exit.
    if args.revert_from is not None:
        try:
            revert_date = date.fromisoformat(args.revert_from)
        except ValueError:
            parser.error(f"--revert-from must be YYYY-MM-DD, got: {args.revert_from!r}")
            return 1  # unreachable — satisfies pyright

        from src.db.client import get_client
        from src.simulator.executor import revert_from

        client = get_client()
        revert_from(workspace_slug, spec_path, revert_date, client)
        return 0

    # Normal execution path.
    from src.db.client import get_client
    from src.simulator.executor import ExecutorConfig, run

    client = get_client()

    config = ExecutorConfig(
        workspace_slug=workspace_slug,
        spec_path=spec_path,
        accounts_filter=accounts_filter,
        weeks_limit=args.weeks,
        dry_run=args.dry_run,
        model=args.model,
        audit_final_week=not args.no_audit,
        audit_all_weeks=args.audit_all,
        max_retries=args.max_retries,
        force=args.force,
    )

    try:
        result = run(config, client)
    except SystemExit:
        raise
    except Exception as exc:
        logger.error("Executor failed: %s", exc, exc_info=True)
        return 1

    # Final summary line.
    status = "dry-run" if args.dry_run else "complete"
    print(
        f"Simulator {status}: workspace={workspace_slug} "
        f"entries_processed={result.entries_processed} "
        f"entries_failed={result.entries_failed} "
        f"weeks={result.weeks_total} "
        f"narratives={result.narratives_generated} "
        f"audit_passed={result.audit_passed} "
        f"audit_failed={result.audit_failed} "
        f"cost_usd={result.cost_usd:.4f} "
        f"spec={spec_path}"
    )

    return 1 if result.entries_failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
