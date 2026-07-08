"""
FastAPI handler tests for POST /inbound.

DB calls and process_event are fully patched — no Supabase connection required.
"""

import json
import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.server.app import create_app

DOMAIN = "signal.example.com"
SECRET = "test-secret-abc123"

# Minimal SendGrid-style form data that routes cleanly
_ENVELOPE = json.dumps({"from": "sender@customer.com", "to": [f"quantas-labs@{DOMAIN}"]})
_FORM_DATA = {
    "envelope": _ENVELOPE,
    "from": "Sender <sender@customer.com>",
    "to": f"quantas-labs@{DOMAIN}",
    "subject": "Hello",
    "text": "Test body",
    "timestamp": "1714000000",
    "headers": "Message-ID: <msgid123@mail.example.com>\n",
}


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("INBOUND_DOMAIN", DOMAIN)
    return TestClient(create_app(), raise_server_exceptions=False)


def _post(client: TestClient, *, secret: str = SECRET, data: dict | None = None) -> object:
    return client.post("/inbound", data=data or _FORM_DATA, params={"token": secret})


# ---------------------------------------------------------------------------
# 1. WEBHOOK_SECRET unset → 500
# ---------------------------------------------------------------------------


def test_inbound_missing_webhook_secret(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("INBOUND_DOMAIN", DOMAIN)
    client = TestClient(create_app(), raise_server_exceptions=False)
    resp = client.post("/inbound", data=_FORM_DATA, params={"token": SECRET})
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# 2. Wrong secret → 401
# ---------------------------------------------------------------------------


def test_inbound_wrong_secret(monkeypatch):
    client = _make_client(monkeypatch)
    resp = _post(client, secret="wrong-secret")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 3. Valid request → 200 {"status": "ok"}
# ---------------------------------------------------------------------------


def test_inbound_valid_request(monkeypatch):
    client = _make_client(monkeypatch)

    fake_workspace = MagicMock()
    fake_workspace.id = uuid.uuid4()
    fake_workspace.internal_domains = set()

    fake_signal = MagicMock()
    fake_signal.external_id = "msgid123@mail.example.com"
    fake_signal.routing_method = "domain_match"

    with (
        patch("src.server.routes.inbound.get_client"),
        patch("src.server.routes.inbound.get_workspace_by_slug", return_value=fake_workspace),
        patch("src.server.routes.inbound.insert_raw_event"),
        patch("src.server.routes.inbound.get_accounts_for_workspace", return_value=[]),
        patch("src.server.routes.inbound.process_event", return_value=fake_signal),
    ):
        resp = _post(client)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# 4. process_event raises ValidationError → 200 {"status": "malformed_payload"}
# ---------------------------------------------------------------------------


def test_inbound_validation_error_returns_200(monkeypatch):
    client = _make_client(monkeypatch)

    fake_workspace = MagicMock()
    fake_workspace.id = uuid.uuid4()
    fake_workspace.internal_domains = set()

    # Construct a real ValidationError from pydantic
    from pydantic import BaseModel

    class _M(BaseModel):
        required_field: str

    try:
        _M.model_validate({})
    except ValidationError as exc:
        validation_error = exc

    with (
        patch("src.server.routes.inbound.get_client"),
        patch("src.server.routes.inbound.get_workspace_by_slug", return_value=fake_workspace),
        patch("src.server.routes.inbound.insert_raw_event"),
        patch("src.server.routes.inbound.get_accounts_for_workspace", return_value=[]),
        patch("src.server.routes.inbound.process_event", side_effect=validation_error),
    ):
        resp = _post(client)

    assert resp.status_code == 200
    assert resp.json() == {"status": "malformed_payload"}


# ---------------------------------------------------------------------------
# 5. insert_raw_event raises duplicate-key (23505) → 200 {"status": "duplicate"}
# ---------------------------------------------------------------------------


def test_inbound_duplicate_event_returns_200(monkeypatch):
    client = _make_client(monkeypatch)

    fake_workspace = MagicMock()
    fake_workspace.id = uuid.uuid4()
    fake_workspace.internal_domains = set()

    with (
        patch("src.server.routes.inbound.get_client"),
        patch("src.server.routes.inbound.get_workspace_by_slug", return_value=fake_workspace),
        patch(
            "src.server.routes.inbound.insert_raw_event",
            side_effect=Exception("duplicate key value violates unique constraint (23505)"),
        ),
        patch("src.server.routes.inbound.get_accounts_for_workspace", return_value=[]),
    ):
        resp = _post(client)

    assert resp.status_code == 200
    assert resp.json() == {"status": "duplicate"}
