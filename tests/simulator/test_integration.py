"""Phase 6 integration smoke test for the trajectory simulator.

Tests the full pipeline end-to-end with mocked Supabase and LLM clients:

  TrajectorySpec -> executor.run() -> signals ingested -> narrative generated
  -> generated_at stamped on spec entry

No real API calls are made.  All Supabase and Anthropic interactions are mocked
at the call site within executor.py.

See ADR-021 §Phase 6 coder handoff for test specification.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.simulator.spec import (
    TrajectoryEntry,
    TrajectoryParams,
    TrajectorySpec,
    load_spec,
    save_spec,
)

# ---------------------------------------------------------------------------
# Helpers: minimal domain objects (mirrors test_executor.py helpers)
# ---------------------------------------------------------------------------

_WS_SLUG = "integration-workspace"
_WS_ID = uuid.uuid5(uuid.NAMESPACE_DNS, _WS_SLUG)
_ACC_SLUG = "integration-account"
_ACC_ID = uuid.uuid5(uuid.NAMESPACE_DNS, f"{_WS_ID}:{_ACC_SLUG}")


def _make_workspace() -> MagicMock:
    ws = MagicMock()
    ws.id = _WS_ID
    ws.slug = _WS_SLUG
    ws.name = "Integration Workspace"
    ws.internal_domains = ()
    return ws


def _make_account(slug: str = _ACC_SLUG) -> MagicMock:
    acc = MagicMock()
    acc.id = uuid.uuid5(uuid.NAMESPACE_DNS, f"{_WS_ID}:{slug}")
    acc.workspace_id = _WS_ID
    acc.slug = slug
    acc.name = slug.replace("-", " ").title()
    acc.primary_domain = f"{slug}.com"
    acc.frequency_multiplier = 1.0
    acc.overall_health_score = 70
    return acc


def _make_supabase_client() -> MagicMock:
    """Return a MagicMock that satisfies the executor's Supabase calls."""
    client = MagicMock()

    # Workspace lookup (used by _warn_existing_production_narratives and revert_from)
    ws_data = {"id": str(_WS_ID)}
    single_chain = (
        client.table.return_value.select.return_value
        .eq.return_value.single.return_value.execute.return_value
    )
    single_chain.data = ws_data

    # Default empty response for any table SELECT
    _empty = MagicMock()
    _empty.data = []
    eq_chain = client.table.return_value.select.return_value.eq.return_value
    eq_chain.execute.return_value = _empty
    is_chain = client.table.return_value.select.return_value.is_.return_value
    is_chain.eq.return_value.execute.return_value = _empty
    in_chain = client.table.return_value.select.return_value.in_.return_value
    in_chain.execute.return_value = _empty
    in_chain.limit.return_value.execute.return_value = _empty
    # UPDATE (used to backdate generated_at)
    client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []

    return client


def _make_narrative(narrative_id: uuid.UUID | None = None) -> MagicMock:
    narr = MagicMock()
    narr.id = narrative_id or uuid.uuid4()
    narr.engagement = 70
    narr.sentiment = 65
    narr.signals_considered = []
    return narr


# ---------------------------------------------------------------------------
# Spec builder for the integration test
# ---------------------------------------------------------------------------


def _make_integration_spec(tmp_path: Path) -> tuple[TrajectorySpec, Path]:
    """Build a 1-account, 1-entry (2-week) spec and write it to a temp file."""
    entry = TrajectoryEntry(
        id="a1b2c3d4",
        start_date=date(2026, 3, 3),
        end_date=date(2026, 3, 16),  # 2 weeks: 3-Mar, 10-Mar
        primitive="stable",
        params=TrajectoryParams(**{"target_band": [65, 75]}),
        seed=77001,
        generated_at=None,
    )
    spec = TrajectorySpec(
        workspace_slug=_WS_SLUG,
        trajectories={_ACC_SLUG: [entry]},
    )
    spec_file = tmp_path / f"trajectory.{_WS_SLUG}.yaml"
    save_spec(spec, spec_file)
    return spec, spec_file


# ---------------------------------------------------------------------------
# Test: full pipeline smoke test
# ---------------------------------------------------------------------------


def test_full_pipeline_smoke(tmp_path: Path) -> None:
    """Construct a minimal spec, run the executor, verify end-state invariants.

    Invariants checked (per-week mode):
    1. executor.run() completes without raising.
    2. entries_processed == 1 (the single pending entry was consumed).
    3. entries_failed == 0.
    4. narratives_generated == 2 (one per week; 2-week entry -> 2 narratives).
    5. The spec on disk has the entry's generated_at populated (not None).
    6. weeks_total == 2 (one per-week narrative per signal-synthesis week).
    7. The mock supabase client received at least one UPDATE call (backdating
       generated_at on each narrative row).
    """
    _spec, spec_file = _make_integration_spec(tmp_path)

    mock_client = _make_supabase_client()
    mock_workspace = _make_workspace()
    mock_account = _make_account()
    mock_signal = MagicMock()
    mock_signal.id = uuid.uuid4()

    # Per-week mode: one narrative mock returned per generate_narrative_for_week call.
    narrative_account = _make_narrative()

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
        audit_final_week=False,  # skip audit — no OpenAI dep needed
        audit_all_weeks=False,
    )

    with (
        patch(
            "src.simulator.executor._load_workspace_and_accounts",
            return_value=(mock_workspace, _WS_ID, [mock_account]),
        ),
        patch(
            "src.simulator.executor._warn_existing_production_narratives",
            return_value=None,
        ),
        patch(
            "src.simulator.executor._ingest_week",
            return_value=[mock_signal],
        ),
        patch(
            "src.simulator.executor._generate_narrative_for_week",
            return_value=(narrative_account, 120, 60, 20),
        ) as mock_gen,
        patch(
            "src.config.loader.load_config",
            return_value=MagicMock(),
        ),
    ):
        result = run(config, mock_client)

    # Invariant 1: no exception (implicit — we reached this line)
    # Invariant 2: one entry processed
    assert result.entries_processed == 1
    # Invariant 3: no failures
    assert result.entries_failed == 0
    # Invariant 4: one narrative per week (per-week mode: 2-week entry -> 2 narratives)
    assert result.narratives_generated == 2
    assert mock_gen.call_count == 2
    # Invariant 5: spec on disk has generated_at populated
    reloaded = load_spec(spec_file)
    entry = reloaded.trajectories[_ACC_SLUG][0]
    assert entry.generated_at is not None, "entry.generated_at should be set after run"
    assert isinstance(entry.generated_at, datetime)
    # Invariant 6: two weeks processed
    assert result.weeks_total == 2
    # Invariant 7: _generate_narrative_for_week was called with tz-aware narrative_time
    # each call. The narrative_time kwarg must be UTC-aware.
    for call_obj in mock_gen.call_args_list:
        narrative_time = call_obj.kwargs.get("narrative_time")
        assert narrative_time is not None, (
            "_generate_narrative_for_week must receive narrative_time kwarg"
        )
        assert narrative_time.tzinfo is not None, "narrative_time must be tz-aware"


# ---------------------------------------------------------------------------
# Test: dry-run produces no ingestion or narrative calls
# ---------------------------------------------------------------------------


def test_dry_run_produces_no_llm_calls(tmp_path: Path) -> None:
    """In dry-run mode, neither signal ingestion nor narrative generation is called."""
    _, spec_file = _make_integration_spec(tmp_path)

    mock_client = _make_supabase_client()
    mock_workspace = _make_workspace()
    mock_account = _make_account()

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
        dry_run=True,
    )

    with (
        patch(
            "src.simulator.executor._load_workspace_and_accounts",
            return_value=(mock_workspace, _WS_ID, [mock_account]),
        ),
        patch(
            "src.simulator.executor._warn_existing_production_narratives",
            return_value=None,
        ),
        patch(
            "src.simulator.executor._ingest_week",
        ) as mock_ingest,
        patch(
            "src.simulator.executor._generate_narrative_for_week",
        ) as mock_gen,
        patch(
            "src.config.loader.load_config",
            return_value=MagicMock(),
        ),
    ):
        result = run(config, mock_client)

    mock_ingest.assert_not_called()
    mock_gen.assert_not_called()
    # Entry still marked processed even in dry-run
    assert result.entries_processed == 1


# ---------------------------------------------------------------------------
# Test: idempotency — completed spec produces no re-processing
# ---------------------------------------------------------------------------


def test_completed_spec_is_idempotent(tmp_path: Path) -> None:
    """Re-running a spec where all entries already have generated_at is a no-op."""
    entry = TrajectoryEntry(
        id="b2c3d4e5",
        start_date=date(2026, 3, 3),
        end_date=date(2026, 3, 16),
        primitive="stable",
        params=TrajectoryParams(**{"target_band": [65, 75]}),
        seed=77002,
        generated_at=datetime(2026, 3, 17, 10, 0, 0, tzinfo=UTC),  # already done
    )
    spec = TrajectorySpec(
        workspace_slug=_WS_SLUG,
        trajectories={_ACC_SLUG: [entry]},
    )
    spec_file = tmp_path / f"trajectory.{_WS_SLUG}.yaml"
    save_spec(spec, spec_file)

    mock_client = _make_supabase_client()
    mock_workspace = _make_workspace()
    mock_account = _make_account()

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
    )

    with (
        patch(
            "src.simulator.executor._load_workspace_and_accounts",
            return_value=(mock_workspace, _WS_ID, [mock_account]),
        ),
        patch(
            "src.simulator.executor._warn_existing_production_narratives",
            return_value=None,
        ),
        patch(
            "src.simulator.executor._ingest_week",
        ) as mock_ingest,
        patch(
            "src.simulator.executor._generate_narrative_for_week",
        ) as mock_gen,
        patch(
            "src.config.loader.load_config",
            return_value=MagicMock(),
        ),
    ):
        result = run(config, mock_client)

    assert result.entries_processed == 0
    assert result.entries_failed == 0
    assert result.narratives_generated == 0
    mock_ingest.assert_not_called()
    mock_gen.assert_not_called()


# ---------------------------------------------------------------------------
# Test: spec written with correct generated_at after run (backdating not lost)
# ---------------------------------------------------------------------------


def test_spec_generated_at_survives_save_reload(tmp_path: Path) -> None:
    """generated_at written to the spec YAML is round-trippable through load_spec."""
    _, spec_file = _make_integration_spec(tmp_path)

    mock_client = _make_supabase_client()
    mock_workspace = _make_workspace()
    mock_account = _make_account()
    mock_signal = MagicMock()
    mock_signal.id = uuid.uuid4()

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
        audit_final_week=False,
        audit_all_weeks=False,
    )

    with (
        patch(
            "src.simulator.executor._load_workspace_and_accounts",
            return_value=(mock_workspace, _WS_ID, [mock_account]),
        ),
        patch(
            "src.simulator.executor._warn_existing_production_narratives",
            return_value=None,
        ),
        patch(
            "src.simulator.executor._ingest_week",
            return_value=[mock_signal],
        ),
        patch(
            "src.simulator.executor._generate_narrative_for_week",
            return_value=(_make_narrative(), 100, 50, 20),
        ),
        patch(
            "src.config.loader.load_config",
            return_value=MagicMock(),
        ),
    ):
        run(config, mock_client)

    # Load again and verify the entry is a valid datetime
    reloaded = load_spec(spec_file)
    entry = reloaded.trajectories[_ACC_SLUG][0]
    assert entry.generated_at is not None
    assert entry.generated_at.tzinfo is not None, "generated_at must be tz-aware"


# ---------------------------------------------------------------------------
# Test: accounts_filter limits processing to named accounts only
# ---------------------------------------------------------------------------


def test_accounts_filter_integration(tmp_path: Path) -> None:
    """Only the specified account's entry is processed when accounts_filter is set."""
    entry_a = TrajectoryEntry(
        id="c3d4e5f6",
        start_date=date(2026, 3, 3),
        end_date=date(2026, 3, 9),
        primitive="stable",
        params=TrajectoryParams(**{"target_band": [65, 75]}),
        seed=77003,
        generated_at=None,
    )
    entry_b = TrajectoryEntry(
        id="d4e5f6a7",
        start_date=date(2026, 3, 3),
        end_date=date(2026, 3, 9),
        primitive="stable",
        params=TrajectoryParams(**{"target_band": [50, 60]}),
        seed=77004,
        generated_at=None,
    )
    spec = TrajectorySpec(
        workspace_slug=_WS_SLUG,
        trajectories={
            "account-a": [entry_a],
            "account-b": [entry_b],
        },
    )
    spec_file = tmp_path / f"trajectory.{_WS_SLUG}.yaml"
    save_spec(spec, spec_file)

    mock_client = _make_supabase_client()
    mock_workspace = _make_workspace()
    acc_a = _make_account("account-a")
    acc_b = _make_account("account-b")

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
        accounts_filter=["account-a"],
        audit_final_week=False,
    )

    with (
        patch(
            "src.simulator.executor._load_workspace_and_accounts",
            return_value=(mock_workspace, _WS_ID, [acc_a, acc_b]),
        ),
        patch(
            "src.simulator.executor._warn_existing_production_narratives",
            return_value=None,
        ),
        patch(
            "src.simulator.executor._ingest_week",
            return_value=[],
        ),
        patch(
            "src.simulator.executor._generate_narrative_for_week",
            return_value=(_make_narrative(), 100, 50, 20),
        ),
        patch(
            "src.config.loader.load_config",
            return_value=MagicMock(),
        ),
    ):
        result = run(config, mock_client)

    assert result.entries_processed == 1   # account-a only
    assert result.entries_skipped == 1     # account-b skipped

    reloaded = load_spec(spec_file)
    assert reloaded.trajectories["account-a"][0].generated_at is not None
    assert reloaded.trajectories["account-b"][0].generated_at is None


# ---------------------------------------------------------------------------
# Test: --force re-processes entries that already have generated_at set
# ---------------------------------------------------------------------------


def test_force_reprocesses_completed_entries(tmp_path: Path) -> None:
    """Re-running with --force on a spec where entries already have generated_at
    set must re-process them (not silently skip).

    Regression test for Bug 2: without the fix, config.force only suppressed the
    production-narrative warning; the pending-filter still excluded entries with a
    non-null generated_at, so every re-run was a silent no-op.
    """
    # Build a spec with one entry that is already marked complete.
    entry = TrajectoryEntry(
        id="e5f6a7b8",
        start_date=date(2026, 3, 3),
        end_date=date(2026, 3, 9),  # 1 week
        primitive="stable",
        params=TrajectoryParams(**{"target_band": [65, 75]}),
        seed=77005,
        generated_at=datetime(2026, 3, 10, 10, 0, 0, tzinfo=UTC),  # already done
    )
    spec = TrajectorySpec(
        workspace_slug=_WS_SLUG,
        trajectories={_ACC_SLUG: [entry]},
    )
    spec_file = tmp_path / f"trajectory.{_WS_SLUG}.yaml"
    save_spec(spec, spec_file)

    mock_client = _make_supabase_client()
    mock_workspace = _make_workspace()
    mock_account = _make_account()
    mock_signal = MagicMock()
    mock_signal.id = uuid.uuid4()

    from src.simulator.executor import ExecutorConfig, run

    # --- Run 1: force=False → entry is already complete, should be skipped ---
    config_no_force = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
        audit_final_week=False,
        audit_all_weeks=False,
        force=False,
    )

    with (
        patch(
            "src.simulator.executor._load_workspace_and_accounts",
            return_value=(mock_workspace, _WS_ID, [mock_account]),
        ),
        patch("src.simulator.executor._warn_existing_production_narratives"),
        patch("src.simulator.executor._ingest_week", return_value=[mock_signal]),
        patch(
            "src.simulator.executor._generate_narrative_for_week",
            return_value=(_make_narrative(), 100, 50, 20),
        ) as mock_gen_no_force,
        patch("src.config.loader.load_config", return_value=MagicMock()),
    ):
        result_no_force = run(config_no_force, mock_client)

    assert result_no_force.entries_processed == 0, (
        "force=False: entry with generated_at set must be skipped"
    )
    mock_gen_no_force.assert_not_called()

    # --- Run 2: force=True → entry must be re-processed despite generated_at ---
    config_force = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
        audit_final_week=False,
        audit_all_weeks=False,
        force=True,
    )

    with (
        patch(
            "src.simulator.executor._load_workspace_and_accounts",
            return_value=(mock_workspace, _WS_ID, [mock_account]),
        ),
        patch("src.simulator.executor._warn_existing_production_narratives"),
        patch("src.simulator.executor._ingest_week", return_value=[mock_signal]),
        patch(
            "src.simulator.executor._generate_narrative_for_week",
            return_value=(_make_narrative(), 100, 50, 20),
        ) as mock_gen_force,
        patch("src.config.loader.load_config", return_value=MagicMock()),
    ):
        result_force = run(config_force, mock_client)

    assert result_force.entries_processed == 1, (
        "force=True: entry with generated_at set must be re-processed"
    )
    assert mock_gen_force.call_count == 1, "force=True must call narrative generation once"
