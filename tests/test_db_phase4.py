"""
Unit tests for Phase 4 DB modules: dimension_configs, dimension_scores, health_snapshots.
All tests mock the Supabase client — no live DB required.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from src.db.dimension_configs import get_dimension_configs, seed_dimension_configs
from src.db.dimension_scores import get_current_scores, supersede_dimension_score
from src.db.health_snapshots import supersede_health_snapshot
from src.domain.dimension_config import DimensionConfig

_NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_WS_ID = UUID("00000000-0000-0000-0000-000000000001")
_DIM_ID = UUID("00000000-0000-0000-0000-000000000002")
_ACC_ID = UUID("00000000-0000-0000-0000-000000000003")

_DIM_ROW = {
    "id": str(_DIM_ID),
    "workspace_id": str(_WS_ID),
    "dimension_type": "email",
    "name": "Email Health",
    "weight": 0.7,
    "enabled": True,
    "config": {"email_score_source": "engagement"},
    "created_at": _NOW.isoformat(),
    "updated_at": _NOW.isoformat(),
    "deleted_at": None,
}


def _mock_chain_data(data: list[dict]) -> MagicMock:
    """Build a mock client where any fluent chain ending in .execute().data returns `data`."""
    m = MagicMock()
    # MagicMock returns a consistent child for each attribute; setting .data on the
    # terminal execute node covers all read chains regardless of how many .eq()/.is_() calls.
    m.table.return_value.select.return_value.eq.return_value.is_.return_value.execute.return_value.data = data  # noqa: E501
    m.table.return_value.select.return_value.eq.return_value.eq.return_value.is_.return_value.execute.return_value.data = data  # noqa: E501
    return m


# ── dimension_configs ─────────────────────────────────────────────────────────


def test_get_dimension_configs_returns_list():
    client = _mock_chain_data([_DIM_ROW])
    results = get_dimension_configs(client, _WS_ID)
    assert len(results) == 1
    assert isinstance(results[0], DimensionConfig)
    assert results[0].dimension_type == "email"
    assert results[0].enabled is True


def test_get_dimension_configs_empty():
    client = _mock_chain_data([])
    results = get_dimension_configs(client, _WS_ID)
    assert results == []


def test_seed_dimension_configs_skips_existing():
    from src.config.schema import DimensionScoringConfig

    existing = DimensionConfig(
        id=_DIM_ID,
        workspace_id=_WS_ID,
        dimension_type="email",
        name="Email Health",
        weight=0.7,
        enabled=True,
        config={},
        created_at=_NOW,
        updated_at=_NOW,
        deleted_at=None,
    )
    new_dim = DimensionScoringConfig(dimension_type="email", name="Email Health", weight=0.7, enabled=True)  # noqa: E501
    with patch("src.db.dimension_configs.get_dimension_configs", return_value=[existing]):
        client = MagicMock()
        seed_dimension_configs(client, _WS_ID, [new_dim])
        client.table.assert_not_called()


def test_seed_dimension_configs_inserts_new():
    from src.config.schema import DimensionScoringConfig

    new_dim = DimensionScoringConfig(dimension_type="csm_score", name="CSM Score", weight=0.3, enabled=True)  # noqa: E501
    with patch("src.db.dimension_configs.get_dimension_configs", return_value=[]):
        client = MagicMock()
        seed_dimension_configs(client, _WS_ID, [new_dim])
        client.table.assert_called_once_with("health_dimension_configs")


# ── dimension_scores ──────────────────────────────────────────────────────────


def test_get_current_scores_empty():
    client = _mock_chain_data([])
    results = get_current_scores(client, _WS_ID, _ACC_ID)
    assert results == []


def test_supersede_dimension_score_returns_count():
    client = MagicMock()
    terminal = client.table.return_value.update.return_value.eq.return_value.eq.return_value.is_.return_value.execute.return_value  # noqa: E501
    terminal.data = [{"id": str(uuid4())}]
    count = supersede_dimension_score(client, _ACC_ID, _DIM_ID, _NOW)
    assert count == 1


def test_supersede_dimension_score_no_rows():
    client = MagicMock()
    terminal = client.table.return_value.update.return_value.eq.return_value.eq.return_value.is_.return_value.execute.return_value  # noqa: E501
    terminal.data = []
    count = supersede_dimension_score(client, _ACC_ID, _DIM_ID, _NOW)
    assert count == 0


# ── health_snapshots ──────────────────────────────────────────────────────────


def test_supersede_health_snapshot_returns_count():
    client = MagicMock()
    terminal = client.table.return_value.update.return_value.eq.return_value.is_.return_value.execute.return_value  # noqa: E501
    terminal.data = [{"id": str(uuid4())}]
    count = supersede_health_snapshot(client, _ACC_ID, _NOW)
    assert count == 1


def test_supersede_health_snapshot_no_rows():
    client = MagicMock()
    terminal = client.table.return_value.update.return_value.eq.return_value.is_.return_value.execute.return_value  # noqa: E501
    terminal.data = []
    count = supersede_health_snapshot(client, _ACC_ID, _NOW)
    assert count == 0
