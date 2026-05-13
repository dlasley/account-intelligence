"""POST /signal/{kind} — push webhook adapter entry point (ADR-020 D2, Phase 2 / Phase 2.5).

Each `kind` maps to a registered push adapter. Unregistered kinds return 501.
The `note` kind is reserved but has no registered adapter in v1 (Granola is
pull-based). Posting to /signal/note returns 501.

Vendor dispatch for `ticket`:
  The same URL receives webhooks from both Plain and Pylon. The vendor is identified
  by which signature header is present in the request:
    Plain-Request-Signature  → Plain adapter
    X-Pylon-Signature        → Pylon adapter
  If neither header is present, the handler returns 401 (cannot determine vendor).
  This approach requires no extra DB round-trip for vendor detection and is explicit
  about which header establishes the vendor identity.

Handler flow for `ticket` (Plain) — see ADR-020 D10:
  1. Read raw body bytes (needed for HMAC before JSON parse)
  2. Parse JSON
  3. Detect vendor from signature header
  4. Dispatch to per-vendor handler (Plain or Pylon)
  5-11. Per-vendor credential lookup, HMAC verify, adapter parse, normalize, regen, respond
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import src.analytics as analytics
from src.db.client import get_client
from src.db.external_credentials import (
    get_credential_by_plain_workspace_id,
    get_credential_by_pylon_workspace_id,
)
from src.db.workspaces import get_workspace_by_id
from src.integrations.crypto import decrypt_secret, get_integration_encryption_key
from src.integrations.plain.adapter import parse_plain_event
from src.integrations.plain.hmac import verify_plain_signature
from src.integrations.pylon.adapter import parse_pylon_event
from src.integrations.pylon.hmac import verify_pylon_signature
from src.pipeline.scheduler import schedule_regen
from src.pipeline.structured_signal import normalize_structured_signal

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Adapter registry ─────────────────────────────────────────────────────────
# Maps kind → handler coroutine.
# "note" is intentionally absent — Granola is pull-based (Phase 3).
# Posting to /signal/note returns 501 (no adapter registered).

async def _handle_ticket(request: Request) -> JSONResponse:
    """Dispatch to Plain or Pylon adapter based on which signature header is present."""
    if request.headers.get("Plain-Request-Signature"):
        return await _handle_plain_ticket(request)
    if request.headers.get("X-Pylon-Signature"):
        return await _handle_pylon_ticket(request)
    # Neither vendor's header is present — unknown vendor, refuse with 401
    logger.warning("ticket_unknown_vendor no_signature_header")
    raise HTTPException(status_code=401, detail="Missing vendor signature header")


REGISTERED_PUSH_ADAPTERS: dict[str, object] = {
    "ticket": _handle_ticket,
}


# ─── Route ────────────────────────────────────────────────────────────────────


@router.post("/signal/{kind}")
async def receive_signal(kind: str, request: Request) -> JSONResponse:
    handler = REGISTERED_PUSH_ADAPTERS.get(kind)
    if handler is None:
        raise HTTPException(status_code=501, detail=f"No adapter registered for kind '{kind}'")
    return await handler(request)  # type: ignore[operator]


# ─── Plain ticket adapter ─────────────────────────────────────────────────────


async def _handle_plain_ticket(request: Request) -> JSONResponse:
    # 1. Read raw bytes before any JSON parse — needed for HMAC verification
    raw_bytes = await request.body()

    # 2. Parse JSON
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("plain_malformed_payload json_parse_failed: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(body, dict):
        logger.warning("plain_malformed_payload body_not_object")
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    # 3. Extract Plain workspace ID — needed to look up the per-workspace credential
    plain_workspace_id: str | None = body.get("workspaceId")
    if not plain_workspace_id:
        logger.warning("plain_malformed_payload missing_workspaceId")
        raise HTTPException(status_code=400, detail="Missing workspaceId in payload")

    client = get_client()

    # 4. Look up credential by Plain workspace ID
    credential = get_credential_by_plain_workspace_id(client, plain_workspace_id)
    if credential is None:
        logger.warning("plain_workspace_unknown plain_workspace_id=%s", plain_workspace_id)
        # Return 200 — prevents Plain's retry storm for unknown/unconfigured workspaces
        return JSONResponse({"status": "workspace_unknown"}, status_code=200)

    # 5. Decrypt the webhook signing secret
    try:
        enc_key = get_integration_encryption_key()
        secret = decrypt_secret(credential.secret_enc, enc_key)
    except Exception as exc:
        logger.error(
            "plain_credential_decrypt_failed credential=%s hint=%s: %s",
            credential.id,
            credential.key_hint,
            exc,
        )
        raise HTTPException(status_code=500, detail="Credential error") from exc

    # 6. Verify HMAC signature
    signature_header = request.headers.get("Plain-Request-Signature", "")
    if not signature_header:
        logger.warning(
            "plain_hmac_mismatch credential=%s reason=missing_header", credential.id
        )
        raise HTTPException(status_code=401, detail="Missing Plain-Request-Signature header")

    if not verify_plain_signature(raw_bytes, signature_header, secret):
        logger.warning("plain_hmac_mismatch credential=%s", credential.id)
        raise HTTPException(status_code=401, detail="Signature verification failed")

    # 7. Extract event type
    event_type: str = body.get("type", "")
    if not event_type:
        logger.warning("plain_malformed_payload missing_type credential=%s", credential.id)
        raise HTTPException(status_code=400, detail="Missing type in payload")

    logger.info(
        "plain_webhook_received event_type=%s credential=%s hint=%s",
        event_type,
        credential.id,
        credential.key_hint,
    )

    # 8. Parse Plain event to StructuredSignalInput
    try:
        signal_input = parse_plain_event(body, event_type, credential.id)
    except ValueError as exc:
        logger.warning("plain_malformed_payload parse_failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if signal_input is None:
        # Unhandled event type — log and return 200 (don't make Plain retry)
        return JSONResponse({"status": "event_skipped", "type": event_type}, status_code=200)

    # 9. Resolve workspace — needed for normalize_structured_signal
    workspace = get_workspace_by_id(client, credential.workspace_id)
    if workspace is None:
        logger.error(
            "plain_workspace_row_missing workspace_id=%s credential=%s",
            credential.workspace_id,
            credential.id,
        )
        raise HTTPException(status_code=500, detail="Workspace not found")

    # 10. Normalize → signal row
    try:
        result = normalize_structured_signal(
            signal_input,
            workspace.id,
            workspace.name,
            credential.id,
            credential.kind,
            client,
        )
    except Exception as exc:
        logger.exception(
            "plain_pipeline_error external_id=%s workspace=%s",
            signal_input.external_id,
            workspace.slug,
        )
        raise HTTPException(status_code=500, detail="Pipeline error") from exc

    if result.duplicate:
        logger.info(
            "plain_duplicate external_id=%s workspace=%s",
            signal_input.external_id,
            workspace.slug,
        )
    else:
        logger.info(
            "plain_signal_ingested external_id=%s routing=%s account=%s workspace=%s",
            signal_input.external_id,
            result.signal.routing_method,
            result.signal.account_id,
            workspace.slug,
        )

    # 11. Schedule narrative regen (fire-and-log)
    if not result.duplicate and result.signal.account_id is not None:
        try:
            schedule_regen(result.signal, workspace.id, client)
        except Exception:
            logger.exception(
                "plain_schedule_regen_failed signal=%s workspace=%s",
                result.signal.id,
                workspace.slug,
            )

    analytics.track(
        "Plain Signal Ingested",
        workspace.id,
        {
            "event_type": event_type,
            "routing_method": (
                str(result.signal.routing_method) if result.signal.routing_method else None
            ),
            "duplicate": result.duplicate,
        },
    )

    return JSONResponse(
        {
            "accepted": True,
            "signal_id": str(result.signal.id),
            "duplicate": result.duplicate,
        },
        status_code=200,
    )


# ─── Pylon ticket adapter ─────────────────────────────────────────────────────


async def _handle_pylon_ticket(request: Request) -> JSONResponse:
    # 1. Read raw bytes before any JSON parse — needed for HMAC verification
    raw_bytes = await request.body()

    # 2. Parse JSON (Pylon wraps everything under a "data" key)
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("pylon_malformed_payload json_parse_failed: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(body, dict):
        logger.warning("pylon_malformed_payload body_not_object")
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    # 3. Extract Pylon workspace ID from data.workspace_id (or data.workspaceId)
    data = body.get("data") or {}
    pylon_workspace_id: str | None = data.get("workspace_id") or data.get("workspaceId")
    if not pylon_workspace_id:
        logger.warning("pylon_malformed_payload missing_workspace_id")
        raise HTTPException(status_code=400, detail="Missing workspace_id in payload data")

    client = get_client()

    # 4. Look up credential by Pylon workspace ID
    credential = get_credential_by_pylon_workspace_id(client, pylon_workspace_id)
    if credential is None:
        logger.warning("pylon_workspace_unknown pylon_workspace_id=%s", pylon_workspace_id)
        # Return 200 — prevents Pylon's retry storm (up to 4 retries) for unknown workspaces
        return JSONResponse({"status": "workspace_unknown"}, status_code=200)

    # 5. Decrypt the webhook signing secret
    try:
        enc_key = get_integration_encryption_key()
        secret = decrypt_secret(credential.secret_enc, enc_key)
    except Exception as exc:
        logger.error(
            "pylon_credential_decrypt_failed credential=%s hint=%s: %s",
            credential.id,
            credential.key_hint,
            exc,
        )
        raise HTTPException(status_code=500, detail="Credential error") from exc

    # 6. Verify HMAC signature (X-Pylon-Signature + Pylon-Webhook-Timestamp)
    signature_header = request.headers.get("X-Pylon-Signature", "")
    timestamp_header = request.headers.get("Pylon-Webhook-Timestamp", "")

    if not signature_header:
        logger.warning(
            "pylon_hmac_mismatch credential=%s reason=missing_signature_header", credential.id
        )
        raise HTTPException(status_code=401, detail="Missing X-Pylon-Signature header")

    if not timestamp_header:
        logger.warning(
            "pylon_hmac_mismatch credential=%s reason=missing_timestamp_header", credential.id
        )
        raise HTTPException(status_code=401, detail="Missing Pylon-Webhook-Timestamp header")

    if not verify_pylon_signature(raw_bytes, signature_header, timestamp_header, secret):
        logger.warning("pylon_hmac_mismatch credential=%s", credential.id)
        raise HTTPException(status_code=401, detail="Signature verification failed")

    # 7. Extract event type
    event_type: str = data.get("type") or data.get("event_type", "")
    if not event_type:
        logger.warning("pylon_malformed_payload missing_type credential=%s", credential.id)
        raise HTTPException(status_code=400, detail="Missing type in payload data")

    logger.info(
        "pylon_webhook_received event_type=%s credential=%s hint=%s",
        event_type,
        credential.id,
        credential.key_hint,
    )

    # 8. Parse Pylon event to StructuredSignalInput
    try:
        signal_input = parse_pylon_event(body, event_type, credential.id)
    except ValueError as exc:
        logger.warning("pylon_malformed_payload parse_failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if signal_input is None:
        # Unhandled or skipped event type — log and return 200 (don't make Pylon retry)
        return JSONResponse({"status": "event_skipped", "type": event_type}, status_code=200)

    # 9. Resolve workspace — needed for normalize_structured_signal
    workspace = get_workspace_by_id(client, credential.workspace_id)
    if workspace is None:
        logger.error(
            "pylon_workspace_row_missing workspace_id=%s credential=%s",
            credential.workspace_id,
            credential.id,
        )
        raise HTTPException(status_code=500, detail="Workspace not found")

    # 10. Normalize → signal row
    try:
        result = normalize_structured_signal(
            signal_input,
            workspace.id,
            workspace.name,
            credential.id,
            credential.kind,
            client,
        )
    except Exception as exc:
        logger.exception(
            "pylon_pipeline_error external_id=%s workspace=%s",
            signal_input.external_id,
            workspace.slug,
        )
        raise HTTPException(status_code=500, detail="Pipeline error") from exc

    if result.duplicate:
        logger.info(
            "pylon_duplicate external_id=%s workspace=%s",
            signal_input.external_id,
            workspace.slug,
        )
    else:
        logger.info(
            "pylon_signal_ingested external_id=%s routing=%s account=%s workspace=%s",
            signal_input.external_id,
            result.signal.routing_method,
            result.signal.account_id,
            workspace.slug,
        )

    # 11. Schedule narrative regen (fire-and-log)
    if not result.duplicate and result.signal.account_id is not None:
        try:
            schedule_regen(result.signal, workspace.id, client)
        except Exception:
            logger.exception(
                "pylon_schedule_regen_failed signal=%s workspace=%s",
                result.signal.id,
                workspace.slug,
            )

    analytics.track(
        "Pylon Signal Ingested",
        workspace.id,
        {
            "event_type": event_type,
            "routing_method": (
                str(result.signal.routing_method) if result.signal.routing_method else None
            ),
            "duplicate": result.duplicate,
        },
    )

    return JSONResponse(
        {
            "accepted": True,
            "signal_id": str(result.signal.id),
            "duplicate": result.duplicate,
        },
        status_code=200,
    )
