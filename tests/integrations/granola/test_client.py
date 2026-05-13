"""Unit tests for src.integrations.granola.client.GranolaClient.

HTTP calls are mocked with unittest.mock.patch on httpx.AsyncClient.get so that
no network traffic leaves the process.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.integrations.granola.client import (
    GranolaAuthError,
    GranolaClient,
    GranolaRateLimitError,
    GranolaServerError,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _mock_response(status: int, json_body: dict, *, retry_after: str | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = json_body
    headers: dict[str, str] = {}
    if retry_after:
        headers["Retry-After"] = retry_after
    resp.headers = headers
    # raise_for_status should only raise for non-handled codes
    if status < 400:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status}", request=MagicMock(), response=resp
        )
    return resp


def _note(note_id: str = "not_abc123") -> dict:
    return {
        "id": note_id,
        "title": "Q2 Customer Sync",
        "createdAt": "2026-05-08T10:00:00Z",
        "owner": {"name": "Alice CSM", "email": "alice@acme.com"},
        "summary": "Discussed expansion plans and Q2 roadmap.",
        "transcript": [
            {"speaker": {"name": "Alice CSM", "source": "microphone"}, "text": "Hello everyone."}
        ],
    }


# ─── Tests: list_notes ────────────────────────────────────────────────────────


async def test_list_notes_single_page():
    """One page with no next_cursor returns all notes and None cursor."""
    notes = [_note("not_001"), _note("not_002")]
    mock_resp = _mock_response(200, {"data": notes, "next_cursor": None})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = GranolaClient("grn_testkey")
        result_notes, cursor = await client.list_notes()

    assert result_notes == notes
    assert cursor is None


async def test_list_notes_multi_page():
    """Two-page scenario: first call returns next_cursor, second returns None."""
    page1_notes = [_note("not_001")]
    page2_notes = [_note("not_002")]

    page1_resp = _mock_response(200, {"data": page1_notes, "next_cursor": "not_001"})
    page2_resp = _mock_response(200, {"data": page2_notes, "next_cursor": None})

    call_count = 0

    with patch("httpx.AsyncClient") as mock_client_cls:

        async def mock_get(url, *, params, headers):
            nonlocal call_count
            call_count += 1
            return page1_resp if call_count == 1 else page2_resp

        mock_ctx = AsyncMock()
        mock_ctx.get = mock_get
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = GranolaClient("grn_testkey")
        notes1, cursor1 = await client.list_notes()
        notes2, cursor2 = await client.list_notes(after=cursor1)

    assert notes1 == page1_notes
    assert cursor1 == "not_001"
    assert notes2 == page2_notes
    assert cursor2 is None


async def test_list_notes_rate_limit_raises():
    """429 response raises GranolaRateLimitError."""
    mock_resp = _mock_response(429, {}, retry_after="10")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = GranolaClient("grn_testkey")
        with pytest.raises(GranolaRateLimitError):
            await client.list_notes()


async def test_list_notes_auth_error_raises_on_401():
    """401 response raises GranolaAuthError."""
    mock_resp = _mock_response(401, {"error": "invalid_key"})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = GranolaClient("grn_testkey")
        with pytest.raises(GranolaAuthError):
            await client.list_notes()


async def test_list_notes_auth_error_raises_on_403():
    """403 response raises GranolaAuthError."""
    mock_resp = _mock_response(403, {"error": "forbidden"})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = GranolaClient("grn_testkey")
        with pytest.raises(GranolaAuthError):
            await client.list_notes()


async def test_list_notes_server_error_raises():
    """5xx response raises GranolaServerError."""
    mock_resp = _mock_response(503, {"error": "unavailable"})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = GranolaClient("grn_testkey")
        with pytest.raises(GranolaServerError):
            await client.list_notes()


async def test_list_notes_timeout_propagates():
    """httpx.TimeoutException is not caught by the client — propagates to caller."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = GranolaClient("grn_testkey")
        with pytest.raises(httpx.TimeoutException):
            await client.list_notes()


# ─── Tests: get_note ─────────────────────────────────────────────────────────


async def test_get_note_with_transcript():
    """get_note passes include=transcript and returns the note dict."""
    note = _note("not_abc")
    mock_resp = _mock_response(200, note)

    captured_params: dict = {}

    with patch("httpx.AsyncClient") as mock_client_cls:

        async def mock_get(url, *, params, headers):
            captured_params.update(params)
            return mock_resp

        mock_ctx = AsyncMock()
        mock_ctx.get = mock_get
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = GranolaClient("grn_testkey")
        result = await client.get_note("not_abc", include_transcript=True)

    assert result == note
    assert captured_params.get("include") == "transcript"


async def test_get_note_without_transcript():
    """get_note without transcript does not send include param."""
    note = _note("not_abc")
    note_no_transcript = {k: v for k, v in note.items() if k != "transcript"}
    mock_resp = _mock_response(200, note_no_transcript)

    captured_params: dict = {}

    with patch("httpx.AsyncClient") as mock_client_cls:

        async def mock_get(url, *, params, headers):
            captured_params.update(params)
            return mock_resp

        mock_ctx = AsyncMock()
        mock_ctx.get = mock_get
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = GranolaClient("grn_testkey")
        await client.get_note("not_abc", include_transcript=False)

    assert "include" not in captured_params
