import hmac
import logging
import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import src.analytics as analytics
from src.config.loader import get_inbound_domain
from src.db.accounts import get_accounts_for_workspace
from src.db.client import get_client
from src.db.raw_inbound_events import insert_raw_event
from src.db.workspaces import get_workspace_by_slug
from src.pipeline.run import process_event
from src.signals.shared_inbox import (
    build_raw_inbound_event,
    build_raw_payload,
    extract_workspace_slug,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/inbound")
async def receive_inbound(request: Request) -> JSONResponse:
    # --- Secret verification ---
    expected_secret = (os.environ.get("WEBHOOK_SECRET") or "").strip()
    if not expected_secret:
        logger.error("WEBHOOK_SECRET not configured")
        raise HTTPException(status_code=500, detail="Server misconfigured")
    provided_secret = request.query_params.get("token", "")
    if not hmac.compare_digest(expected_secret, provided_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")

    # --- Parse form ---
    form = await request.form()
    form_data = {k: v for k, v in form.items()}

    # --- Extract workspace slug ---
    inbound_domain = get_inbound_domain()
    envelope_raw = form_data.get("envelope", "")
    envelope_json = envelope_raw if isinstance(envelope_raw, str) else ""
    try:
        workspace_slug, _account_slug = extract_workspace_slug(envelope_json, inbound_domain)
    except Exception as exc:
        logger.warning("envelope parse failed: %s", exc)
        # Return 200 — we can't route it, but returning 4xx would cause SendGrid to retry forever
        return JSONResponse({"status": "unroutable", "reason": str(exc)}, status_code=200)

    # --- Load workspace ---
    client = get_client()
    workspace = get_workspace_by_slug(client, workspace_slug)
    if not workspace:
        logger.warning("workspace not found: %s", workspace_slug)
        return JSONResponse({"status": "workspace_not_found"}, status_code=200)

    # --- Build and insert raw event ---
    received_at = datetime.now(UTC)
    raw_payload = build_raw_payload(form_data, inbound_domain)
    event = build_raw_inbound_event(raw_payload, workspace.id, received_at)

    try:
        insert_raw_event(client, event)
    except Exception as exc:
        # 23505 = unique_violation: duplicate external_id — already processed, return 200
        if "23505" in str(exc):
            logger.info("duplicate event external_id, skipping: %s", event.id)
            return JSONResponse({"status": "duplicate"}, status_code=200)
        logger.exception("insert_raw_event failed")
        raise HTTPException(status_code=500, detail="DB error") from exc

    # --- Run pipeline ---
    accounts = get_accounts_for_workspace(client, workspace.id)
    try:
        signal = process_event(event, workspace, accounts, client)
        logger.info(
            "signal processed workspace=%s external_id=%s routing=%s",
            workspace_slug,
            signal.external_id,
            signal.routing_method,
        )
    except ValidationError as exc:
        logger.warning("malformed inbound payload for event %s: %s", event.id, exc)
        return JSONResponse({"status": "malformed_payload"}, status_code=200)
    except Exception as exc:
        logger.exception("process_event failed for event %s", event.id)
        raise HTTPException(status_code=500, detail="Pipeline error") from exc

    analytics.track(
        "Inbound Email Received",
        workspace.id,
        {
            "routing_method": str(signal.routing_method) if signal.routing_method else None,
            "routing_confidence": signal.routing_confidence,
            "account_id": str(signal.account_id) if signal.account_id else None,
        },
    )

    return JSONResponse({"status": "ok"})
