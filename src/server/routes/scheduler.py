import hmac
import logging
import os

import anthropic as anthropic_sdk
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.config.loader import load_config
from src.db.accounts import get_accounts_for_workspace
from src.db.client import get_client
from src.db.contacts import get_contacts_by_ids
from src.db.narratives import get_current_narrative
from src.db.regen_jobs import get_pending_jobs, recover_stale_jobs, update_job_status
from src.db.signals import get_signals_for_account
from src.db.workspaces import get_all_workspaces
from src.domain.regen_job import RegenJobStatus
from src.pipeline.generator import generate_narrative
from src.pipeline.run import UNMATCHED_ACCOUNT_SLUG

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_JOBS_PER_WORKSPACE = 20


@router.post("/run-narratives")
async def run_narratives(request: Request) -> JSONResponse:
    expected_secret = (os.environ.get("SCHEDULER_SECRET") or "").strip()
    if not expected_secret:
        logger.error("SCHEDULER_SECRET not configured")
        raise HTTPException(status_code=500, detail="Server misconfigured")
    auth_header = request.headers.get("Authorization", "")
    provided_secret = auth_header.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(expected_secret, provided_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")

    client_db = get_client()
    client_ai = anthropic_sdk.Anthropic()

    workspaces = get_all_workspaces(client_db)
    total_generated = 0
    total_failed = 0

    recover_stale_jobs(client_db)
    for workspace in workspaces:
        jobs = get_pending_jobs(client_db, workspace.id, limit=_MAX_JOBS_PER_WORKSPACE)
        if not jobs:
            continue

        config = load_config(workspace.slug)
        accounts = {a.id: a for a in get_accounts_for_workspace(client_db, workspace.id)}

        for job in jobs:
            account = accounts.get(job.account_id)
            if not account or account.slug == UNMATCHED_ACCOUNT_SLUG:
                update_job_status(client_db, job.id, RegenJobStatus.FAILED)
                total_failed += 1
                continue

            update_job_status(client_db, job.id, RegenJobStatus.RUNNING)
            try:
                signals = get_signals_for_account(client_db, account.workspace_id, account.id)
                contact_ids = list({s.author_contact_id for s in signals if s.author_contact_id})
                contacts = get_contacts_by_ids(client_db, contact_ids)
                prior = get_current_narrative(client_db, account.workspace_id, account.id)
                generate_narrative(
                    account=account,
                    signals=signals,
                    contacts=contacts,
                    prior_narrative=prior,
                    config=config,
                    workspace_slug=workspace.slug,
                    client_db=client_db,
                    client_anthropic=client_ai,
                )
                update_job_status(client_db, job.id, RegenJobStatus.DONE)
                logger.info(
                    "narrative generated workspace=%s account=%s", workspace.slug, account.slug
                )
                total_generated += 1
            except Exception:
                logger.exception(
                    "narrative generation failed workspace=%s account=%s job=%s",
                    workspace.slug,
                    account.slug,
                    job.id,
                )
                update_job_status(client_db, job.id, RegenJobStatus.FAILED)
                total_failed += 1

    logger.info("run-narratives complete generated=%d failed=%d", total_generated, total_failed)
    return JSONResponse({"generated": total_generated, "failed": total_failed})
