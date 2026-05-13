"""Tests for src/simulator/bootstrap.py.

Strategy: mock the Supabase client; no real DB access.
All 15 tests from the Phase 5.5 handoff are covered here.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from src.simulator.author import derive_seed
from src.simulator.bootstrap import (
    BootstrapConfig,
    _propose_entry,
    _resolve_dates,
    bootstrap_workspace,
)
from src.simulator.spec import (
    TrajectoryEntry,
    TrajectoryParams,
    TrajectorySpec,
    load_spec,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_WS = "test-workspace"
_START = date(2026, 4, 1)
_END = date(2026, 4, 28)


def _no_dim() -> list[int]:
    return []


def _make_entry(
    start: date = _START,
    end: date = _END,
    primitive: str = "stable",
    params: dict | None = None,
    entry_id: str = "aabb1122",
) -> TrajectoryEntry:
    if params is None:
        params = {"target_band": [60, 80]}
    return TrajectoryEntry(
        id=entry_id,
        start_date=start,
        end_date=end,
        primitive=primitive,
        params=TrajectoryParams(**params),
        seed=1234567,
        generated_at=None,
    )


def _make_client_mock(
    account_rows: list[dict] | None = None,
    dim_rows: list[dict] | None = None,
) -> MagicMock:
    """Return a mock supabase client.

    Workspace lookup → id='ws-001'.
    Account rows default to a single healthy account.
    Dimension score rows default to empty (no scores).
    """
    client = MagicMock()

    # Workspace lookup (table().select().eq().single().execute())
    ws_resp = MagicMock()
    ws_resp.data = {"id": "ws-001"}
    (
        client.table.return_value
        .select.return_value
        .eq.return_value
        .single.return_value
        .execute.return_value
    ) = ws_resp

    # Account lookup (table().select().eq().order().execute())
    acc_resp = MagicMock()
    acc_resp.data = account_rows if account_rows is not None else [
        {"slug": "alpha", "status": "active", "overall_health_score": 80, "deleted_at": None},
    ]
    (
        client.table.return_value
        .select.return_value
        .eq.return_value
        .order.return_value
        .execute.return_value
    ) = acc_resp

    # Dimension scores — first try rpc(), then fall back to table query.
    dim_resp = MagicMock()
    dim_resp.data = dim_rows if dim_rows is not None else []
    client.rpc.return_value.execute.return_value = dim_resp

    return client


# ---------------------------------------------------------------------------
# Test 1 — Healthy account (h >= 75) → stable
# ---------------------------------------------------------------------------


def test_propose_entry_healthy_stable() -> None:
    """h=80, no divergence → stable with target_band [75, 85]."""
    entry = _propose_entry(
        account_slug="alpha",
        overall_health=80,
        dim_scores=_no_dim(),
        status="active",
        existing_entries=[],
        start_date=_START,
        end_date=_END,
        workspace_slug=_WS,
    )
    assert entry is not None
    assert entry.primitive == "stable"
    band = entry.params.model_extra["target_band"]
    assert band == [75, 85]


# ---------------------------------------------------------------------------
# Test 2 — Moderate stable (50–74, divergence < 30) → stable
# ---------------------------------------------------------------------------


def test_propose_entry_moderate_stable() -> None:
    """h=62, dim scores [58, 65, 60] → divergence=7 < 30 → stable [55, 69]."""
    entry = _propose_entry(
        account_slug="beta",
        overall_health=62,
        dim_scores=[58, 65, 60],
        status="active",
        existing_entries=[],
        start_date=_START,
        end_date=_END,
        workspace_slug=_WS,
    )
    assert entry is not None
    assert entry.primitive == "stable"
    band = entry.params.model_extra["target_band"]
    assert band == [55, 69]


# ---------------------------------------------------------------------------
# Test 3 — Moderate with divergence (50–74, divergence >= 30) → declining
# ---------------------------------------------------------------------------


def test_propose_entry_moderate_divergent() -> None:
    """h=62, dim scores [40, 71] → divergence=31 >= 30 → declining start=77 end=62."""
    entry = _propose_entry(
        account_slug="gamma",
        overall_health=62,
        dim_scores=[40, 71],
        status="active",
        existing_entries=[],
        start_date=_START,
        end_date=_END,
        workspace_slug=_WS,
    )
    assert entry is not None
    assert entry.primitive == "declining"
    assert entry.params.model_extra["start_health"] == 77
    assert entry.params.model_extra["end_health"] == 62


# ---------------------------------------------------------------------------
# Test 4 — At-risk account (30–49) → declining
# ---------------------------------------------------------------------------


def test_propose_entry_at_risk() -> None:
    """h=38 → at-risk → declining start=63 end=38."""
    entry = _propose_entry(
        account_slug="delta",
        overall_health=38,
        dim_scores=_no_dim(),
        status="active",
        existing_entries=[],
        start_date=_START,
        end_date=_END,
        workspace_slug=_WS,
    )
    assert entry is not None
    assert entry.primitive == "declining"
    assert entry.params.model_extra["start_health"] == 63
    assert entry.params.model_extra["end_health"] == 38


# ---------------------------------------------------------------------------
# Test 5 — Severe account (< 30) → cliff
# ---------------------------------------------------------------------------


def test_propose_entry_severe_cliff() -> None:
    """h=18 → severe → cliff with pre_band=[55,65], post_band clamped to [13,23]."""
    entry = _propose_entry(
        account_slug="epsilon",
        overall_health=18,
        dim_scores=_no_dim(),
        status="active",
        existing_entries=[],
        start_date=_START,
        end_date=_END,
        workspace_slug=_WS,
    )
    assert entry is not None
    assert entry.primitive == "cliff"
    assert entry.params.model_extra["pre_band"] == [55, 65]
    assert entry.params.model_extra["post_band"] == [13, 23]
    # cliff_date should be the midpoint of [_START, _END]
    days = (_END - _START).days  # 27 days
    expected_midpoint = _START + timedelta(days=days // 2)
    assert entry.params.model_extra["cliff_date"] == expected_midpoint.isoformat()


# ---------------------------------------------------------------------------
# Test 6 — Candidate status → recovering regardless of health
# ---------------------------------------------------------------------------


def test_propose_entry_candidate_recovering() -> None:
    """status='candidate', h=35 → recovering start=40 end=60 (ignores health band)."""
    entry = _propose_entry(
        account_slug="zeta",
        overall_health=35,
        dim_scores=_no_dim(),
        status="candidate",
        existing_entries=[],
        start_date=_START,
        end_date=_END,
        workspace_slug=_WS,
    )
    assert entry is not None
    assert entry.primitive == "recovering"
    assert entry.params.model_extra["start_health"] == 40
    assert entry.params.model_extra["end_health"] == 60


# ---------------------------------------------------------------------------
# Test 7 — Soft-deleted account → skipped (counted in BootstrapResult)
# ---------------------------------------------------------------------------


def test_bootstrap_soft_deleted_skipped(tmp_path: Path) -> None:
    """deleted_at IS NOT NULL → skipped_deleted incremented, no entry proposed."""
    client = _make_client_mock(
        account_rows=[
            {
                "slug": "deleted-account",
                "status": "active",
                "overall_health_score": 80,
                "deleted_at": "2026-04-01T00:00:00+00:00",
            },
        ],
    )
    cfg = BootstrapConfig(
        workspace_slug=_WS,
        weeks=4,
        out_path=tmp_path / "trajectory.yaml",
        force=False,
    )
    result = bootstrap_workspace(cfg, client)
    assert result.skipped_deleted == 1
    assert result.proposed_count == 0


# ---------------------------------------------------------------------------
# Test 8 — NULL health score fallback: stable[50,70], counted in skipped_no_health
# ---------------------------------------------------------------------------


def test_propose_entry_null_health_fallback() -> None:
    """overall_health=None → stable with target_band=[50, 70]."""
    entry = _propose_entry(
        account_slug="no-health",
        overall_health=None,
        dim_scores=_no_dim(),
        status="active",
        existing_entries=[],
        start_date=_START,
        end_date=_END,
        workspace_slug=_WS,
    )
    assert entry is not None
    assert entry.primitive == "stable"
    assert entry.params.model_extra["target_band"] == [50, 70]


def test_bootstrap_null_health_counted(tmp_path: Path) -> None:
    """NULL health score accounts appear in skipped_no_health (but entry is still proposed)."""
    client = _make_client_mock(
        account_rows=[
            {
                "slug": "no-health",
                "status": "active",
                "overall_health_score": None,
                "deleted_at": None,
            },
        ],
    )
    cfg = BootstrapConfig(
        workspace_slug=_WS,
        weeks=4,
        out_path=tmp_path / "trajectory.yaml",
        force=False,
    )
    result = bootstrap_workspace(cfg, client)
    # Entry is still proposed (fallback); it's also counted in skipped_no_health as a warning.
    assert result.skipped_no_health == 1
    assert result.proposed_count == 1


# ---------------------------------------------------------------------------
# Test 9 — No dimension scores → divergence=0, uses overall_health band only
# ---------------------------------------------------------------------------


def test_propose_entry_no_dim_scores_uses_health_band() -> None:
    """dim_scores=[] for h=62 → treat divergence as 0 → stable (moderate-stable band)."""
    entry = _propose_entry(
        account_slug="no-dims",
        overall_health=62,
        dim_scores=[],
        status="active",
        existing_entries=[],
        start_date=_START,
        end_date=_END,
        workspace_slug=_WS,
    )
    assert entry is not None
    # divergence=0 (single score), falls into moderate-stable
    assert entry.primitive == "stable"


# ---------------------------------------------------------------------------
# Test 10 — Continuation: existing entry end_date → bootstrap starts next day
# ---------------------------------------------------------------------------


def test_bootstrap_continuation_from_existing_entries(tmp_path: Path) -> None:
    """Account has existing entry ending 2026-04-28; bootstrap starts 2026-04-29."""
    existing_entry = _make_entry(
        start=date(2026, 4, 1),
        end=date(2026, 4, 28),
    )
    existing_spec = TrajectorySpec(
        workspace_slug=_WS,
        trajectories={"alpha": [existing_entry]},
    )
    spec_path = tmp_path / "trajectory.yaml"
    from src.simulator.spec import save_spec
    save_spec(existing_spec, spec_path)

    client = _make_client_mock(
        account_rows=[
            {"slug": "alpha", "status": "active", "overall_health_score": 80, "deleted_at": None},
        ],
    )
    cfg = BootstrapConfig(
        workspace_slug=_WS,
        weeks=4,
        out_path=spec_path,
        force=True,  # merge into existing
    )
    result = bootstrap_workspace(cfg, client)

    assert result.proposed_count == 1
    loaded = load_spec(spec_path)
    new_entries = [e for e in loaded.trajectories["alpha"] if e.start_date != date(2026, 4, 1)]
    assert len(new_entries) == 1
    assert new_entries[0].start_date == date(2026, 4, 29)


# ---------------------------------------------------------------------------
# Test 11 — Full coverage skip: account entries cover proposed range → skip
# ---------------------------------------------------------------------------


def test_bootstrap_full_coverage_skip(tmp_path: Path) -> None:
    """Existing entry ends after default_end → account skipped (covered)."""
    today = date.today()
    # Create an entry that ends tomorrow (well past today - 1)
    far_future_end = today + timedelta(days=10)
    existing_entry = _make_entry(
        start=today - timedelta(weeks=8),
        end=far_future_end,
    )
    existing_spec = TrajectorySpec(
        workspace_slug=_WS,
        trajectories={"alpha": [existing_entry]},
    )
    spec_path = tmp_path / "trajectory.yaml"
    from src.simulator.spec import save_spec
    save_spec(existing_spec, spec_path)

    client = _make_client_mock(
        account_rows=[
            {"slug": "alpha", "status": "active", "overall_health_score": 80, "deleted_at": None},
        ],
    )
    cfg = BootstrapConfig(
        workspace_slug=_WS,
        weeks=4,
        out_path=spec_path,
        force=True,
    )
    result = bootstrap_workspace(cfg, client)
    assert result.skipped_covered == 1
    assert result.proposed_count == 0


# ---------------------------------------------------------------------------
# Test 12 — Seed determinism: same inputs → same seed
# ---------------------------------------------------------------------------


def test_seed_determinism() -> None:
    """Same (workspace, account, start_date) always produces the same seed."""
    s1 = derive_seed("test-workspace", "alpha", date(2026, 4, 1))
    s2 = derive_seed("test-workspace", "alpha", date(2026, 4, 1))
    assert s1 == s2


# ---------------------------------------------------------------------------
# Test 13 — Seed diverges across start_dates (regression guard for texture repeat)
# ---------------------------------------------------------------------------


def test_seed_diverges_across_start_dates() -> None:
    """Different start_dates for the same account produce different seeds.

    Regression guard for ADR-021 §Consequences: without start_date in the hash,
    two consecutive entries on the same account share signal-axis draws (concern_topic,
    email_tone, contact-name picks), producing visibly identical texture despite
    different primitive math.
    """
    s1 = derive_seed("test-workspace", "alpha", date(2026, 4, 1))
    s2 = derive_seed("test-workspace", "alpha", date(2026, 5, 1))
    assert s1 != s2


# ---------------------------------------------------------------------------
# Test 14 — Spec validation: bootstrapped spec passes load_spec cleanly
# ---------------------------------------------------------------------------


def test_bootstrapped_spec_validates(tmp_path: Path) -> None:
    """The spec produced by bootstrap_workspace passes load_spec without ValidationError."""
    client = _make_client_mock(
        account_rows=[
            {"slug": "alpha", "status": "active", "overall_health_score": 80, "deleted_at": None},
            {"slug": "beta", "status": "active", "overall_health_score": 25, "deleted_at": None},
        ],
    )
    spec_path = tmp_path / "trajectory.test.yaml"
    cfg = BootstrapConfig(
        workspace_slug=_WS,
        weeks=4,
        out_path=spec_path,
        force=False,
    )
    bootstrap_workspace(cfg, client)

    # Must not raise pydantic.ValidationError
    loaded = load_spec(spec_path)
    assert loaded.workspace_slug == _WS
    assert len(loaded.trajectories) == 2


# ---------------------------------------------------------------------------
# Test 15 — Force overwrite: existing file merged when force=True
# ---------------------------------------------------------------------------


def test_bootstrap_force_overwrite(tmp_path: Path) -> None:
    """When force=True and spec exists, new entries are merged into the existing spec."""
    existing_spec = TrajectorySpec(
        workspace_slug=_WS,
        trajectories={
            "alpha": [
                _make_entry(
                    start=date(2026, 1, 1),
                    end=date(2026, 1, 28),
                    entry_id="aa001122",
                )
            ]
        },
    )
    spec_path = tmp_path / "trajectory.yaml"
    from src.simulator.spec import save_spec
    save_spec(existing_spec, spec_path)

    client = _make_client_mock(
        account_rows=[
            {"slug": "alpha", "status": "active", "overall_health_score": 80, "deleted_at": None},
        ],
    )
    cfg = BootstrapConfig(
        workspace_slug=_WS,
        weeks=4,
        out_path=spec_path,
        force=True,
    )
    result = bootstrap_workspace(cfg, client)
    # A new entry should have been merged in (existing entry ends 2026-01-28;
    # default range is ~4 weeks ago to yesterday — no overlap → new entry proposed)
    assert result.proposed_count == 1

    loaded = load_spec(spec_path)
    # Original entry preserved
    original = [e for e in loaded.trajectories["alpha"] if e.id == "aa001122"]
    assert len(original) == 1
    # New entry also present
    assert len(loaded.trajectories["alpha"]) == 2


# ---------------------------------------------------------------------------
# Test 16 — System pseudo-account slug (_unmatched) → skipped, counted in skipped_system
# ---------------------------------------------------------------------------


def test_bootstrap_system_slug_skipped(tmp_path: Path) -> None:
    """Slugs starting with '_' are skipped and counted in skipped_system, not proposed."""
    client = _make_client_mock(
        account_rows=[
            {
                "slug": "_unmatched",
                "status": "active",
                "overall_health_score": 55,
                "deleted_at": None,
            },
            {
                "slug": "real-account",
                "status": "active",
                "overall_health_score": 80,
                "deleted_at": None,
            },
        ],
    )
    cfg = BootstrapConfig(
        workspace_slug=_WS,
        weeks=4,
        out_path=tmp_path / "trajectory.yaml",
        force=False,
    )
    result = bootstrap_workspace(cfg, client)
    assert result.skipped_system == 1
    assert result.proposed_count == 1  # only real-account gets an entry


# ---------------------------------------------------------------------------
# Test: _resolve_dates continuation logic
# ---------------------------------------------------------------------------


def test_resolve_dates_no_existing() -> None:
    """No existing entries → returns (default_start, default_end)."""
    today = date.today()
    d_start = today - timedelta(weeks=4)
    d_end = today - timedelta(days=1)
    start, end = _resolve_dates([], d_start, d_end, 4)
    assert start == d_start
    assert end == d_end


def test_resolve_dates_continuation() -> None:
    """Existing entry ends 2026-04-28 → continuation starts 2026-04-29."""
    existing = [_make_entry(start=date(2026, 4, 1), end=date(2026, 4, 28))]
    default_start = date(2026, 4, 1)
    default_end = date(2026, 4, 28)  # same window
    start, _end = _resolve_dates(existing, default_start, default_end, 4)
    # continuation_start = 2026-04-29, which is > default_end → skip
    assert start is None


def test_resolve_dates_continuation_new_window() -> None:
    """Existing entry ends in the past; new window is in the future → continuation."""
    today = date.today()
    existing = [_make_entry(start=today - timedelta(weeks=8), end=today - timedelta(weeks=4))]
    default_start = today - timedelta(weeks=4)
    default_end = today - timedelta(days=1)
    start, _end = _resolve_dates(existing, default_start, default_end, 4)
    # continuation_start = (today - 4 weeks) + 1 day
    expected_start = today - timedelta(weeks=4) + timedelta(days=1)
    assert start == expected_start
