"""
Unit tests for src/db/workspaces.py.
All tests mock the Supabase client — no live DB required.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID

from src.db.workspaces import get_workspace_by_id, get_workspace_by_slug
from src.domain.workspace import Workspace

_NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_WS_ID = UUID("00000000-0000-0000-0000-000000000001")
_ORG_ID = UUID("00000000-0000-0000-0000-000000000002")

_WS_ROW = {
    "id": str(_WS_ID),
    "organization_id": str(_ORG_ID),
    "slug": "quantas-labs",
    "name": "Quantas Labs",
    "internal_domains": ["quantaslabs.com"],
    "crm_url_template": None,
    "crm_portal_id": None,
    "outbound_sender_email": "cs@quantaslabs.com",
    "outbound_sender_name": "Quantas Labs CS",
    "created_at": _NOW.isoformat(),
    "updated_at": _NOW.isoformat(),
    "deleted_at": None,
}


def _mock_slug_chain(data: list[dict]) -> MagicMock:
    """Build mock for get_workspace_by_slug: table.select.eq.is_.execute.data"""
    m = MagicMock()
    terminal = m.table.return_value.select.return_value.eq.return_value.is_.return_value
    terminal.execute.return_value.data = data
    return m


def _mock_id_chain(data: list[dict]) -> MagicMock:
    """Build mock for get_workspace_by_id: same chain shape."""
    return _mock_slug_chain(data)


# ── get_workspace_by_slug ─────────────────────────────────────────────────────


def test_get_workspace_by_slug_returns_workspace():
    client = _mock_slug_chain([_WS_ROW])
    result = get_workspace_by_slug(client, "quantas-labs")
    assert isinstance(result, Workspace)
    assert result.slug == "quantas-labs"
    assert result.outbound_sender_email == "cs@quantaslabs.com"


def test_get_workspace_by_slug_filters_deleted_at():
    """Verify the query includes .is_('deleted_at', 'null')."""
    client = _mock_slug_chain([_WS_ROW])
    get_workspace_by_slug(client, "quantas-labs")
    is_call = client.table.return_value.select.return_value.eq.return_value.is_
    is_call.assert_called_once_with("deleted_at", "null")


def test_get_workspace_by_slug_returns_none_when_empty():
    client = _mock_slug_chain([])
    result = get_workspace_by_slug(client, "nonexistent")
    assert result is None


# ── get_workspace_by_id ───────────────────────────────────────────────────────


def test_get_workspace_by_id_returns_workspace():
    client = _mock_id_chain([_WS_ROW])
    result = get_workspace_by_id(client, _WS_ID)
    assert isinstance(result, Workspace)
    assert result.id == _WS_ID
    assert result.name == "Quantas Labs"
