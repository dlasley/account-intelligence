import logging
from uuid import UUID

from src.db.regen_jobs import enqueue_regen_job
from src.domain.regen_job import RegenTrigger
from src.domain.signal import Signal
from supabase import Client

logger = logging.getLogger(__name__)

UNMATCHED_ACCOUNT_SLUG = "_unmatched"


def schedule_regen(
    signal: Signal, workspace_id: UUID, client: Client, *, account_slug: str | None = None
) -> None:
    if signal.account_id is None:
        return
    if account_slug == UNMATCHED_ACCOUNT_SLUG:
        return
    enqueue_regen_job(
        client,
        workspace_id=workspace_id,
        account_id=signal.account_id,
        triggered_by=RegenTrigger.NEW_SIGNAL,
    )
