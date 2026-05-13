"""POST /run-polls — Granola integration poller scheduler endpoint (ADR-020 Phase 3, D4).

Mirrors the /run-narratives pattern exactly:
    - Auth: Authorization: Bearer <SCHEDULER_SECRET> (same env var, same compare_digest logic)
    - Fan-out: all active workspaces → all active granola_api_key credentials
    - Per-workspace isolation: one workspace exception does not abort others
    - Summary response: {polled_workspaces, total_new, total_failed}

Intended trigger: Cloud Scheduler job (integration-poller) every */15 * * * * UTC.
The Cloud Scheduler job creation is an ops step; this endpoint just has to exist
and be correctly authenticated.
"""

import hmac
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.db.client import get_client
from src.db.external_credentials import get_active_credentials_by_kind
from src.db.integration_state import get_or_create_integration_state
from src.db.workspaces import get_all_workspaces
from src.integrations.granola.poller import poll_workspace_granola

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/run-polls")
async def run_polls(request: Request) -> JSONResponse:
    """Fan-out Granola polls across all active workspaces.

    Authentication: Authorization: Bearer <SCHEDULER_SECRET>.
    Returns 500 if SCHEDULER_SECRET is unset (fail-closed).
    Returns 401 on wrong secret.
    """
    expected_secret = (os.environ.get("SCHEDULER_SECRET") or "").strip()
    if not expected_secret:
        logger.error("SCHEDULER_SECRET not configured")
        raise HTTPException(status_code=500, detail="Server misconfigured")
    auth_header = request.headers.get("Authorization", "")
    provided_secret = auth_header.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(expected_secret, provided_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")

    client_db = get_client()
    workspaces = get_all_workspaces(client_db)

    total_new = 0
    total_failed = 0
    polled_workspaces = 0

    for workspace in workspaces:
        credentials = get_active_credentials_by_kind(client_db, workspace.id, "granola_api_key")
        if not credentials:
            continue

        polled_workspaces += 1
        for credential in credentials:
            state = get_or_create_integration_state(
                client_db, workspace.id, credential.id, credential.kind
            )
            try:
                new, _dup = await poll_workspace_granola(workspace, credential, state, client_db)
                total_new += new
            except Exception:
                logger.exception(
                    "run_polls workspace=%s cred=%s unexpected_error",
                    workspace.slug,
                    credential.id,
                )
                total_failed += 1

    logger.info(
        "run-polls complete polled=%d new=%d failed=%d",
        polled_workspaces,
        total_new,
        total_failed,
    )
    return JSONResponse(
        {"polled_workspaces": polled_workspaces, "total_new": total_new, "failed": total_failed}
    )
