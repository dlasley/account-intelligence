"""FastAPI handler tests for POST /signal/{kind}.

All DB calls and pipeline calls are patched. No live Supabase required.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_stdlib
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from src.db.external_credentials import ExternalCredential
from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType
from src.integrations.crypto import encrypt_secret
from src.pipeline.product_event import IngestResult
from src.server.app import create_app

# ─── Test constants ───────────────────────────────────────────────────────────

_WS_ID = uuid4()
_CRED_ID = uuid4()
_PLAIN_WS_ID = "worksp_abc123"
_WEBHOOK_SECRET = "test-plain-webhook-secret-x"
_ENC_KEY_B64 = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # 32 bytes of zeroes, base64


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
    monkeypatch.setenv("INBOUND_DOMAIN", "signal.example.com")
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY", _ENC_KEY_B64)
    return TestClient(create_app(), raise_server_exceptions=False)


def _enc_key_bytes() -> bytes:
    import base64
    return base64.b64decode(_ENC_KEY_B64)


def _encrypted_secret() -> bytes:
    """Return WEBHOOK_SECRET encrypted with the test key."""
    return encrypt_secret(_WEBHOOK_SECRET, _enc_key_bytes())


def _make_credential(*, plain_workspace_id: str = _PLAIN_WS_ID) -> ExternalCredential:
    now = datetime.now(UTC)
    return ExternalCredential(
        id=_CRED_ID,
        workspace_id=_WS_ID,
        kind="plain_webhook_secret",
        direction="inbound",
        label="Plain production",
        secret_enc=_encrypted_secret(),
        key_hint=_WEBHOOK_SECRET[-4:],
        metadata={"plain_workspace_id": plain_workspace_id},
        is_active=True,
        last_verified_at=None,
        error_at=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def _make_workspace() -> MagicMock:
    ws = MagicMock()
    ws.id = _WS_ID
    ws.slug = "acme-corp"
    ws.name = "Acme Corp"
    return ws


def _make_signal(*, duplicate: bool = False) -> IngestResult:
    now = datetime.now(UTC)
    signal = Signal(
        id=uuid4(),
        workspace_id=_WS_ID,
        account_id=None,
        source_type=SourceType.PLAIN_TICKET,
        external_id="plain:evt_001",
        thread_id="thread_001",
        direction=Direction.INBOUND,
        channel=Channel.TICKET,
        occurred_at=now,
        created_at=now,
        updated_at=now,
        subject="Help request",
        body="",
        author_contact_id=None,
        recipient_contact_ids=[],
        routing_method=RoutingMethod.UNMATCHED,
        routing_confidence=0.0,
        routing_warning=None,
        deleted_at=None,
        signal_metadata={},
    )
    return IngestResult(signal=signal, duplicate=duplicate)


def _thread_created_body(plain_workspace_id: str = _PLAIN_WS_ID) -> dict:
    return {
        "id": "evt_thread_001",
        "type": "thread.created",
        "timestamp": "2026-05-08T10:00:00Z",
        "workspaceId": plain_workspace_id,
        "payload": {
            "thread": {"id": "thread_001", "title": "I need help"},
            "customer": {
                "email": {"email": "alice@acme.com"},
                "fullName": "Alice Smith",
            },
        },
        "webhookMetadata": {},
    }


def _make_signature(body: bytes, secret: str) -> str:
    hex_digest = hmac_stdlib.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={hex_digest}"


def _post_ticket(
    client: TestClient,
    body: dict,
    *,
    secret: str = _WEBHOOK_SECRET,
    extra_headers: dict | None = None,
) -> object:
    body_bytes = json.dumps(body).encode()
    sig = _make_signature(body_bytes, secret)
    headers = {"Plain-Request-Signature": sig, "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return client.post("/signal/ticket", content=body_bytes, headers=headers)


# ─── 501 for unregistered kind ────────────────────────────────────────────────


def test_unregistered_kind_returns_501(monkeypatch):
    client = _make_client(monkeypatch)
    resp = client.post("/signal/note", content=b"{}", headers={"Content-Type": "application/json"})
    assert resp.status_code == 501


def test_unknown_kind_returns_501(monkeypatch):
    client = _make_client(monkeypatch)
    resp = client.post(
        "/signal/foobar", content=b"{}", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 501


# ─── Unknown workspace → 200 ──────────────────────────────────────────────────


def test_unknown_workspace_returns_200(monkeypatch):
    """No credential found for Plain workspace ID → 200, no retry storm."""
    client = _make_client(monkeypatch)
    body = _thread_created_body(plain_workspace_id="worksp_unknown")

    with (
        patch("src.server.routes.signal.get_client"),
        patch("src.server.routes.signal.get_credential_by_plain_workspace_id", return_value=None),
    ):
        resp = _post_ticket(client, body)

    assert resp.status_code == 200
    assert resp.json()["status"] == "workspace_unknown"


# ─── HMAC mismatch → 401 ─────────────────────────────────────────────────────


def test_hmac_mismatch_returns_401(monkeypatch):
    client = _make_client(monkeypatch)
    body = _thread_created_body()
    body_bytes = json.dumps(body).encode()
    # Sign with wrong secret
    wrong_sig = _make_signature(body_bytes, "completely-wrong-secret")

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_plain_workspace_id",
            return_value=_make_credential(),
        ),
    ):
        resp = client.post(
            "/signal/ticket",
            content=body_bytes,
            headers={
                "Plain-Request-Signature": wrong_sig,
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 401


def test_missing_signature_header_returns_401(monkeypatch):
    client = _make_client(monkeypatch)
    body = _thread_created_body()

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_plain_workspace_id",
            return_value=_make_credential(),
        ),
    ):
        resp = client.post(
            "/signal/ticket",
            content=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 401


# ─── Unhandled event type → 200 event_skipped ────────────────────────────────


def test_unhandled_event_type_returns_200_skipped(monkeypatch):
    client = _make_client(monkeypatch)
    body = {
        "id": "evt_sla_001",
        "type": "sla.breached",
        "timestamp": "2026-05-08T10:00:00Z",
        "workspaceId": _PLAIN_WS_ID,
        "payload": {},
        "webhookMetadata": {},
    }

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_plain_workspace_id",
            return_value=_make_credential(),
        ),
    ):
        resp = _post_ticket(client, body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "event_skipped"
    assert data["type"] == "sla.breached"


# ─── Happy path: valid request ingests signal ─────────────────────────────────


def test_valid_request_ingests_signal(monkeypatch):
    client = _make_client(monkeypatch)
    body = _thread_created_body()
    ingest_result = _make_signal()

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_plain_workspace_id",
            return_value=_make_credential(),
        ),
        patch(
            "src.server.routes.signal.get_workspace_by_id",
            return_value=_make_workspace(),
        ),
        patch(
            "src.server.routes.signal.normalize_structured_signal",
            return_value=ingest_result,
        ),
        patch("src.server.routes.signal.schedule_regen"),
        patch("src.server.routes.signal.analytics"),
    ):
        resp = _post_ticket(client, body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert data["duplicate"] is False
    assert "signal_id" in data


# ─── Duplicate event → 200 with duplicate=true ───────────────────────────────


def test_duplicate_event_returns_200_with_duplicate_flag(monkeypatch):
    client = _make_client(monkeypatch)
    body = _thread_created_body()
    ingest_result = _make_signal(duplicate=True)

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_plain_workspace_id",
            return_value=_make_credential(),
        ),
        patch(
            "src.server.routes.signal.get_workspace_by_id",
            return_value=_make_workspace(),
        ),
        patch(
            "src.server.routes.signal.normalize_structured_signal",
            return_value=ingest_result,
        ),
        patch("src.server.routes.signal.schedule_regen"),
        patch("src.server.routes.signal.analytics"),
    ):
        resp = _post_ticket(client, body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert data["duplicate"] is True


# ─── Malformed JSON → 400 ────────────────────────────────────────────────────


def test_malformed_json_returns_400(monkeypatch):
    client = _make_client(monkeypatch)
    resp = client.post(
        "/signal/ticket",
        content=b"this is not json",
        headers={"Content-Type": "application/json", "Plain-Request-Signature": "sha256=abc"},
    )
    assert resp.status_code == 400


# ─── Missing workspaceId → 400 ───────────────────────────────────────────────


def test_missing_workspace_id_returns_400(monkeypatch):
    client = _make_client(monkeypatch)
    body = {"id": "evt_001", "type": "thread.created", "timestamp": "2026-05-08T10:00:00Z"}
    body_bytes = json.dumps(body).encode()
    sig = _make_signature(body_bytes, _WEBHOOK_SECRET)

    with (
        patch("src.server.routes.signal.get_client"),
    ):
        resp = client.post(
            "/signal/ticket",
            content=body_bytes,
            headers={
                "Plain-Request-Signature": sig,
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 400


# ─── schedule_regen fires only when account_id is set ────────────────────────


def test_schedule_regen_called_when_account_id_present(monkeypatch):
    client = _make_client(monkeypatch)
    body = _thread_created_body()
    account_id = uuid4()

    # Build a signal with a real account_id
    now = datetime.now(UTC)
    signal = Signal(
        id=uuid4(),
        workspace_id=_WS_ID,
        account_id=account_id,
        source_type=SourceType.PLAIN_TICKET,
        external_id="plain:evt_001",
        thread_id="thread_001",
        direction=Direction.INBOUND,
        channel=Channel.TICKET,
        occurred_at=now,
        created_at=now,
        updated_at=now,
        subject="Help",
        body="",
        author_contact_id=None,
        recipient_contact_ids=[],
        routing_method=RoutingMethod.AUTO_DISCOVERY,
        routing_confidence=0.3,
        routing_warning=None,
        deleted_at=None,
        signal_metadata={},
    )
    ingest_result = IngestResult(signal=signal, duplicate=False)

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_plain_workspace_id",
            return_value=_make_credential(),
        ),
        patch(
            "src.server.routes.signal.get_workspace_by_id",
            return_value=_make_workspace(),
        ),
        patch(
            "src.server.routes.signal.normalize_structured_signal",
            return_value=ingest_result,
        ),
        patch("src.server.routes.signal.schedule_regen") as mock_sched,
        patch("src.server.routes.signal.analytics"),
    ):
        resp = _post_ticket(client, body)

    assert resp.status_code == 200
    mock_sched.assert_called_once()


def test_schedule_regen_not_called_when_no_account_id(monkeypatch):
    client = _make_client(monkeypatch)
    body = _thread_created_body()
    ingest_result = _make_signal()  # account_id=None by default

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_plain_workspace_id",
            return_value=_make_credential(),
        ),
        patch(
            "src.server.routes.signal.get_workspace_by_id",
            return_value=_make_workspace(),
        ),
        patch(
            "src.server.routes.signal.normalize_structured_signal",
            return_value=ingest_result,
        ),
        patch("src.server.routes.signal.schedule_regen") as mock_sched,
        patch("src.server.routes.signal.analytics"),
    ):
        resp = _post_ticket(client, body)

    assert resp.status_code == 200
    mock_sched.assert_not_called()


# =============================================================================
# Pylon adapter tests
# =============================================================================

_PYLON_WS_ID = "pylon_ws_abc123"
_PYLON_WEBHOOK_SECRET = "test-pylon-webhook-secret-y"
_PYLON_TIMESTAMP = "1746700800"


def _make_pylon_credential(*, pylon_workspace_id: str = _PYLON_WS_ID) -> ExternalCredential:
    now = datetime.now(UTC)
    return ExternalCredential(
        id=_CRED_ID,
        workspace_id=_WS_ID,
        kind="pylon_webhook_secret",
        direction="inbound",
        label="Pylon production",
        secret_enc=encrypt_secret(_PYLON_WEBHOOK_SECRET, _enc_key_bytes()),
        key_hint=_PYLON_WEBHOOK_SECRET[-4:],
        metadata={"pylon_workspace_id": pylon_workspace_id},
        is_active=True,
        last_verified_at=None,
        error_at=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def _make_pylon_signal(*, duplicate: bool = False) -> IngestResult:
    now = datetime.now(UTC)
    signal = Signal(
        id=uuid4(),
        workspace_id=_WS_ID,
        account_id=None,
        source_type=SourceType.PYLON_TICKET,
        external_id="pylon:evt_001",
        thread_id="pylon:issue_001",
        direction=Direction.INBOUND,
        channel=Channel.TICKET,
        occurred_at=now,
        created_at=now,
        updated_at=now,
        subject="Cannot access dashboard",
        body="I keep getting a 403.",
        author_contact_id=None,
        recipient_contact_ids=[],
        routing_method=RoutingMethod.UNMATCHED,
        routing_confidence=0.0,
        routing_warning=None,
        deleted_at=None,
        signal_metadata={},
    )
    return IngestResult(signal=signal, duplicate=duplicate)


def _issue_created_body(pylon_workspace_id: str = _PYLON_WS_ID) -> dict:
    return {
        "data": {
            "id": "evt_pylon_001",
            "type": "issue.created",
            "timestamp": "2026-05-08T10:00:00Z",
            "workspace_id": pylon_workspace_id,
            "issue": {
                "id": "issue_001",
                "title": "Cannot access dashboard",
                "requester": {"email": "alice@acme.com", "name": "Alice Smith"},
                "messages": [
                    {
                        "id": "msg_001",
                        "body": "I keep getting a 403.",
                        "author": {
                            "type": "customer",
                            "email": "alice@acme.com",
                            "name": "Alice Smith",
                        },
                    }
                ],
            },
        }
    }


def _make_pylon_signature(body: bytes, timestamp: str, secret: str) -> str:
    signing_payload = (timestamp + ".").encode() + body
    hex_digest = hmac_stdlib.new(secret.encode(), signing_payload, hashlib.sha256).hexdigest()
    return hex_digest


def _post_pylon_ticket(
    client: TestClient,
    body: dict,
    *,
    secret: str = _PYLON_WEBHOOK_SECRET,
    timestamp: str = _PYLON_TIMESTAMP,
    extra_headers: dict | None = None,
) -> object:
    body_bytes = json.dumps(body).encode()
    sig = _make_pylon_signature(body_bytes, timestamp, secret)
    headers = {
        "X-Pylon-Signature": sig,
        "Pylon-Webhook-Timestamp": timestamp,
        "Pylon-Webhook-Version": "v1",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    return client.post("/signal/ticket", content=body_bytes, headers=headers)


# ─── Dispatch: header detection routes correctly ─────────────────────────────


def test_plain_header_still_routes_to_plain_adapter(monkeypatch):
    """Regression: Plain-Request-Signature header → Plain adapter (not Pylon)."""
    client = _make_client(monkeypatch)
    body = _thread_created_body()
    ingest_result = _make_signal()

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_plain_workspace_id",
            return_value=_make_credential(),
        ),
        patch(
            "src.server.routes.signal.get_workspace_by_id",
            return_value=_make_workspace(),
        ),
        patch(
            "src.server.routes.signal.normalize_structured_signal",
            return_value=ingest_result,
        ),
        patch("src.server.routes.signal.schedule_regen"),
        patch("src.server.routes.signal.analytics"),
    ):
        resp = _post_ticket(client, body)

    assert resp.status_code == 200
    assert resp.json()["accepted"] is True


def test_no_signature_header_returns_401(monkeypatch):
    """No vendor signature header present → 401 (cannot determine vendor)."""
    client = _make_client(monkeypatch)
    body = _issue_created_body()
    resp = client.post(
        "/signal/ticket",
        content=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401


# ─── Pylon: unknown workspace → 200 ─────────────────────────────────────────


def test_pylon_unknown_workspace_returns_200(monkeypatch):
    """No credential found for Pylon workspace ID → 200, no retry storm."""
    client = _make_client(monkeypatch)
    body = _issue_created_body(pylon_workspace_id="pylon_ws_unknown")

    with (
        patch("src.server.routes.signal.get_client"),
        patch("src.server.routes.signal.get_credential_by_pylon_workspace_id", return_value=None),
    ):
        resp = _post_pylon_ticket(client, body)

    assert resp.status_code == 200
    assert resp.json()["status"] == "workspace_unknown"


# ─── Pylon: HMAC mismatch → 401 ─────────────────────────────────────────────


def test_pylon_hmac_mismatch_returns_401(monkeypatch):
    client = _make_client(monkeypatch)
    body = _issue_created_body()
    body_bytes = json.dumps(body).encode()
    wrong_sig = _make_pylon_signature(body_bytes, _PYLON_TIMESTAMP, "completely-wrong-secret")

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_pylon_workspace_id",
            return_value=_make_pylon_credential(),
        ),
    ):
        resp = client.post(
            "/signal/ticket",
            content=body_bytes,
            headers={
                "X-Pylon-Signature": wrong_sig,
                "Pylon-Webhook-Timestamp": _PYLON_TIMESTAMP,
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 401


def test_pylon_missing_timestamp_header_returns_401(monkeypatch):
    client = _make_client(monkeypatch)
    body = _issue_created_body()
    body_bytes = json.dumps(body).encode()
    sig = _make_pylon_signature(body_bytes, _PYLON_TIMESTAMP, _PYLON_WEBHOOK_SECRET)

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_pylon_workspace_id",
            return_value=_make_pylon_credential(),
        ),
    ):
        resp = client.post(
            "/signal/ticket",
            content=body_bytes,
            headers={
                "X-Pylon-Signature": sig,
                # Pylon-Webhook-Timestamp intentionally omitted
                "Content-Type": "application/json",
            },
        )

    assert resp.status_code == 401


# ─── Pylon: valid request ingests signal ────────────────────────────────────


def test_pylon_valid_request_ingests_signal(monkeypatch):
    client = _make_client(monkeypatch)
    body = _issue_created_body()
    ingest_result = _make_pylon_signal()

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_pylon_workspace_id",
            return_value=_make_pylon_credential(),
        ),
        patch(
            "src.server.routes.signal.get_workspace_by_id",
            return_value=_make_workspace(),
        ),
        patch(
            "src.server.routes.signal.normalize_structured_signal",
            return_value=ingest_result,
        ),
        patch("src.server.routes.signal.schedule_regen"),
        patch("src.server.routes.signal.analytics"),
    ):
        resp = _post_pylon_ticket(client, body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert data["duplicate"] is False
    assert "signal_id" in data


# ─── Pylon: duplicate event → 200 with duplicate=true ───────────────────────


def test_pylon_duplicate_event_returns_200_with_duplicate_flag(monkeypatch):
    client = _make_client(monkeypatch)
    body = _issue_created_body()
    ingest_result = _make_pylon_signal(duplicate=True)

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_pylon_workspace_id",
            return_value=_make_pylon_credential(),
        ),
        patch(
            "src.server.routes.signal.get_workspace_by_id",
            return_value=_make_workspace(),
        ),
        patch(
            "src.server.routes.signal.normalize_structured_signal",
            return_value=ingest_result,
        ),
        patch("src.server.routes.signal.schedule_regen"),
        patch("src.server.routes.signal.analytics"),
    ):
        resp = _post_pylon_ticket(client, body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert data["duplicate"] is True


# ─── Pylon: skipped event type → 200 event_skipped ──────────────────────────


def test_pylon_skipped_event_type_returns_200_skipped(monkeypatch):
    """issue.status_changed is recognized-but-skipped; handler returns 200 event_skipped."""
    client = _make_client(monkeypatch)
    body = {
        "data": {
            "id": "evt_status_001",
            "type": "issue.status_changed",
            "timestamp": "2026-05-08T10:00:00Z",
            "workspace_id": _PYLON_WS_ID,
            "issue": {
                "id": "issue_001",
                "title": "title",
                "requester": {"email": "alice@acme.com", "name": "Alice"},
                "messages": [],
            },
        }
    }

    with (
        patch("src.server.routes.signal.get_client"),
        patch(
            "src.server.routes.signal.get_credential_by_pylon_workspace_id",
            return_value=_make_pylon_credential(),
        ),
    ):
        resp = _post_pylon_ticket(client, body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "event_skipped"
    assert data["type"] == "issue.status_changed"
