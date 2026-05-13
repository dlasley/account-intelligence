"""
FastAPI handler tests for POST /run-narratives.

DB calls are fully patched — no Supabase connection required.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.server.app import create_app

SECRET = "scheduler-secret-xyz"


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("SCHEDULER_SECRET", SECRET)
    return TestClient(create_app(), raise_server_exceptions=False)


def _post(client: TestClient, *, secret: str = SECRET) -> object:
    return client.post("/run-narratives", headers={"Authorization": f"Bearer {secret}"})


def test_scheduler_secret_unset_returns_500(monkeypatch):
    monkeypatch.delenv("SCHEDULER_SECRET", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)
    resp = client.post("/run-narratives", headers={"Authorization": f"Bearer {SECRET}"})
    assert resp.status_code == 500


def test_wrong_secret_returns_401(monkeypatch):
    client = _make_client(monkeypatch)
    resp = _post(client, secret="wrong-secret")
    assert resp.status_code == 401


def test_missing_authorization_header_returns_401(monkeypatch):
    client = _make_client(monkeypatch)
    resp = client.post("/run-narratives")
    assert resp.status_code == 401


def test_empty_workspace_list_returns_zero_counts(monkeypatch):
    client = _make_client(monkeypatch)
    with (
        patch("src.server.routes.scheduler.get_client"),
        patch("src.server.routes.scheduler.anthropic_sdk.Anthropic"),
        patch("src.server.routes.scheduler.get_all_workspaces", return_value=[]),
        patch("src.server.routes.scheduler.recover_stale_jobs"),
    ):
        resp = _post(client)
    assert resp.status_code == 200
    assert resp.json() == {"generated": 0, "failed": 0}
