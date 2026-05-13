"""FastAPI handler tests for POST /run-polls.

Mirrors test_scheduler.py in structure. All DB + poller calls are patched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from src.server.app import create_app

SECRET = "scheduler-secret-xyz"

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("SCHEDULER_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
    return TestClient(create_app(), raise_server_exceptions=False)


def _post(client: TestClient, *, secret: str = SECRET):
    return client.post("/run-polls", headers={"Authorization": f"Bearer {secret}"})


def _workspace(slug: str = "acme") -> MagicMock:
    ws = MagicMock()
    ws.id = uuid4()
    ws.slug = slug
    ws.name = "Acme Corp"
    ws.internal_domains = ("acme.com",)
    return ws


def _credential(workspace_id=None) -> MagicMock:
    cred = MagicMock()
    cred.id = uuid4()
    cred.workspace_id = workspace_id or uuid4()
    cred.kind = "granola_api_key"
    return cred


def _state() -> MagicMock:
    state = MagicMock()
    state.id = uuid4()
    state.cursor = None
    state.consecutive_errors = 0
    return state


# ─── Tests: auth ──────────────────────────────────────────────────────────────


def test_scheduler_secret_unset_returns_500(monkeypatch):
    monkeypatch.delenv("SCHEDULER_SECRET", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
    client = TestClient(create_app(), raise_server_exceptions=False)
    resp = client.post("/run-polls", headers={"Authorization": f"Bearer {SECRET}"})
    assert resp.status_code == 500


def test_wrong_secret_returns_401(monkeypatch):
    client = _make_client(monkeypatch)
    resp = _post(client, secret="wrong-secret")
    assert resp.status_code == 401


def test_missing_authorization_header_returns_401(monkeypatch):
    client = _make_client(monkeypatch)
    resp = client.post("/run-polls")
    assert resp.status_code == 401


# ─── Tests: fan-out ───────────────────────────────────────────────────────────


def test_empty_workspace_list_returns_zero_counts(monkeypatch):
    client = _make_client(monkeypatch)
    with (
        patch("src.server.routes.polls.get_client"),
        patch("src.server.routes.polls.get_all_workspaces", return_value=[]),
    ):
        resp = _post(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["polled_workspaces"] == 0
    assert body["total_new"] == 0
    assert body["failed"] == 0


def test_workspace_with_no_credentials_not_counted(monkeypatch):
    """Workspace with no granola credentials doesn't increment polled_workspaces."""
    client = _make_client(monkeypatch)
    ws = _workspace()
    with (
        patch("src.server.routes.polls.get_client"),
        patch("src.server.routes.polls.get_all_workspaces", return_value=[ws]),
        patch("src.server.routes.polls.get_active_credentials_by_kind", return_value=[]),
    ):
        resp = _post(client)
    assert resp.status_code == 200
    assert resp.json()["polled_workspaces"] == 0


def test_workspace_with_credentials_polled(monkeypatch):
    """Workspace with one credential increments polled_workspaces and total_new."""
    client = _make_client(monkeypatch)
    ws = _workspace()
    cred = _credential(ws.id)
    state = _state()

    with (
        patch("src.server.routes.polls.get_client"),
        patch("src.server.routes.polls.get_all_workspaces", return_value=[ws]),
        patch("src.server.routes.polls.get_active_credentials_by_kind", return_value=[cred]),
        patch("src.server.routes.polls.get_or_create_integration_state", return_value=state),
        patch(
            "src.server.routes.polls.poll_workspace_granola",
            new=AsyncMock(return_value=(3, 0)),
        ),
    ):
        resp = _post(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["polled_workspaces"] == 1
    assert body["total_new"] == 3
    assert body["failed"] == 0


def test_per_workspace_exception_isolation(monkeypatch):
    """Exception in one workspace's poll does not abort other workspaces."""
    client = _make_client(monkeypatch)
    ws_a = _workspace("ws-a")
    ws_b = _workspace("ws-b")
    cred_a = _credential(ws_a.id)
    cred_b = _credential(ws_b.id)
    state = _state()

    call_count = 0

    async def poll_with_first_failure(workspace, credential, state_, client_db):
        nonlocal call_count
        call_count += 1
        if workspace.slug == "ws-a":
            raise RuntimeError("workspace-a boom")
        return (2, 0)

    with (
        patch("src.server.routes.polls.get_client"),
        patch("src.server.routes.polls.get_all_workspaces", return_value=[ws_a, ws_b]),
        patch(
            "src.server.routes.polls.get_active_credentials_by_kind",
            side_effect=[[cred_a], [cred_b]],
        ),
        patch(
            "src.server.routes.polls.get_or_create_integration_state", return_value=state
        ),
        patch(
            "src.server.routes.polls.poll_workspace_granola",
            new=poll_with_first_failure,
        ),
    ):
        resp = _post(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["polled_workspaces"] == 2
    # ws-a failed, ws-b succeeded with 2 new
    assert body["total_new"] == 2
    assert body["failed"] == 1
    # Both workspaces were attempted
    assert call_count == 2


def test_multiple_credentials_per_workspace(monkeypatch):
    """Two credentials for one workspace results in two poll calls."""
    client = _make_client(monkeypatch)
    ws = _workspace()
    cred_1 = _credential(ws.id)
    cred_2 = _credential(ws.id)
    state = _state()

    poll_calls = []

    async def counting_poll(workspace, credential, state_, client_db):
        poll_calls.append(credential.id)
        return (1, 0)

    with (
        patch("src.server.routes.polls.get_client"),
        patch("src.server.routes.polls.get_all_workspaces", return_value=[ws]),
        patch(
            "src.server.routes.polls.get_active_credentials_by_kind",
            return_value=[cred_1, cred_2],
        ),
        patch("src.server.routes.polls.get_or_create_integration_state", return_value=state),
        patch(
            "src.server.routes.polls.poll_workspace_granola",
            new=counting_poll,
        ),
    ):
        resp = _post(client)

    assert resp.status_code == 200
    assert len(poll_calls) == 2
    assert resp.json()["total_new"] == 2
