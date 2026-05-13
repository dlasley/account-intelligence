"""Unit tests for src.db.api_keys: verify_api_key + generate_key."""

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.db.api_keys import generate_key, verify_api_key


def _mock_client_with_row(row: dict | None) -> MagicMock:
    """Build a Supabase client mock whose select chain returns the given row(s)."""
    client = MagicMock()
    chain = client.table.return_value.select.return_value.eq.return_value
    terminal = chain.is_.return_value.is_.return_value.limit.return_value
    terminal.execute.return_value.data = [row] if row else []
    return client


_API_KEY_ID = uuid4()
_WS_ID = uuid4()


def _row(scopes: list[str], *, expires_at: str | None = None) -> dict:
    return {
        "id": str(_API_KEY_ID),
        "workspace_id": str(_WS_ID),
        "key_prefix": "pk_live_abcdefghijklmnop",
        "scopes": scopes,
        "owner_user_id": None,
        "owner_service_account_id": str(uuid4()),
        "expires_at": expires_at,
    }


def test_verify_valid_key_returns_info():
    client = _mock_client_with_row(_row(["ingest"]))
    full = "pk_live_" + "a" * 32
    info = verify_api_key(client, full, required_scope="ingest")
    assert info.id == _API_KEY_ID
    assert info.workspace_id == _WS_ID
    assert "ingest" in info.scopes


def test_verify_malformed_key_raises():
    client = _mock_client_with_row(None)
    with pytest.raises(ValueError, match="malformed"):
        verify_api_key(client, "no_prefix_here", required_scope="ingest")


def test_verify_unknown_key_raises():
    client = _mock_client_with_row(None)
    full = "pk_live_" + "a" * 32
    with pytest.raises(ValueError, match="invalid key"):
        verify_api_key(client, full, required_scope="ingest")


def test_verify_expired_key_raises():
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    client = _mock_client_with_row(_row(["ingest"], expires_at=past))
    full = "pk_live_" + "a" * 32
    with pytest.raises(ValueError, match="expired"):
        verify_api_key(client, full, required_scope="ingest")


def test_verify_wrong_scope_raises_permission_error():
    client = _mock_client_with_row(_row(["read"]))
    full = "pk_live_" + "a" * 32
    with pytest.raises(PermissionError, match="ingest"):
        verify_api_key(client, full, required_scope="ingest")


def test_generate_key_uses_pub_prefix_for_ingest():
    full, prefix, _key_hash = generate_key("ingest")
    assert full.startswith("pk_live_")
    assert prefix.startswith("pk_live_")


def test_generate_key_uses_live_prefix_for_other():
    full, _prefix, _hash = generate_key("write")
    assert full.startswith("sk_live_")


def test_generate_key_prefix_is_24_chars():
    _full, prefix, _hash = generate_key("ingest")
    assert len(prefix) == 24


def test_generate_key_hash_matches_sha256():
    full, _prefix, key_hash = generate_key("ingest")
    assert key_hash == hashlib.sha256(full.encode()).hexdigest()
