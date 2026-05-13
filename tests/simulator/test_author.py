"""Tests for src/simulator/author.py.

Strategy: test the non-interactive (pure-function) parts of the TUI
directly, and use Console(file=StringIO()) for anything that prints.
The interactive Prompt.ask / Confirm.ask flow is exercised via the
run_author_tui() function with a mock client and monkeypatched prompts.

Tests avoid hitting a real database — the Supabase client is always mocked.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from rich.console import Console

from src.simulator.author import (
    WorkspaceAccountInfo,
    build_account_overview_table,
    build_multi_account_preview_table,
    build_preview_table,
    derive_seed,
    estimate_cost,
    health_style,
    load_workspace_accounts,
    run_author_tui,
)
from src.simulator.spec import (
    TrajectoryEntry,
    TrajectoryParams,
    TrajectorySpec,
    generate_entry_id,
    load_spec,
    save_spec,
    spec_path_for_workspace,
)

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


def _make_account(
    slug: str,
    name: str = "",
    health: int | None = 70,
) -> WorkspaceAccountInfo:
    return WorkspaceAccountInfo(
        slug=slug,
        name=name or slug.replace("-", " ").title(),
        overall_health_score=health,
        entry_count=0,
        latest_end_date=None,
    )


def _make_console() -> tuple[Console, io.StringIO]:
    """Return (console, buffer) so tests can read rendered output."""
    buf = io.StringIO()
    console = Console(file=buf, no_color=True, highlight=False, width=120)
    return console, buf


def _make_client_mock(accounts: list[dict] | None = None) -> MagicMock:
    """Return a mock Supabase client that returns the given account rows."""
    client = MagicMock()

    # Workspace lookup
    ws_resp = MagicMock()
    ws_resp.data = {"id": "ws-uuid-001", "slug": "test-workspace"}
    (
        client.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value
    ) = ws_resp

    # Account lookup
    acc_resp = MagicMock()
    acc_resp.data = accounts or [
        {"slug": "crucible", "name": "Crucible", "overall_health_score": 57},
        {"slug": "phalanx-systems", "name": "Phalanx Systems", "overall_health_score": 80},
    ]
    (
        client.table.return_value.select.return_value.eq.return_value.is_.return_value.order.return_value.execute.return_value
    ) = acc_resp

    return client


# ---------------------------------------------------------------------------
# 1. build_preview_table — shape and row count
# ---------------------------------------------------------------------------


def test_build_preview_table_row_count() -> None:
    """build_preview_table returns a table with one row per week tuple."""
    curve = [
        (date(2026, 4, 1), 84),
        (date(2026, 4, 8), 75),
        (date(2026, 4, 15), 66),
        (date(2026, 4, 22), 57),
    ]
    table = build_preview_table(curve, "crucible", "declining")
    # Rich Table row count is exposed via _rows
    assert len(table.rows) == 4


def test_build_preview_table_empty_curve() -> None:
    """An empty curve produces a table with zero rows (edge case)."""
    table = build_preview_table([], "crucible", "stable")
    assert len(table.rows) == 0


def test_build_preview_table_renders_without_error() -> None:
    """build_preview_table renders to a Console without raising."""
    console, buf = _make_console()
    curve = [(date(2026, 4, 1) + timedelta(weeks=i), 80 - i * 5) for i in range(4)]
    table = build_preview_table(curve, "test-account", "declining")
    console.print(table)  # must not raise
    output = buf.getvalue()
    assert "test-account" in output
    assert "2026-04-01" in output


# ---------------------------------------------------------------------------
# 2. Color mapping — health_style
# ---------------------------------------------------------------------------


def test_health_style_green_at_75() -> None:
    """Score 75 and above maps to green style."""
    style = health_style(75)
    assert "green" in style


def test_health_style_green_at_100() -> None:
    style = health_style(100)
    assert "green" in style


def test_health_style_yellow_50_to_74() -> None:
    """Scores 50-74 map to yellow."""
    assert health_style(50) == "yellow"
    assert health_style(65) == "yellow"
    assert health_style(74) == "yellow"


def test_health_style_orange_30_to_49() -> None:
    """Scores 30-49 map to orange."""
    style = health_style(30)
    assert "orange" in style.lower()
    style_mid = health_style(40)
    assert "orange" in style_mid.lower()


def test_health_style_red_below_30() -> None:
    """Scores below 30 map to red."""
    style = health_style(29)
    assert "red" in style
    style_low = health_style(1)
    assert "red" in style_low


def test_health_style_none_is_dim() -> None:
    """None score renders as dim."""
    assert health_style(None) == "dim"


# ---------------------------------------------------------------------------
# 3. derive_seed — determinism
# ---------------------------------------------------------------------------


def test_derive_seed_deterministic() -> None:
    """Same (workspace, account, date) always yields the same seed."""
    s1 = derive_seed("lattice-build", "crucible", date(2026, 4, 1))
    s2 = derive_seed("lattice-build", "crucible", date(2026, 4, 1))
    assert s1 == s2


def test_derive_seed_differs_across_dates() -> None:
    """Different start_dates produce different seeds for the same account."""
    s1 = derive_seed("lattice-build", "crucible", date(2026, 4, 1))
    s2 = derive_seed("lattice-build", "crucible", date(2026, 5, 1))
    assert s1 != s2


def test_derive_seed_differs_across_accounts() -> None:
    """Different accounts produce different seeds on the same date."""
    s1 = derive_seed("lattice-build", "crucible", date(2026, 4, 1))
    s2 = derive_seed("lattice-build", "phalanx-systems", date(2026, 4, 1))
    assert s1 != s2


def test_derive_seed_non_negative_and_bounded() -> None:
    """Seed is in [0, 9_999_999]."""
    for slug in ["crucible", "phalanx-systems", "ironclad-pipeline"]:
        s = derive_seed("lattice-build", slug, date(2026, 4, 1))
        assert 0 <= s <= 9_999_999


# ---------------------------------------------------------------------------
# 4. Entry ID generation
# ---------------------------------------------------------------------------


def test_generate_entry_id_is_8_char_hex() -> None:
    """generate_entry_id() returns exactly 8 lowercase hex characters."""
    import re

    for _ in range(20):
        entry_id = generate_entry_id()
        assert re.fullmatch(r"[0-9a-f]{8}", entry_id), f"bad id: {entry_id!r}"


def test_generate_entry_id_unique_across_calls() -> None:
    """generate_entry_id() produces different values on consecutive calls (probabilistic)."""
    ids = {generate_entry_id() for _ in range(50)}
    # Extremely unlikely to have fewer than 45 unique IDs in 50 tries
    assert len(ids) >= 45


# ---------------------------------------------------------------------------
# 5. estimate_cost
# ---------------------------------------------------------------------------


def test_estimate_cost_scales_with_accounts_and_weeks() -> None:
    """Narrative cost scales linearly with n_accounts * n_weeks."""
    narr, audit = estimate_cost(12, 8)
    # 12 * 8 * 0.05 = $4.80 narrative; 12 * 0.005 = $0.06 audit
    assert abs(narr - 4.80) < 0.01
    assert abs(audit - 0.06) < 0.01


def test_estimate_cost_audit_is_per_account_not_per_week() -> None:
    """Audit cost depends on account count, not week count."""
    _, audit_4w = estimate_cost(5, 4)
    _, audit_8w = estimate_cost(5, 8)
    assert abs(audit_4w - audit_8w) < 0.001


# ---------------------------------------------------------------------------
# 6. build_multi_account_preview_table
# ---------------------------------------------------------------------------


def test_multi_account_preview_row_per_account() -> None:
    """build_multi_account_preview_table has one row per account."""
    curves = [
        ("crucible", [(date(2026, 4, 1), 84), (date(2026, 4, 8), 66)], "declining"),
        ("phalanx-systems", [(date(2026, 4, 1), 80), (date(2026, 4, 8), 78)], "stable"),
    ]
    current_health = {"crucible": 57, "phalanx-systems": 80}
    table = build_multi_account_preview_table(curves, current_health)
    assert len(table.rows) == 2


def test_multi_account_preview_handles_empty_input() -> None:
    """Empty input does not raise."""
    table = build_multi_account_preview_table([], {})
    # Table is returned without error; no rows
    assert table is not None


def test_multi_account_preview_columns_include_all_weeks() -> None:
    """Column count covers the longest curve."""
    curves = [
        ("a", [(date(2026, 4, 1) + timedelta(weeks=i), 70) for i in range(6)], "stable"),
        ("b", [(date(2026, 4, 1) + timedelta(weeks=i), 60) for i in range(3)], "declining"),
    ]
    table = build_multi_account_preview_table(curves, {"a": 70, "b": 60})
    # Fixed columns: Account, Current, Primitive + 6 week columns
    assert len(table.columns) == 3 + 6


# ---------------------------------------------------------------------------
# 7. load_workspace_accounts — mock DB
# ---------------------------------------------------------------------------


def test_load_workspace_accounts_returns_account_list() -> None:
    """load_workspace_accounts parses mock DB rows into WorkspaceAccountInfo."""
    client = _make_client_mock(
        accounts=[
            {"slug": "crucible", "name": "Crucible", "overall_health_score": 57},
            {"slug": "phalanx-systems", "name": "Phalanx Systems", "overall_health_score": 80},
        ]
    )
    accounts, err = load_workspace_accounts("test-workspace", client)
    assert err is None
    assert len(accounts) == 2
    slugs = [a.slug for a in accounts]
    assert "crucible" in slugs
    assert "phalanx-systems" in slugs


def test_load_workspace_accounts_missing_workspace_returns_error() -> None:
    """When the workspace lookup returns empty data, an error string is returned."""
    client = MagicMock()
    ws_resp = MagicMock()
    ws_resp.data = None
    (
        client.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value
    ) = ws_resp
    accounts, err = load_workspace_accounts("does-not-exist", client)
    assert err is not None
    assert "not found" in err.lower() or "workspace" in err.lower()
    assert accounts == []


def test_load_workspace_accounts_db_exception_returns_error() -> None:
    """DB exceptions are caught and returned as an error string."""
    client = MagicMock()
    client.table.side_effect = RuntimeError("connection refused")
    accounts, err = load_workspace_accounts("lattice-build", client)
    assert err is not None
    assert accounts == []


# ---------------------------------------------------------------------------
# 8. save-spec-only path (action="save") via run_author_tui
# ---------------------------------------------------------------------------


def test_run_author_tui_save_spec_produces_valid_yaml(tmp_path: Path) -> None:
    """run_author_tui with action='save' writes a parseable YAML spec."""
    spec_path = tmp_path / "trajectory.test.yaml"
    console, _buf = _make_console()
    client = _make_client_mock()

    prompt_responses = [
        "all",            # Apply trajectory to
        "2026-04-01",     # start_date
        "2026-04-28",     # end_date
        "declining",      # primitive
        "80",             # start_health
        "50",             # end_health
        "linear",         # slope_shape
        # no seed prompt — seeds are auto-derived
        "save",           # action
    ]
    confirm_responses = [
        False,            # per-account customization
    ]

    with (
        patch("src.simulator.author.Prompt.ask", side_effect=prompt_responses),
        patch("src.simulator.author.Confirm.ask", side_effect=confirm_responses),
    ):
        exit_code = run_author_tui(
            workspace_slug="test-workspace",
            spec_path=spec_path,
            console=console,
            client=client,
        )

    assert exit_code == 0
    assert spec_path.exists(), "spec file was not created"
    loaded = load_spec(spec_path)
    assert loaded.workspace_slug == "test-workspace"
    # Both accounts from the mock should have entries
    assert len(loaded.trajectories) >= 1
    # All entries should be pending
    for entries in loaded.trajectories.values():
        for e in entries:
            assert e.generated_at is None


# ---------------------------------------------------------------------------
# 9. Collision warning path
# ---------------------------------------------------------------------------


def test_run_author_tui_collision_abort_writes_nothing(tmp_path: Path) -> None:
    """When the date range collides and the user aborts, no spec is written."""
    # Pre-populate a spec with an overlapping entry
    existing_entry = TrajectoryEntry(
        id="aabb1122",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 28),
        primitive="stable",
        params=TrajectoryParams(**{"target_band": [70, 80]}),
        seed=100,
        generated_at=None,
    )
    existing_spec = TrajectorySpec(
        workspace_slug="test-workspace",
        trajectories={"crucible": [existing_entry]},
    )
    spec_path = tmp_path / "trajectory.test.yaml"
    save_spec(existing_spec, spec_path)

    console, _ = _make_console()
    client = _make_client_mock()

    # Overlapping date range: 2026-04-15 to 2026-05-12
    prompt_responses = [
        "all",
        "2026-04-15",
        "2026-05-12",
        # Collision prompt will fire — confirm.ask is False, so we stop
    ]
    confirm_responses = [
        False,  # "Continue anyway?" → no
    ]

    with (
        patch("src.simulator.author.Prompt.ask", side_effect=prompt_responses),
        patch("src.simulator.author.Confirm.ask", side_effect=confirm_responses),
    ):
        exit_code = run_author_tui(
            workspace_slug="test-workspace",
            spec_path=spec_path,
            console=console,
            client=client,
        )

    assert exit_code == 0
    # Spec should be unchanged — only the original entry remains
    reloaded = load_spec(spec_path)
    assert sum(len(v) for v in reloaded.trajectories.values()) == 1


# ---------------------------------------------------------------------------
# 10. Cancel path writes nothing
# ---------------------------------------------------------------------------


def test_run_author_tui_cancel_writes_nothing(tmp_path: Path) -> None:
    """Selecting 'cancel' at the action prompt does not write a spec."""
    spec_path = tmp_path / "trajectory.test.yaml"
    console, _ = _make_console()
    client = _make_client_mock()

    prompt_responses = [
        "all",
        "2026-04-01",
        "2026-04-28",
        "declining",
        "80",
        "50",
        "linear",
        "cancel",
    ]
    confirm_responses = [
        False,  # per-account customization
    ]

    with (
        patch("src.simulator.author.Prompt.ask", side_effect=prompt_responses),
        patch("src.simulator.author.Confirm.ask", side_effect=confirm_responses),
    ):
        exit_code = run_author_tui(
            workspace_slug="test-workspace",
            spec_path=spec_path,
            console=console,
            client=client,
        )

    assert exit_code == 0
    assert not spec_path.exists(), "spec should not be written on cancel"


# ---------------------------------------------------------------------------
# 11. Quit scope path
# ---------------------------------------------------------------------------


def test_run_author_tui_quit_at_scope_exits_cleanly(tmp_path: Path) -> None:
    """Choosing 'quit' at the scope prompt exits with code 0 and writes nothing."""
    spec_path = tmp_path / "trajectory.test.yaml"
    console, _ = _make_console()
    client = _make_client_mock()

    with patch("src.simulator.author.Prompt.ask", return_value="quit"):
        exit_code = run_author_tui(
            workspace_slug="test-workspace",
            spec_path=spec_path,
            console=console,
            client=client,
        )

    assert exit_code == 0
    assert not spec_path.exists()


# ---------------------------------------------------------------------------
# 12. Per-account customization flow — override applied
# ---------------------------------------------------------------------------


def test_run_author_tui_per_account_override(tmp_path: Path) -> None:
    """Per-account override replaces the global primitive for that account."""
    spec_path = tmp_path / "trajectory.test.yaml"
    console, _ = _make_console()
    client = _make_client_mock(
        accounts=[
            {"slug": "crucible", "name": "Crucible", "overall_health_score": 57},
        ]
    )

    prompt_responses = [
        "all",
        "2026-04-01",
        "2026-04-28",
        "declining",   # global primitive
        "80",
        "50",
        "linear",
        # no seed prompt — seeds are auto-derived
        # per-account: crucible override
        "stable",      # override primitive for crucible
        "60",          # target_band low
        "80",          # target_band high
        "2026-04-01",  # acc start
        "2026-04-28",  # acc end
        "save",        # action
    ]
    confirm_responses = [
        True,   # per-account customization?
        True,   # override for crucible?
    ]

    with (
        patch("src.simulator.author.Prompt.ask", side_effect=prompt_responses),
        patch("src.simulator.author.Confirm.ask", side_effect=confirm_responses),
    ):
        exit_code = run_author_tui(
            workspace_slug="test-workspace",
            spec_path=spec_path,
            console=console,
            client=client,
        )

    assert exit_code == 0
    spec = load_spec(spec_path)
    crucible_entries = spec.trajectories.get("crucible", [])
    assert len(crucible_entries) == 1
    assert crucible_entries[0].primitive == "stable", (
        f"Expected 'stable' override, got {crucible_entries[0].primitive!r}"
    )


# ---------------------------------------------------------------------------
# 13. build_account_overview_table renders correctly
# ---------------------------------------------------------------------------


def test_build_account_overview_table_renders() -> None:
    """build_account_overview_table renders without error and includes slugs."""
    accounts = [
        _make_account("crucible", "Crucible", 57),
        _make_account("phalanx-systems", "Phalanx Systems", 80),
    ]
    console, buf = _make_console()
    table = build_account_overview_table(accounts, spec=None)
    console.print(table)
    output = buf.getvalue()
    assert "crucible" in output
    assert "phalanx-systems" in output


# ---------------------------------------------------------------------------
# 14. spec_path_for_workspace returns the canonical path
# ---------------------------------------------------------------------------


def test_spec_path_canonical_convention() -> None:
    """spec_path_for_workspace uses the trajectory.<slug>.yaml convention."""
    p = spec_path_for_workspace("lattice-build")
    assert p.name == "trajectory.lattice-build.yaml"
    assert "synthetic-scenarios" in str(p)
