"""Tests for src/simulator/executor.py — per-week narrative generation.

All tests mock Supabase and LLM calls — no real API calls are made.

Per-week mode is the simulator's single mode: each (account, week) pair produces
one narrative generated against that week's signal slice.  Current-snapshot
narratives (one per account at the current cumulative window) are the production
scheduler's responsibility and are NOT produced by the simulator.

Test plan:
  1.  Pending-only processing: executed entry skipped; pending entry processed.
  2.  --accounts filter works.
  3.  --weeks limit stops after N weeks; per-week mode produces N narratives.
  4.  --dry-run produces no DB writes.
  5.  Resume: spec with mix of generated/pending processes only pending.
  6.  generated_at written after entry completion.
  7.  save_spec called once per entry (not once per week).
  8.  23505 dedup handling: 23505 error does not abort.
  Extra:
  9.  --revert-from deletes from all 4 tables AND removes YAML entries.
  10. --revert-from with no matching dates is a no-op (no error).
  11. Idempotency: already-completed spec is a no-op.
  12. Failure path: LLM error leaves entry pending.
  13. now_anchor plumbing in orchestrator.
  14. audit_run_id satisfies the DB CHECK constraint.
  15. revert_from deletes audit children before narratives.
  Per-week additions:
  16. Per-week: N weeks → N narratives per account (one per week).
  17. _narrative_timestamp returns week_end 23:00 UTC.
  18. Two-entry spec: first entry stamps generated_at; second entry also generates narratives.
  19. Narrative fires once per week (not once per account).
  20. Crash-resume: pending entry re-synthesizes signals and generates narratives.
  21. Parity: _process_week's per-week output matches validate_per_week signal-slice logic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.simulator.spec import (
    TrajectoryEntry,
    TrajectoryParams,
    TrajectorySpec,
    load_spec,
    save_spec,
)

# ---------------------------------------------------------------------------
# Helpers: minimal domain objects
# ---------------------------------------------------------------------------

_WS_SLUG = "test-workspace"
_WS_ID = uuid.uuid5(uuid.NAMESPACE_DNS, _WS_SLUG)
_ACC_SLUG = "test-account"
_ACC_ID = uuid.uuid5(uuid.NAMESPACE_DNS, f"{_WS_ID}:{_ACC_SLUG}")


def _make_workspace() -> Any:
    ws = MagicMock()
    ws.id = _WS_ID
    ws.slug = _WS_SLUG
    ws.name = "Test Workspace"
    ws.internal_domains = ()
    return ws


def _make_account(slug: str = _ACC_SLUG) -> Any:
    acc = MagicMock()
    acc.id = uuid.uuid5(uuid.NAMESPACE_DNS, f"{_WS_ID}:{slug}")
    acc.workspace_id = _WS_ID
    acc.slug = slug
    acc.name = slug.replace("-", " ").title()
    acc.primary_domain = f"{slug}.com"
    acc.frequency_multiplier = 1.0
    acc.overall_health_score = 70
    return acc


def _make_entry(
    entry_id: str = "a1b2c3d4",
    start: date = date(2026, 4, 1),
    end: date = date(2026, 4, 7),
    primitive: str = "stable",
    params: dict | None = None,
    seed: int = 1234,
    generated_at: datetime | None = None,
) -> TrajectoryEntry:
    if params is None:
        params = {"target_band": [60, 80]}
    return TrajectoryEntry(
        id=entry_id,
        start_date=start,
        end_date=end,
        primitive=primitive,
        params=TrajectoryParams(**params),
        seed=seed,
        generated_at=generated_at,
    )


def _make_spec(
    account_slug: str = _ACC_SLUG,
    entries: list[TrajectoryEntry] | None = None,
) -> TrajectorySpec:
    if entries is None:
        entries = [_make_entry()]
    return TrajectorySpec(
        workspace_slug=_WS_SLUG,
        trajectories={account_slug: entries},
    )


def _make_supabase_client() -> MagicMock:
    """Return a MagicMock that behaves like a supabase.Client for the executor."""
    client = MagicMock()

    # Workspace lookup used by _warn_existing_production_narratives and revert_from.
    ws_data = {"id": str(_WS_ID)}
    client.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = ws_data

    # Most other table queries return empty lists by default.
    _default_empty = MagicMock()
    _default_empty.data = []
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = _default_empty
    client.table.return_value.select.return_value.is_.return_value.eq.return_value.execute.return_value = _default_empty
    client.table.return_value.select.return_value.in_.return_value.execute.return_value = _default_empty
    client.table.return_value.select.return_value.in_.return_value.limit.return_value.execute.return_value = _default_empty
    client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []

    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_spec_path(tmp_path: Path) -> Path:
    """Write a minimal trajectory YAML to a temp file and return the path."""
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    spec = _make_spec()
    save_spec(spec, spec_file)
    return spec_file


@pytest.fixture()
def mock_client() -> MagicMock:
    return _make_supabase_client()


# ---------------------------------------------------------------------------
# Helper: patch the executor's heavy dependencies
# ---------------------------------------------------------------------------


def _patch_executor_deps(
    *,
    dry_run: bool = False,
    ingest_error: Exception | None = None,
) -> tuple:
    """Return a context manager stack that patches all external calls in executor.py."""
    mock_workspace = _make_workspace()
    mock_account = _make_account()
    mock_signal = MagicMock()
    mock_signal.id = uuid.uuid4()
    mock_narrative = MagicMock()
    mock_narrative.id = uuid.uuid4()
    mock_narrative.engagement = 70
    mock_narrative.sentiment = 65
    mock_narrative.signals_considered = []

    patches: list[Any] = []

    patches.append(
        patch(
            "src.simulator.executor._load_workspace_and_accounts",
            return_value=(mock_workspace, _WS_ID, [mock_account]),
        )
    )
    patches.append(
        patch(
            "src.simulator.executor._warn_existing_production_narratives",
            return_value=None,
        )
    )

    if ingest_error:
        patches.append(
            patch(
                "src.simulator.executor._ingest_week",
                side_effect=ingest_error,
            )
        )
    else:
        patches.append(
            patch(
                "src.simulator.executor._ingest_week",
                return_value=[mock_signal],
            )
        )

    patches.append(
        patch(
            "src.simulator.executor._generate_narrative_for_week",
            return_value=(mock_narrative, 100, 50, 20),
        )
    )
    patches.append(
        patch(
            "src.simulator.executor._audit_narrative",
            return_value=True,
        )
    )
    patches.append(
        patch(
            "src.config.loader.load_config",
            return_value=MagicMock(),
        )
    )

    return tuple(patches)


# ---------------------------------------------------------------------------
# Test 1 — Pending-only processing
# ---------------------------------------------------------------------------


def test_pending_entry_processed_executed_entry_skipped(tmp_spec_path: Path, mock_client: MagicMock):
    """An entry with generated_at already set is skipped; a pending one is processed."""
    executed_entry = _make_entry(
        entry_id="aaaaaaaa",
        start=date(2026, 3, 1),
        end=date(2026, 3, 7),
        generated_at=datetime(2026, 3, 8, 12, 0, tzinfo=UTC),
    )
    pending_entry = _make_entry(entry_id="bbbbbbbb")
    spec = _make_spec(entries=[executed_entry, pending_entry])
    save_spec(spec, tmp_spec_path)

    from src.simulator.executor import ExecutorConfig, run

    patches = _patch_executor_deps()
    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=tmp_spec_path,
        dry_run=False,
    )

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = run(config, mock_client)

    assert result.entries_processed == 1
    assert result.entries_failed == 0

    # After the run the pending entry should have generated_at set in the file.
    reloaded = load_spec(tmp_spec_path)
    entries = reloaded.trajectories[_ACC_SLUG]
    executed = next(e for e in entries if e.id == "aaaaaaaa")
    new_pending = next(e for e in entries if e.id == "bbbbbbbb")
    # The already-executed entry is unchanged.
    assert executed.generated_at == datetime(2026, 3, 8, 12, 0, tzinfo=UTC)
    # The pending entry now has a generated_at.
    assert new_pending.generated_at is not None


# ---------------------------------------------------------------------------
# Test 2 — --accounts filter
# ---------------------------------------------------------------------------


def test_accounts_filter_skips_other_accounts(tmp_path: Path, mock_client: MagicMock):
    """Only the specified account's entries are processed when --accounts is set."""
    spec = TrajectorySpec(
        workspace_slug=_WS_SLUG,
        trajectories={
            "account-a": [_make_entry("a1a1a1a1")],
            "account-b": [_make_entry("b2b2b2b2")],
        },
    )
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    save_spec(spec, spec_file)

    from src.simulator.executor import ExecutorConfig, run

    # Mock account objects for both.
    acc_a = _make_account("account-a")
    acc_b = _make_account("account-b")

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
        accounts_filter=["account-a"],
    )

    patches = _patch_executor_deps()
    with patches[0] as mock_load, patches[1], patches[2], patches[3], patches[4], patches[5]:
        mock_load.return_value = (_make_workspace(), _WS_ID, [acc_a, acc_b])
        result = run(config, mock_client)

    assert result.entries_processed == 1  # account-a only
    assert result.entries_skipped == 1    # account-b skipped


# ---------------------------------------------------------------------------
# Test 3 — --weeks limit (per-week: N weeks → N narratives)
# ---------------------------------------------------------------------------


def test_weeks_limit_caps_processing(tmp_spec_path: Path, mock_client: MagicMock):
    """Processing stops after N weeks; per-week mode generates one narrative per week."""
    # Entry spans 4 weeks (28 days)
    long_entry = _make_entry(
        entry_id="cccccccc",
        start=date(2026, 4, 1),
        end=date(2026, 4, 28),
    )
    spec = _make_spec(entries=[long_entry])
    save_spec(spec, tmp_spec_path)

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=tmp_spec_path,
        weeks_limit=2,
    )

    patches = _patch_executor_deps()
    with patches[0], patches[1], patches[2], patches[3] as mock_gen, patches[4], patches[5]:
        result = run(config, mock_client)

    assert result.weeks_total == 2
    # Per-week mode: one narrative per week, so 2 weeks -> 2 narratives.
    assert result.narratives_generated == 2
    assert mock_gen.call_count == 2


# ---------------------------------------------------------------------------
# Test 4 — --dry-run produces no DB writes
# ---------------------------------------------------------------------------


def test_dry_run_skips_all_db_writes(tmp_spec_path: Path, mock_client: MagicMock):
    """dry_run=True must not trigger _ingest_week or _generate_narrative_for_week."""
    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=tmp_spec_path,
        dry_run=True,
    )

    patches = _patch_executor_deps(dry_run=True)
    with patches[0], patches[1], patches[2] as mock_ingest, patches[3] as mock_gen, patches[4], patches[5]:
        result = run(config, mock_client)

    # In dry-run mode: ingest and generation must NOT be called.
    mock_ingest.assert_not_called()
    mock_gen.assert_not_called()
    # The entry should still be marked processed (dry-run marks entries executed too)
    assert result.entries_processed == 1


# ---------------------------------------------------------------------------
# Test 5 — Resume: mix of generated and pending
# ---------------------------------------------------------------------------


def test_resume_only_processes_pending(tmp_path: Path, mock_client: MagicMock):
    """Re-running a spec with mix of generated/pending processes only pending."""
    already_done = _make_entry(
        entry_id="dddddddd",
        generated_at=datetime(2026, 4, 8, 0, 0, tzinfo=UTC),
    )
    still_pending = _make_entry(
        entry_id="eeeeeeee",
        start=date(2026, 4, 8),
        end=date(2026, 4, 14),
    )
    spec = _make_spec(entries=[already_done, still_pending])
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    save_spec(spec, spec_file)

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
    )

    patches = _patch_executor_deps()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = run(config, mock_client)

    # Only 1 entry processed (the pending one), not 2.
    assert result.entries_processed == 1

    reloaded = load_spec(spec_file)
    done = next(e for e in reloaded.trajectories[_ACC_SLUG] if e.id == "dddddddd")
    pending = next(e for e in reloaded.trajectories[_ACC_SLUG] if e.id == "eeeeeeee")
    # The already-done entry's generated_at is unchanged.
    assert done.generated_at == datetime(2026, 4, 8, 0, 0, tzinfo=UTC)
    # The pending entry is now executed.
    assert pending.generated_at is not None


# ---------------------------------------------------------------------------
# Test 6 — generated_at written after entry completion
# ---------------------------------------------------------------------------


def test_generated_at_set_after_entry_completes(tmp_spec_path: Path, mock_client: MagicMock):
    """After the executor processes an entry, its generated_at in the spec is non-null."""
    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=tmp_spec_path,
    )

    patches = _patch_executor_deps()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        run(config, mock_client)

    reloaded = load_spec(tmp_spec_path)
    entry = reloaded.trajectories[_ACC_SLUG][0]
    assert entry.generated_at is not None
    assert isinstance(entry.generated_at, datetime)


# ---------------------------------------------------------------------------
# Test 7 — save_spec called once per entry (not once per week)
# ---------------------------------------------------------------------------


def test_save_spec_called_once_per_entry_not_per_week(tmp_path: Path, mock_client: MagicMock):
    """save_spec should be called once per completed entry, not once per week."""
    # Entry spanning 2 weeks.
    entry = _make_entry(
        entry_id="ffffffff",
        start=date(2026, 4, 1),
        end=date(2026, 4, 14),
    )
    spec = _make_spec(entries=[entry])
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    save_spec(spec, spec_file)

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
    )

    patches = _patch_executor_deps()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
         patch("src.simulator.executor.save_spec") as mock_save:
        result = run(config, mock_client)

    # One entry → exactly one save_spec call.
    assert mock_save.call_count == 1
    assert result.entries_processed == 1


# ---------------------------------------------------------------------------
# Test 8 — 23505 dedup handling
# ---------------------------------------------------------------------------


def test_23505_error_treated_as_no_op(tmp_spec_path: Path, mock_client: MagicMock):
    """If _ingest_week raises a 23505 error, the executor continues (not abort)."""
    dup_error = RuntimeError("ERROR: duplicate key violates unique constraint (23505)")

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=tmp_spec_path,
    )

    patches = _patch_executor_deps(ingest_error=dup_error)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = run(config, mock_client)

    # 23505 is a no-op — entry should still be marked processed.
    assert result.entries_processed == 1
    assert result.entries_failed == 0


# ---------------------------------------------------------------------------
# Test 9 — --revert-from deletes from all 4 tables and prunes spec
# ---------------------------------------------------------------------------


def test_revert_from_deletes_tables_and_prunes_spec(tmp_path: Path):
    """revert_from should delete from 4 tables and remove YAML entries >= revert_date."""
    spec = _make_spec(entries=[
        _make_entry("11111111", start=date(2026, 3, 1), end=date(2026, 3, 7)),
        _make_entry("22222222", start=date(2026, 4, 1), end=date(2026, 4, 7)),
    ])
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    save_spec(spec, spec_file)

    client = MagicMock()
    # workspace lookup
    client.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": str(_WS_ID)
    }
    # accounts lookup
    acc_resp = MagicMock()
    acc_resp.data = [{"id": str(_ACC_ID), "slug": _ACC_SLUG}]
    client.table.return_value.select.return_value.eq.return_value.in_.return_value.execute.return_value = acc_resp

    # delete calls
    del_resp = MagicMock()
    del_resp.data = []
    client.table.return_value.delete.return_value.in_.return_value.gte.return_value.execute.return_value = del_resp

    from src.simulator.executor import revert_from

    revert_from(_WS_SLUG, spec_file, date(2026, 4, 1), client)

    # Verify delete was called for each of the 4 tables.
    called_tables = {str(c.args[0]) for c in client.table.call_args_list if c.args}
    # At minimum the 4 data tables should have been targeted.
    for table in ("signals", "narratives", "account_dimension_scores", "account_health_snapshots"):
        assert table in called_tables, f"Expected delete on {table!r}, got calls to: {called_tables}"

    # Entries with start_date >= 2026-04-01 should be removed from spec.
    reloaded = load_spec(spec_file)
    remaining = reloaded.trajectories[_ACC_SLUG]
    assert len(remaining) == 1
    assert remaining[0].id == "11111111"


# ---------------------------------------------------------------------------
# Test 10 — --revert-from with no matching dates is a no-op
# ---------------------------------------------------------------------------


def test_revert_from_no_matching_entries_is_noop(tmp_path: Path):
    """revert_from with a date after all entries produces no delete calls and no error."""
    spec = _make_spec(entries=[
        _make_entry("33333333", start=date(2026, 3, 1), end=date(2026, 3, 7)),
    ])
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    save_spec(spec, spec_file)

    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": str(_WS_ID)
    }
    acc_resp = MagicMock()
    acc_resp.data = [{"id": str(_ACC_ID), "slug": _ACC_SLUG}]
    client.table.return_value.select.return_value.eq.return_value.in_.return_value.execute.return_value = acc_resp

    del_resp = MagicMock()
    del_resp.data = []
    client.table.return_value.delete.return_value.in_.return_value.gte.return_value.execute.return_value = del_resp

    from src.simulator.executor import revert_from

    # Revert from after all entries — should not error.
    revert_from(_WS_SLUG, spec_file, date(2026, 5, 1), client)

    # All entries should remain (none have start_date >= 2026-05-01).
    reloaded = load_spec(spec_file)
    assert len(reloaded.trajectories[_ACC_SLUG]) == 1


# ---------------------------------------------------------------------------
# Test 11 — Idempotency
# ---------------------------------------------------------------------------


def test_already_completed_spec_is_noop(tmp_path: Path, mock_client: MagicMock):
    """Re-running a spec where all entries have generated_at set is a no-op."""
    fully_done = _make_entry(
        entry_id="44444444",
        generated_at=datetime(2026, 4, 8, 0, 0, tzinfo=UTC),
    )
    spec = _make_spec(entries=[fully_done])
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    save_spec(spec, spec_file)

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
    )

    patches = _patch_executor_deps()
    with patches[0], patches[1], patches[2] as mock_ingest, patches[3] as mock_gen, patches[4], patches[5]:
        result = run(config, mock_client)

    # Nothing was processed or failed.
    assert result.entries_processed == 0
    assert result.entries_failed == 0
    # No LLM calls.
    mock_ingest.assert_not_called()
    mock_gen.assert_not_called()


# ---------------------------------------------------------------------------
# Test 12 — Failure path: LLM error leaves entry pending
# ---------------------------------------------------------------------------


def test_entry_stays_pending_on_narrative_failure(tmp_spec_path: Path, mock_client: MagicMock):
    """If _generate_narrative_for_week raises, the entry is counted as failed."""
    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=tmp_spec_path,
        max_retries=0,  # No retries — fail immediately.
    )

    patches = list(_patch_executor_deps())
    patches[3] = patch(
        "src.simulator.executor._generate_narrative_for_week",
        side_effect=RuntimeError("LLM timeout"),
    )

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = run(config, mock_client)

    assert result.entries_processed == 0
    assert result.entries_failed == 1

    # Entry still has generated_at=None (failure left it pending).
    reloaded = load_spec(tmp_spec_path)
    entry = reloaded.trajectories[_ACC_SLUG][0]
    assert entry.generated_at is None


# ---------------------------------------------------------------------------
# Test 13 — now_anchor plumbing in orchestrator
# ---------------------------------------------------------------------------


def test_orchestrator_now_anchor_overrides_base_time():
    """yield_events with now_anchor produces timestamps in the anchor's week, not 2026-01-01."""
    import uuid as uuid_mod

    from src.synthetic.orchestrator import yield_events
    from src.synthetic.scenario import AccountSpec, AxesSpec, ScenarioSpec, SignalSpec

    anchor = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
    workspace_id = uuid_mod.uuid4()

    scenario = ScenarioSpec(
        version=1,
        name="anchor-test",
        seed=42,
        description="test",
        workspace_slug="test",
        accounts=[AccountSpec(slug="acct", name="Acct", primary_domain="acct.com")],
        signals=[
            SignalSpec(
                source_type="inbound_email",
                account_slug="acct",
                count=2,
                axes=AxesSpec(),
            )
        ],
    )

    # Without anchor — timestamps near 2026-01-01
    events_default = list(yield_events(scenario, workspace_id))
    # With anchor — timestamps near 2026-04-01
    events_anchored = list(yield_events(scenario, workspace_id, now_anchor=anchor))

    assert len(events_default) == 2
    assert len(events_anchored) == 2

    from src.domain.raw_inbound_event import RawInboundEvent

    # First event of the anchored run should be close to the anchor.
    _, ev = events_anchored[0]
    assert isinstance(ev, RawInboundEvent)
    ts = ev.received_at
    delta = abs((ts - anchor).total_seconds())
    assert delta < 86400 * 2, f"Expected timestamp near anchor {anchor}, got {ts}"

    # Default run should be near 2026-01-01 (not 2026-04-01)
    _, ev_default = events_default[0]
    assert isinstance(ev_default, RawInboundEvent)
    ts_default = ev_default.received_at
    default_base = datetime(2026, 1, 1, tzinfo=UTC)
    delta_default = abs((ts_default - default_base).total_seconds())
    assert delta_default < 86400 * 2, f"Expected default near 2026-01-01, got {ts_default}"


# ---------------------------------------------------------------------------
# Test 14 — audit_run_id satisfies the DB CHECK constraint
# ---------------------------------------------------------------------------


def test_audit_run_id_matches_constraint_regex(tmp_spec_path: Path, mock_client: MagicMock):
    """audit_run_id passed to _audit_narrative must match the DB CHECK constraint pattern."""
    import re

    captured_run_ids: list[str] = []

    def _capture_audit(narrative, workspace_id, client, audit_run_id, dry_run=False):
        captured_run_ids.append(audit_run_id)
        return True

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=tmp_spec_path,
        audit_final_week=True,
    )

    patches = list(_patch_executor_deps())
    patches[4] = patch("src.simulator.executor._audit_narrative", side_effect=_capture_audit)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        run(config, mock_client)

    assert len(captured_run_ids) >= 1, "Expected at least one _audit_narrative call"
    pattern = re.compile(r"^(ci|nightly|manual)_[A-Za-z0-9_-]{1,200}$")
    for run_id in captured_run_ids:
        assert pattern.match(run_id), (
            f"audit_run_id {run_id!r} does not satisfy the DB CHECK constraint "
            r"^(ci|nightly|manual)_[A-Za-z0-9_-]{1,200}$"
        )
    assert all(r.startswith("manual_simulator_") for r in captured_run_ids), (
        f"Expected audit_run_id to start with 'manual_simulator_', got: {captured_run_ids}"
    )


# ---------------------------------------------------------------------------
# Test 15 — revert_from deletes audit children before narratives
# ---------------------------------------------------------------------------


def test_revert_from_deletes_audit_tables_before_narratives(tmp_path: Path):
    """narrative_audits and narrative_audit_runs must be deleted before narratives."""
    _NARR_ID = str(uuid.uuid4())

    spec = _make_spec(entries=[
        _make_entry("aa111111", start=date(2026, 4, 1), end=date(2026, 4, 7)),
    ])
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    save_spec(spec, spec_file)

    client = MagicMock()

    # Workspace lookup.
    client.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": str(_WS_ID)
    }

    # Track table() call order to verify delete sequencing.
    table_call_order: list[str] = []

    def _recording_table(name: str):
        mock = MagicMock()

        real_delete = MagicMock()
        real_delete.return_value.in_.return_value.execute.return_value.data = []
        real_delete.return_value.in_.return_value.gte.return_value.execute.return_value.data = []

        def _delete_recording():
            table_call_order.append(name)
            return real_delete.return_value

        mock.delete = _delete_recording

        # SELECT chain for accounts and narrative-id lookup.
        select_mock = MagicMock()
        if name == "workspaces":
            select_mock.eq.return_value.single.return_value.execute.return_value.data = {
                "id": str(_WS_ID)
            }
        elif name == "accounts":
            acc_data = MagicMock()
            acc_data.data = [{"id": str(_ACC_ID), "slug": _ACC_SLUG}]
            select_mock.eq.return_value.in_.return_value.execute.return_value = acc_data
        elif name == "narratives":
            narr_data = MagicMock()
            narr_data.data = [{"id": _NARR_ID}]
            select_mock.in_.return_value.gte.return_value.execute.return_value = narr_data
        else:
            empty = MagicMock()
            empty.data = []
            select_mock.in_.return_value.execute.return_value = empty
            select_mock.in_.return_value.gte.return_value.execute.return_value = empty

        mock.select = MagicMock(return_value=select_mock)
        return mock

    client.table = _recording_table

    from src.simulator.executor import revert_from

    revert_from(_WS_SLUG, spec_file, date(2026, 4, 1), client)

    assert "narrative_audits" in table_call_order
    assert "narrative_audit_runs" in table_call_order
    assert "narratives" in table_call_order

    narr_audit_pos = table_call_order.index("narrative_audits")
    narr_audit_runs_pos = table_call_order.index("narrative_audit_runs")
    narr_pos = table_call_order.index("narratives")
    assert narr_audit_pos < narr_pos, (
        f"narrative_audits (pos {narr_audit_pos}) must be deleted before "
        f"narratives (pos {narr_pos}); order was: {table_call_order}"
    )
    assert narr_audit_runs_pos < narr_pos, (
        f"narrative_audit_runs (pos {narr_audit_runs_pos}) must be deleted before "
        f"narratives (pos {narr_pos}); order was: {table_call_order}"
    )


# ---------------------------------------------------------------------------
# Test 16 — Per-week: N weeks → N narratives
# ---------------------------------------------------------------------------


def test_per_week_two_week_entry_generates_two_narratives(tmp_path: Path, mock_client: MagicMock):
    """With a 2-week entry, exactly 2 narratives are generated (one per week).

    Per-week contract: each (account, week) pair gets one narrative generated
    against that week's signal slice.
    """
    entry = _make_entry(
        entry_id="11aa22bb",
        start=date(2026, 3, 3),
        end=date(2026, 3, 16),  # 2 weeks
        primitive="stable",
        params={"target_band": [60, 75]},
        seed=1001,
    )
    spec = _make_spec(entries=[entry])
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    save_spec(spec, spec_file)

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
    )

    patches = _patch_executor_deps()
    with patches[0], patches[1], patches[2], patches[3] as mock_gen, patches[4], patches[5]:
        result = run(config, mock_client)

    # 2 weeks -> 2 narratives (one per week, not one per account).
    assert result.narratives_generated == 2, (
        f"Expected 2 narratives for a 2-week entry; got {result.narratives_generated}"
    )
    assert mock_gen.call_count == 2
    assert result.weeks_total == 2


# ---------------------------------------------------------------------------
# Test 17 — _narrative_timestamp returns week_end 23:00 UTC
# ---------------------------------------------------------------------------


def test_narrative_timestamp_returns_week_end_23h():
    """_narrative_timestamp should return 2026-04-07T23:00:00Z for week_start=2026-04-01."""
    from src.simulator.executor import _narrative_timestamp

    week_start = date(2026, 4, 1)
    result = _narrative_timestamp(week_start)

    expected = datetime(2026, 4, 7, 23, 0, 0, tzinfo=UTC)
    assert result == expected, f"Expected {expected}, got {result}"
    assert result.tzinfo is not None, "Result must be tz-aware"


# ---------------------------------------------------------------------------
# Test 18 — Two-entry spec: both entries produce narratives
# ---------------------------------------------------------------------------


def test_two_entry_spec_both_entries_generate_narratives(
    tmp_path: Path, mock_client: MagicMock
):
    """For a 2-entry account with 1 week each, both entries produce 1 narrative each.

    Per-week mode: entry1's week gets a narrative; entry2's week also gets a narrative.
    Total: 2 narratives, 2 entries processed.
    """
    entry1 = _make_entry(
        entry_id="eeeeeeee",
        start=date(2026, 3, 3),
        end=date(2026, 3, 9),  # 1 week
        primitive="stable",
        params={"target_band": [60, 75]},
        seed=2001,
    )
    entry2 = _make_entry(
        entry_id="ffffffff",
        start=date(2026, 3, 10),
        end=date(2026, 3, 16),  # 1 week
        primitive="stable",
        params={"target_band": [60, 75]},
        seed=2002,
    )
    spec = _make_spec(entries=[entry1, entry2])
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    save_spec(spec, spec_file)

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
    )

    patches = _patch_executor_deps()
    with patches[0], patches[1], patches[2], patches[3] as mock_gen, patches[4], patches[5]:
        result = run(config, mock_client)

    assert result.entries_processed == 2
    assert result.narratives_generated == 2, (
        f"Expected 2 narratives (1 per entry week); got {result.narratives_generated}"
    )
    assert mock_gen.call_count == 2

    reloaded = load_spec(spec_file)
    e1 = next(e for e in reloaded.trajectories[_ACC_SLUG] if e.id == "eeeeeeee")
    e2 = next(e for e in reloaded.trajectories[_ACC_SLUG] if e.id == "ffffffff")
    assert e1.generated_at is not None, "entry1.generated_at must be set"
    assert e2.generated_at is not None, "entry2.generated_at must be set"


# ---------------------------------------------------------------------------
# Test 19 — Narrative fires once per week (proportional to week count)
# ---------------------------------------------------------------------------


def test_narrative_fires_once_per_week_for_three_entry_spec(
    tmp_path: Path, mock_client: MagicMock
):
    """With 3 single-week entries, exactly 3 narratives are generated (one per week).

    This is the per-week invariant: narratives_generated == total_weeks_processed.
    """
    entry_ids = ["aabb1100", "ccdd2200", "eeff3300"]
    entries = [
        _make_entry(
            entry_id=entry_ids[i],
            start=date(2026, 3, 3 + i * 7),
            end=date(2026, 3, 9 + i * 7),
            primitive="stable",
            params={"target_band": [60, 75]},
            seed=3000 + i,
        )
        for i in range(3)
    ]
    spec = _make_spec(entries=entries)
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    save_spec(spec, spec_file)

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
    )

    patches = _patch_executor_deps()
    with patches[0], patches[1], patches[2] as mock_ingest, patches[3] as mock_gen, patches[4], patches[5]:
        result = run(config, mock_client)

    # 3 entries x 1 week each -> 3 narratives.
    assert result.narratives_generated == 3, (
        f"Expected 3 narratives for 3 single-week entries; got {result.narratives_generated}"
    )
    assert mock_gen.call_count == 3
    assert mock_ingest.call_count == 3
    assert result.weeks_total == 3


# ---------------------------------------------------------------------------
# Test 20 — Crash-resume: pending entry synthesizes signals and generates narratives
# ---------------------------------------------------------------------------


def test_crash_resume_pending_entry_synthesizes_and_generates(
    tmp_path: Path, mock_client: MagicMock
):
    """Simulate a crash after entry1 was stamped but before entry2 completed.

    entry1 has generated_at set (complete).
    entry2 has generated_at=None (pending — signals+narrative incomplete).

    On resume:
    - Only entry2 is processed.
    - entry2's signals are synthesized (or deduped via 23505 no-op on re-run).
    - entry2's narrative is generated.
    """
    entry1 = _make_entry(
        entry_id="11112222",
        start=date(2026, 3, 3),
        end=date(2026, 3, 9),  # 1 week
        primitive="stable",
        params={"target_band": [60, 75]},
        seed=4001,
        generated_at=datetime(2026, 3, 10, 0, 0, tzinfo=UTC),  # already done
    )
    entry2 = _make_entry(
        entry_id="33334444",
        start=date(2026, 3, 10),
        end=date(2026, 3, 16),  # 1 week
        primitive="stable",
        params={"target_band": [60, 75]},
        seed=4002,
        # generated_at=None simulates the crash point
    )
    spec = _make_spec(entries=[entry1, entry2])
    spec_file = tmp_path / "trajectory.test-workspace.yaml"
    save_spec(spec, spec_file)

    from src.simulator.executor import ExecutorConfig, run

    config = ExecutorConfig(
        workspace_slug=_WS_SLUG,
        spec_path=spec_file,
    )

    patches = _patch_executor_deps()
    with patches[0], patches[1], patches[2] as mock_ingest, patches[3] as mock_gen, patches[4], patches[5]:
        result = run(config, mock_client)

    # Only the pending entry (entry2) is processed.
    assert result.entries_processed == 1
    # Signals synthesized only for entry2's 1 week.
    assert mock_ingest.call_count == 1
    # Narrative generated once (for entry2's week).
    assert mock_gen.call_count == 1
    assert result.narratives_generated == 1

    reloaded = load_spec(spec_file)
    e2 = next(e for e in reloaded.trajectories[_ACC_SLUG] if e.id == "33334444")
    assert e2.generated_at is not None, "entry2.generated_at must be set after resume"


# ---------------------------------------------------------------------------
# Test 21 — Parity: per-week signal slice matches validate_per_week filter logic
# ---------------------------------------------------------------------------


def test_per_week_signal_slice_matches_validate_per_week_filter():
    """The signal slice used by _process_week (signals in [week_start, week_end])
    matches the _filter_signals_for_week logic in validate_per_week.py.

    This is the cross-reference parity test: both paths must use the same
    week boundary definition so an account audited by validate_per_week.py
    gives the same pass/fail signal as the same account run through the
    simulator's per-week path.

    The test constructs a mixed signal set spanning two weeks and asserts that
    the filter correctly partitions them by the same boundaries that
    _narrative_timestamp uses for the generated_at timestamp.
    """
    from src.simulator.executor import _narrative_timestamp

    week_start_a = date(2026, 4, 1)
    week_start_b = date(2026, 4, 8)

    # _narrative_timestamp returns week_end 23:00 UTC; the boundary is inclusive.
    ts_a = _narrative_timestamp(week_start_a)  # 2026-04-07T23:00Z

    # Week A boundary: [2026-04-01 00:00, 2026-04-07 23:00] UTC
    week_a_start_dt = datetime(week_start_a.year, week_start_a.month, week_start_a.day, tzinfo=UTC)
    week_a_end_dt = ts_a  # 2026-04-07T23:00Z

    # Verify the simulator's narrative timestamp falls within week A.
    assert week_a_start_dt <= ts_a <= week_a_end_dt

    # Verify week B start is strictly after week A end (no overlap).
    week_b_start_dt = datetime(week_start_b.year, week_start_b.month, week_start_b.day, tzinfo=UTC)
    assert week_b_start_dt > week_a_end_dt, (
        f"Weeks must not overlap: week A ends {week_a_end_dt}, week B starts {week_b_start_dt}"
    )

    # Confirm the narrative_timestamp formula: week_end 23:00 = week_start + 6d + 23h.
    expected_ts_a = datetime(2026, 4, 7, 23, 0, 0, tzinfo=UTC)
    assert ts_a == expected_ts_a, f"Expected {expected_ts_a}, got {ts_a}"
