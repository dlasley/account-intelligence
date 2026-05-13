"""Interactive TUI for authoring trajectory specs.

Provides a ``rich``-based interface for selecting trajectory shapes and
writing them to a per-workspace YAML spec file.  The flow is:

  1. Load (or create) the workspace's trajectory spec.
  2. Display a per-account overview: current health score, existing entries.
  3. Prompt for scope (all accounts / subset), date range, primitive, and params.
  4. Optionally walk through per-account overrides.
  5. Show a per-account preview table with color-coded target health values.
  6. Print an estimated LLM cost and ask to confirm.
  7. Write the spec and either invoke the executor or exit cleanly.

See ADR-021 §D6, §D8, §Phase 5 for design intent and polish requirements.

Entry point: ``run_author_tui(workspace_slug, spec_path, console, client)``
             called from ``scripts/simulate_history.py --author``.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

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


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Model cost estimates (per narrative call and per audit call)
# Rough figures: Opus 4 narrative ~$0.05, GPT-5-mini audit ~$0.005
_NARRATIVE_COST_PER_CALL: float = 0.05
_AUDIT_COST_PER_CALL: float = 0.005

# Health value color thresholds (match ADR-021 §D8 and prompt text)
# green ≥75, yellow 50-74, orange 30-49, red <30
_GREEN_THRESHOLD: int = 75
_YELLOW_THRESHOLD: int = 50
_ORANGE_THRESHOLD: int = 30

PRIMITIVE_CHOICES = ["stable", "declining", "recovering", "oscillating", "cliff"]
SLOPE_SHAPE_CHOICES = ["linear", "exponential", "cliff_at_week_N", "jump_at_week_N"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class WorkspaceAccountInfo:
    """Per-account display data fetched from the DB at TUI startup."""

    slug: str
    name: str
    overall_health_score: int | None
    entry_count: int
    latest_end_date: date | None


@dataclass
class AccountOverride:
    """Per-account customization applied when the user chooses the per-account flow."""

    slug: str
    primitive: str
    params: dict
    seed: int
    start_date: date
    end_date: date


@dataclass
class AuthorSession:
    """Accumulated state across a single TUI authoring session."""

    workspace_slug: str
    spec: TrajectorySpec | None
    accounts: list[WorkspaceAccountInfo]
    selected_slugs: list[str]
    start_date: date
    end_date: date
    global_primitive: str
    global_params: dict
    global_seed: int
    overrides: dict[str, AccountOverride] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Health styling
# ---------------------------------------------------------------------------


def health_style(score: int | None) -> str:
    """Return a Rich style string for a health score.

    Thresholds: green ≥75, yellow 50-74, orange 30-49, red <30.
    None (no score yet) renders as dim.
    """
    if score is None:
        return "dim"
    if score >= _GREEN_THRESHOLD:
        return "bold green"
    if score >= _YELLOW_THRESHOLD:
        return "yellow"
    if score >= _ORANGE_THRESHOLD:
        return "dark_orange"
    return "bold red"


def _health_text(score: int | None) -> Text:
    """Return a styled Rich Text object for a health score."""
    if score is None:
        return Text("—", style="dim")
    return Text(str(score), style=health_style(score))


# ---------------------------------------------------------------------------
# Seed derivation
# ---------------------------------------------------------------------------


def derive_seed(workspace_slug: str, account_slug: str, start_date: date) -> int:
    """Derive a deterministic seed for an entry.

    Follows ADR-021 §D14::

        abs(hash(f"{workspace_slug}:{account_slug}:{start_date.isoformat()}")) % 9_999_999

    The start_date component is load-bearing — two entries for the same account at
    different start dates must produce different seeds to avoid sharing signal-axis
    draws (concern_topic, email_tone, contact-name picks).  See ADR-021 §Consequences.
    """
    return abs(hash(f"{workspace_slug}:{account_slug}:{start_date.isoformat()}")) % 9_999_999


# ---------------------------------------------------------------------------
# Cost estimate
# ---------------------------------------------------------------------------


def estimate_cost(n_accounts: int, n_weeks: int) -> tuple[float, float]:
    """Return (narrative_cost, audit_cost) for the proposed batch.

    Audit covers the final week of each account only (ADR-021 §O4).
    """
    narrative_cost = n_accounts * n_weeks * _NARRATIVE_COST_PER_CALL
    audit_cost = n_accounts * _AUDIT_COST_PER_CALL
    return narrative_cost, audit_cost


# ---------------------------------------------------------------------------
# Preview tables
# ---------------------------------------------------------------------------


def build_preview_table(
    curve: list[tuple[date, int]],
    account_slug: str,
    primitive: str,
) -> Table:
    """Return a Rich Table showing one account's week-by-week health curve.

    Args:
        curve:  list of ``(week_start, target_health)`` from ``primitive_to_curve``.
        account_slug: used in the table title.
        primitive: displayed in the Primitive column.
    """
    t = Table(
        title=f"[bold]{account_slug}[/bold]",
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 1),
    )
    t.add_column("Week", style="dim", justify="right", no_wrap=True)
    t.add_column("Start Date", no_wrap=True)
    t.add_column("Primitive", style="cyan", no_wrap=True)
    t.add_column("Target Health", justify="right", no_wrap=True)

    for i, (ws, th) in enumerate(curve):
        t.add_row(
            str(i + 1),
            ws.isoformat(),
            primitive if i == 0 else "",
            _health_text(th),
        )
    return t


def build_account_overview_table(
    accounts: list[WorkspaceAccountInfo],
    spec: TrajectorySpec | None,
) -> Table:
    """Return a Rich Table summarising workspace accounts.

    Columns: #, Slug, Name, Current Health, Entries, Latest End.
    """
    t = Table(
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 1),
    )
    t.add_column("#", style="dim", justify="right", no_wrap=True)
    t.add_column("Account", no_wrap=True)
    t.add_column("Name", no_wrap=True)
    t.add_column("Health", justify="right", no_wrap=True)
    t.add_column("Entries", justify="right", style="dim", no_wrap=True)
    t.add_column("Latest End", style="dim", no_wrap=True)

    for i, acc in enumerate(accounts, 1):
        entries = spec.trajectories.get(acc.slug, []) if spec else []
        n_entries = len(entries)
        latest_end: date | None = None
        if entries:
            latest_end = max(e.end_date for e in entries)

        t.add_row(
            str(i),
            acc.slug,
            acc.name,
            _health_text(acc.overall_health_score),
            str(n_entries),
            latest_end.isoformat() if latest_end else "—",
        )
    return t


def build_multi_account_preview_table(
    account_curves: list[tuple[str, list[tuple[date, int]], str]],
    current_health: Mapping[str, int | None],
) -> Table:
    """Return a Rich Table with one row per account showing per-week targets.

    Args:
        account_curves: list of ``(account_slug, curve, primitive)`` tuples.
        current_health: mapping of account_slug to current overall_health_score.

    The table contains: Account | Current | Primitive | Week 1 | Week 2 | ...
    """
    if not account_curves:
        t = Table()
        t.add_column("(no accounts)")
        return t

    max_weeks = max(len(curve) for _, curve, _ in account_curves)

    t = Table(
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 1),
    )
    t.add_column("Account", no_wrap=True)
    t.add_column("Current", justify="right", no_wrap=True)
    t.add_column("Primitive", style="cyan", no_wrap=True)
    for w in range(1, max_weeks + 1):
        t.add_column(f"Wk {w}", justify="right", no_wrap=True)

    for slug, curve, primitive in account_curves:
        curr = current_health.get(slug)
        row_cells: list[Any] = [
            slug,
            _health_text(curr),
            primitive,
        ]
        for w in range(max_weeks):
            if w < len(curve):
                row_cells.append(_health_text(curve[w][1]))
            else:
                row_cells.append(Text("", style="dim"))
        t.add_row(*row_cells)

    return t


# ---------------------------------------------------------------------------
# Primitive param prompts
# ---------------------------------------------------------------------------


def _prompt_params_for_primitive(
    console: Console,
    primitive: str,
    defaults: dict | None = None,
) -> dict:
    """Prompt the user for the required params for the chosen primitive.

    Returns a plain dict matching the primitive's required keys.
    """
    d = defaults or {}

    if primitive == "stable":
        console.print("  [dim]target_band: two-item list [low, high][/dim]")
        band = d.get("target_band", [60, 80])
        low_def = str(d.get("low", band[0] if "target_band" in d else 60))
        high_def = str(d.get("high", band[1] if "target_band" in d else 80))
        low = int(Prompt.ask("    target_band low", default=low_def, console=console))
        high = int(Prompt.ask("    target_band high", default=high_def, console=console))
        return {"target_band": [_clamp(low), _clamp(high)]}

    if primitive in ("declining", "recovering"):
        start_def = str(d.get("start_health", 80 if primitive == "declining" else 35))
        end_def = str(d.get("end_health", 45 if primitive == "declining" else 70))
        slope_def = str(d.get("slope_shape", "linear"))
        slope_choices = "linear, exponential, cliff_at_week_N, jump_at_week_N"
        console.print(f"  [dim]slope_shape choices: {slope_choices}[/dim]")
        start_health = int(Prompt.ask("    start_health", default=start_def, console=console))
        end_health = int(Prompt.ask("    end_health", default=end_def, console=console))
        slope_shape = Prompt.ask("    slope_shape", default=slope_def, console=console)
        # Validate direction
        if primitive == "declining" and start_health < end_health:
            console.print(
                "[dark_orange]  Warning: declining expects start_health > end_health. "
                "Swapping.[/dark_orange]"
            )
            start_health, end_health = end_health, start_health
        if primitive == "recovering" and start_health > end_health:
            console.print(
                "[dark_orange]  Warning: recovering expects start_health < end_health. "
                "Swapping.[/dark_orange]"
            )
            start_health, end_health = end_health, start_health
        return {
            "start_health": _clamp(start_health),
            "end_health": _clamp(end_health),
            "slope_shape": slope_shape,
        }

    if primitive == "oscillating":
        low_def = str(d.get("low", 40))
        high_def = str(d.get("high", 70))
        period_def = str(d.get("period_weeks", 4))
        low = int(Prompt.ask("    low", default=low_def, console=console))
        high = int(Prompt.ask("    high", default=high_def, console=console))
        period_weeks = int(Prompt.ask("    period_weeks", default=period_def, console=console))
        return {
            "low": _clamp(low),
            "high": _clamp(high),
            "period_weeks": max(1, period_weeks),
        }

    if primitive == "cliff":
        console.print("  [dim]cliff_date: date when the account drops (YYYY-MM-DD)[/dim]")
        cliff_date_def = str(d.get("cliff_date", ""))
        pre_lo_def = str(d.get("pre_lo", 65))
        pre_hi_def = str(d.get("pre_hi", 80))
        post_lo_def = str(d.get("post_lo", 20))
        post_hi_def = str(d.get("post_hi", 35))
        cliff_date_str = Prompt.ask("    cliff_date", default=cliff_date_def, console=console)
        pre_lo = int(Prompt.ask("    pre_band low", default=pre_lo_def, console=console))
        pre_hi = int(Prompt.ask("    pre_band high", default=pre_hi_def, console=console))
        post_lo = int(Prompt.ask("    post_band low", default=post_lo_def, console=console))
        post_hi = int(Prompt.ask("    post_band high", default=post_hi_def, console=console))
        return {
            "cliff_date": cliff_date_str,
            "pre_band": [_clamp(pre_lo), _clamp(pre_hi)],
            "post_band": [_clamp(post_lo), _clamp(post_hi)],
        }

    raise ValueError(f"Unknown primitive: {primitive!r}")


def _clamp(v: int, lo: int = 1, hi: int = 100) -> int:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# DB workspace inspection
# ---------------------------------------------------------------------------


def load_workspace_accounts(
    workspace_slug: str,
    client: Any,
) -> tuple[list[WorkspaceAccountInfo], str | None]:
    """Return (accounts, error_message) for the workspace.

    Queries: workspace by slug → accounts with overall_health_score.
    Returns ([], error_str) when workspace does not exist.
    Does NOT import src.db directly — uses the client object (consistent with
    the executor boundary from ADR-021 §D10).
    """
    try:
        ws_resp = (
            client.table("workspaces")
            .select("id,slug")
            .eq("slug", workspace_slug)
            .single()
            .execute()
        )
    except Exception as exc:
        return [], f"Could not connect to database: {exc}"

    ws_data = getattr(ws_resp, "data", None)
    if not ws_data:
        return [], f"Workspace not found: {workspace_slug!r}"

    ws_id = ws_data.get("id")
    if not ws_id:
        return [], f"Workspace not found: {workspace_slug!r}"

    try:
        acc_resp = (
            client.table("accounts")
            .select("slug,name,overall_health_score")
            .eq("workspace_id", ws_id)
            .is_("deleted_at", "null")
            .order("slug")
            .execute()
        )
    except Exception as exc:
        return [], f"Failed to load accounts: {exc}"

    rows = acc_resp.data or []
    accounts = [
        WorkspaceAccountInfo(
            slug=row["slug"],
            name=row.get("name") or row["slug"],
            overall_health_score=row.get("overall_health_score"),
            entry_count=0,   # populated from spec below
            latest_end_date=None,
        )
        for row in rows
    ]
    return accounts, None


def _next_available_start(spec: TrajectorySpec | None) -> date:
    """Return the day after the latest end_date across all entries in the spec.

    Falls back to today if the spec is None or has no entries.
    """
    if spec is None:
        return date.today()
    all_ends: list[date] = [
        e.end_date
        for entries in spec.trajectories.values()
        for e in entries
    ]
    if not all_ends:
        return date.today()
    return max(all_ends) + timedelta(days=1)


# ---------------------------------------------------------------------------
# Interactive TUI
# ---------------------------------------------------------------------------


def run_author_tui(
    workspace_slug: str,
    spec_path: Path,
    console: Console,
    client: Any,
) -> int:
    """Run the interactive trajectory authoring TUI.

    Returns 0 on clean exit (wrote spec or canceled), 1 on error.
    The caller is responsible for catching KeyboardInterrupt and printing a
    cancellation message.
    """
    # ── Banner ──────────────────────────────────────────────────────────────
    console.print()
    console.rule(
        f"[bold]trajectory authoring — [cyan]{workspace_slug}[/cyan][/bold]",
        style="bold",
    )
    console.print()

    # ── Load spec (or create skeleton) ──────────────────────────────────────
    spec: TrajectorySpec | None = None
    if spec_path.exists():
        try:
            spec = load_spec(spec_path)
            n_entries = sum(len(v) for v in spec.trajectories.values())
            n_pending = sum(
                1
                for v in spec.trajectories.values()
                for e in v
                if e.generated_at is None
            )
            console.print(
                f"[dim]Loaded[/dim] [bold]{spec_path}[/bold]  "
                f"[dim]({n_entries} entries, {n_pending} pending)[/dim]"
            )
        except Exception as exc:
            console.print(f"[bold red]Error loading spec:[/bold red] {exc}")
            return 1
    else:
        console.print(f"[dim]No existing spec — will create[/dim] [bold]{spec_path}[/bold]")

    # ── Load workspace accounts ──────────────────────────────────────────────
    console.print()
    with console.status("[dim]Loading workspace accounts…[/dim]"):
        accounts, err = load_workspace_accounts(workspace_slug, client)

    if err:
        console.print(f"[bold red]Error:[/bold red] {err}")
        return 1

    if not accounts:
        console.print(f"[bold red]No accounts found[/bold red] for workspace {workspace_slug!r}.")
        return 1

    # Annotate entry counts from spec
    for acc in accounts:
        if spec and acc.slug in spec.trajectories:
            entries = spec.trajectories[acc.slug]
            acc.entry_count = len(entries)
            if entries:
                acc.latest_end_date = max(e.end_date for e in entries)

    # ── Account overview ─────────────────────────────────────────────────────
    next_start = _next_available_start(spec)
    console.print(
        Panel(
            build_account_overview_table(accounts, spec),
            title=f"[bold]{workspace_slug}[/bold] — {len(accounts)} accounts",
            subtitle=f"[dim]Next available start: {next_start.isoformat()}[/dim]",
            expand=False,
        )
    )
    console.print()

    # ── Scope: all / subset / quit ───────────────────────────────────────────
    scope = Prompt.ask(
        "Apply trajectory to",
        choices=["all", "subset", "quit"],
        default="all",
        console=console,
    )
    if scope == "quit":
        console.print("[dim]Aborted, no changes written.[/dim]")
        return 0

    selected_slugs: list[str]
    if scope == "all":
        selected_slugs = [acc.slug for acc in accounts]
    else:
        slug_map = {str(i + 1): acc.slug for i, acc in enumerate(accounts)}
        console.print(
            "[dim]Enter comma-separated account numbers or slugs (e.g. 1,3,crucible):[/dim]"
        )
        raw = Prompt.ask("Accounts", console=console)
        selected_slugs = []
        for token in raw.split(","):
            t = token.strip()
            if t in slug_map:
                selected_slugs.append(slug_map[t])
            elif any(acc.slug == t for acc in accounts):
                selected_slugs.append(t)
            else:
                console.print(f"  [dark_orange]Unknown account {t!r} — skipped[/dark_orange]")
        if not selected_slugs:
            console.print("[bold red]No valid accounts selected. Aborted.[/bold red]")
            return 1

    console.print(
        f"[dim]Selected:[/dim] {', '.join(selected_slugs)}"
    )
    console.print()

    # ── Date range ───────────────────────────────────────────────────────────
    default_end = next_start + timedelta(weeks=4) - timedelta(days=1)
    start_date_str = Prompt.ask(
        "Start date",
        default=next_start.isoformat(),
        console=console,
    )
    end_date_str = Prompt.ask(
        "End date",
        default=default_end.isoformat(),
        console=console,
    )
    try:
        start_date = date.fromisoformat(start_date_str)
        end_date = date.fromisoformat(end_date_str)
    except ValueError as exc:
        console.print(f"[bold red]Invalid date:[/bold red] {exc}")
        return 1

    if end_date <= start_date:
        console.print("[bold red]end_date must be after start_date.[/bold red]")
        return 1

    span_days = (end_date - start_date).days
    n_weeks = max(1, span_days // 7 + (1 if span_days % 7 else 0))
    console.print(
        f"[dim]Date range:[/dim] {start_date.isoformat()} → {end_date.isoformat()} "
        f"({n_weeks} weeks)"
    )
    console.print()

    # ── Collision check for selected accounts ────────────────────────────────
    if spec:
        colliding: list[str] = []
        for slug in selected_slugs:
            existing = spec.trajectories.get(slug, [])
            verdict = check_collision(existing, start_date, end_date)
            if verdict.collides:
                rec = verdict.recommend_start.isoformat() if verdict.recommend_start else "N/A"
                colliding.append(
                    f"  [yellow]{slug}[/yellow] — recommended start: [bold]{rec}[/bold]"
                )
        if colliding:
            console.print("[dark_orange]Date range overlaps existing entries for:[/dark_orange]")
            for msg in colliding:
                console.print(msg)
            proceed = Confirm.ask(
                "Continue anyway? (entries will be appended; dedup handles re-runs)",
                default=False,
                console=console,
            )
            if not proceed:
                console.print("[dim]Aborted, no changes written.[/dim]")
                return 0
            console.print()

    # ── Primitive selection ──────────────────────────────────────────────────
    console.print("[bold]Primitive:[/bold]")
    for i, p in enumerate(PRIMITIVE_CHOICES, 1):
        console.print(f"  [dim]{i}.[/dim] {p}")
    console.print()
    prim_raw = Prompt.ask(
        "Primitive",
        choices=PRIMITIVE_CHOICES + [str(i) for i in range(1, len(PRIMITIVE_CHOICES) + 1)],
        default="declining",
        console=console,
    )
    # Accept numeric shorthand
    if prim_raw.isdigit():
        idx = int(prim_raw) - 1
        if 0 <= idx < len(PRIMITIVE_CHOICES):
            global_primitive = PRIMITIVE_CHOICES[idx]
        else:
            console.print("[bold red]Invalid primitive choice.[/bold red]")
            return 1
    else:
        global_primitive = prim_raw

    # ── Global params ────────────────────────────────────────────────────────
    console.print()
    console.print(f"[bold]Params for [cyan]{global_primitive}[/cyan]:[/bold]")
    try:
        global_params = _prompt_params_for_primitive(console, global_primitive)
    except (ValueError, TypeError) as exc:
        console.print(f"[bold red]Invalid params:[/bold red] {exc}")
        return 1

    console.print()

    # Seeds are derived deterministically from (workspace, account, start_date) per
    # ADR-021 §D14. Not surfaced in the TUI — under the derivation contract, the
    # author has no actionable use of the value during authoring (no fat-finger risk
    # to prevent because no manual entry exists, no collision to validate because
    # uniqueness is guaranteed by construction). The seed field in the written YAML
    # remains the inspection / override surface for anyone who needs it.

    # ── Per-account customization ────────────────────────────────────────────
    overrides: dict[str, AccountOverride] = {}
    customize = Confirm.ask(
        "Per-account customization?",
        default=False,
        console=console,
    )
    if customize:
        console.print()
        for slug in selected_slugs:
            acc_info = next((a for a in accounts if a.slug == slug), None)
            health_str = (
                f"[{health_style(acc_info.overall_health_score)}]{acc_info.overall_health_score}[/{health_style(acc_info.overall_health_score)}]"
                if acc_info and acc_info.overall_health_score is not None
                else "[dim]—[/dim]"
            )
            console.print(
                f"  [bold]{slug}[/bold]  current health: {health_str}  "
                f"primitive: [cyan]{global_primitive}[/cyan]"
            )
            do_override = Confirm.ask(
                f"  Override for {slug}?",
                default=False,
                console=console,
            )
            if do_override:
                prim_raw2 = Prompt.ask(
                    "    Primitive",
                    choices=PRIMITIVE_CHOICES,
                    default=global_primitive,
                    console=console,
                )
                acc_params = _prompt_params_for_primitive(console, prim_raw2, global_params)
                acc_seed = derive_seed(workspace_slug, slug, start_date)
                acc_start_str = Prompt.ask(
                    "    Start date",
                    default=start_date.isoformat(),
                    console=console,
                )
                acc_end_str = Prompt.ask(
                    "    End date",
                    default=end_date.isoformat(),
                    console=console,
                )
                try:
                    acc_start = date.fromisoformat(acc_start_str)
                    acc_end = date.fromisoformat(acc_end_str)
                except ValueError as exc:
                    console.print(
                        f"    [bold red]Invalid date: {exc}[/bold red]  Using global range."
                    )
                    acc_start, acc_end = start_date, end_date

                overrides[slug] = AccountOverride(
                    slug=slug,
                    primitive=prim_raw2,
                    params=acc_params,
                    seed=acc_seed,
                    start_date=acc_start,
                    end_date=acc_end,
                )
            console.print()

    # ── Build preview curves ─────────────────────────────────────────────────
    from src.simulator.primitives import primitive_to_curve

    account_curves: list[tuple[str, list[tuple[date, int]], str]] = []
    for slug in selected_slugs:
        if slug in overrides:
            ov = overrides[slug]
            curve = primitive_to_curve(ov.primitive, ov.params, ov.start_date, ov.end_date, ov.seed)
            account_curves.append((slug, curve, ov.primitive))
        else:
            seed = derive_seed(workspace_slug, slug, start_date)
            curve = primitive_to_curve(global_primitive, global_params, start_date, end_date, seed)
            account_curves.append((slug, curve, global_primitive))

    current_health = {acc.slug: acc.overall_health_score for acc in accounts}

    console.print()
    console.print("[bold]Preview:[/bold]")
    console.print()
    console.print(
        Panel(
            build_multi_account_preview_table(account_curves, current_health),
            title="Account-by-account health curve",
            expand=False,
        )
    )
    console.print()

    # ── Cost estimate ────────────────────────────────────────────────────────
    max_weeks = max(len(curve) for _, curve, _ in account_curves) if account_curves else n_weeks
    narr_cost, audit_cost = estimate_cost(len(selected_slugs), max_weeks)
    total_cost = narr_cost + audit_cost
    console.print(
        f"[dim]Estimated cost:[/dim] "
        f"[green]${narr_cost:.2f}[/green] narrative + "
        f"[dim]${audit_cost:.2f}[/dim] audit = "
        f"[bold]${total_cost:.2f}[/bold]"
    )
    console.print()

    # ── Final confirmation ───────────────────────────────────────────────────
    action = Prompt.ask(
        "Action",
        choices=["generate", "save", "cancel"],
        default="save",
        console=console,
    )
    if action == "cancel":
        console.print("[dim]Aborted, no changes written.[/dim]")
        return 0

    # ── Assemble new entries ─────────────────────────────────────────────────
    new_entries: list[tuple[str, TrajectoryEntry]] = []
    for slug in selected_slugs:
        if slug in overrides:
            ov = overrides[slug]
            entry_start = ov.start_date
            entry_end = ov.end_date
            entry_prim = ov.primitive
            entry_params = ov.params
            entry_seed = ov.seed
        else:
            entry_start = start_date
            entry_end = end_date
            entry_prim = global_primitive
            entry_params = global_params
            entry_seed = derive_seed(workspace_slug, slug, start_date)

        entry = TrajectoryEntry(
            id=generate_entry_id(),
            start_date=entry_start,
            end_date=entry_end,
            primitive=entry_prim,
            params=TrajectoryParams(**entry_params),
            seed=entry_seed,
            generated_at=None,
        )
        new_entries.append((slug, entry))

    # ── Merge into spec and save ─────────────────────────────────────────────
    if spec is None:
        trajectories: dict[str, list[TrajectoryEntry]] = {}
        for slug, entry in new_entries:
            trajectories.setdefault(slug, []).append(entry)
        spec = TrajectorySpec(workspace_slug=workspace_slug, trajectories=trajectories)
    else:
        for slug, entry in new_entries:
            spec.trajectories.setdefault(slug, []).append(entry)

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        save_spec(spec, spec_path)
    except Exception as exc:
        console.print(f"[bold red]Failed to write spec:[/bold red] {exc}")
        return 1

    n_added = len(new_entries)
    run_cmd = f"uv run python scripts/simulate_history.py --spec {spec_path}"

    if action == "generate":
        console.print(
            f"[bold green]✓[/bold green] Wrote [bold]{spec_path}[/bold] "
            f"({n_added} entr{'y' if n_added == 1 else 'ies'}, {len(selected_slugs)} accounts, "
            f"{max_weeks} weeks).  Running executor…"
        )
        console.print()
        # Import executor lazily and run
        try:
            from src.simulator.executor import ExecutorConfig
            from src.simulator.executor import run as executor_run

            exec_config = ExecutorConfig(
                workspace_slug=workspace_slug,
                spec_path=spec_path,
            )
            executor_run(exec_config, client)
        except Exception as exc:
            console.print(f"[bold red]Executor error:[/bold red] {exc}")
            return 1
    else:
        console.print(
            f"[bold green]✓[/bold green] Wrote [bold]{spec_path}[/bold] "
            f"({n_added} entr{'y' if n_added == 1 else 'ies'}, {len(selected_slugs)} accounts, "
            f"{max_weeks} weeks).  Run:"
        )
        console.print(f"  [dim]{run_cmd}[/dim]")

    console.print()
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Standalone entry point: ``python -m src.simulator.author WORKSPACE_SLUG``.

    Also called by ``scripts/simulate_history.py --author``.
    """
    import argparse

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Interactive TUI for authoring trajectory specs.",
    )
    parser.add_argument(
        "workspace_slug",
        nargs="?",
        default=None,
        help="Workspace slug (e.g. lattice-build).",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        dest="workspace_flag",
        help="Workspace slug (alternative to positional arg).",
    )
    parser.add_argument(
        "--spec",
        default=None,
        metavar="PATH",
        help="Explicit spec path (overrides canonical path).",
    )
    parsed = parser.parse_args(argv)

    slug = parsed.workspace_slug or parsed.workspace_flag
    if not slug:
        parser.error("workspace slug is required (positional or --workspace)")
        return 1

    spec_path = Path(parsed.spec) if parsed.spec else spec_path_for_workspace(slug)
    console = Console(stderr=False)

    from src.db.client import get_client

    client = get_client()

    try:
        return run_author_tui(slug, spec_path, console, client)
    except KeyboardInterrupt:
        console.print()
        console.print("[dim]Aborted, no changes written.[/dim]")
        return 0
    except Exception as exc:
        console.print(f"[bold red]Unexpected error:[/bold red] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
