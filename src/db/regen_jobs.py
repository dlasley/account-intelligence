import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from src.domain.regen_job import NarrativeRegenJob, RegenJobStatus, RegenTrigger
from supabase import Client

logger = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 60
_RATE_CAP_MINUTES = 10


def _from_row(row: dict) -> NarrativeRegenJob:
    return NarrativeRegenJob(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        account_id=UUID(row["account_id"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        scheduled_for=datetime.fromisoformat(row["scheduled_for"]),
        status=RegenJobStatus(row["status"]),
        triggered_by=RegenTrigger(row["triggered_by"]),
        triggered_by_user_id=(
            UUID(row["triggered_by_user_id"]) if row.get("triggered_by_user_id") else None
        ),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        deleted_at=datetime.fromisoformat(row["deleted_at"]) if row.get("deleted_at") else None,
    )


def enqueue_regen_job(
    client: Client,
    workspace_id: UUID,
    account_id: UUID,
    triggered_by: RegenTrigger,
    triggered_by_user_id: UUID | None = None,
) -> NarrativeRegenJob | None:
    """
    Implements ADR-002 debounce + rate cap:
    1. Pending job exists with scheduled_for in the future → debounced, return None.
    2. Done/running job exists within last 10 minutes → schedule after cap window.
    3. Otherwise → schedule in 60 seconds.
    """
    now = datetime.now(UTC)

    # 1. Debounce check — pending job already queued
    pending = (
        client.table("narrative_regen_jobs")
        .select("id")
        .eq("workspace_id", str(workspace_id))
        .eq("account_id", str(account_id))
        .eq("status", RegenJobStatus.PENDING)
        .gt("scheduled_for", now.isoformat())
        .limit(1)
        .execute()
    )
    if pending.data:
        return None

    # 2. Rate cap check — recent *completion* within cap window.
    # Only DONE is queried; RUNNING's updated_at is its start/heartbeat time,
    # not completion — including it would incorrectly block a second concurrent worker.
    cap_cutoff = (now - timedelta(minutes=_RATE_CAP_MINUTES)).isoformat()
    recent = (
        client.table("narrative_regen_jobs")
        .select("updated_at")
        .eq("workspace_id", str(workspace_id))
        .eq("account_id", str(account_id))
        .eq("status", RegenJobStatus.DONE)
        .gt("updated_at", cap_cutoff)
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    if recent.data:
        last_completion = datetime.fromisoformat(recent.data[0]["updated_at"])
        scheduled_for = last_completion + timedelta(minutes=_RATE_CAP_MINUTES)
    else:
        scheduled_for = now + timedelta(seconds=_DEBOUNCE_SECONDS)

    data = {
        "id": str(uuid4()),
        "workspace_id": str(workspace_id),
        "account_id": str(account_id),
        "scheduled_for": scheduled_for.isoformat(),
        "status": RegenJobStatus.PENDING,
        "triggered_by": triggered_by,
        "triggered_by_user_id": str(triggered_by_user_id) if triggered_by_user_id else None,
    }
    result = client.table("narrative_regen_jobs").insert(data).execute()
    return _from_row(result.data[0])


def get_pending_jobs(
    client: Client, workspace_id: UUID, limit: int = 20
) -> list[NarrativeRegenJob]:
    """Return pending jobs whose scheduled_for is <= now, oldest first."""
    now = datetime.now(UTC).isoformat()
    result = (
        client.table("narrative_regen_jobs")
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .eq("status", RegenJobStatus.PENDING)
        .lte("scheduled_for", now)
        .order("scheduled_for", desc=False)
        .limit(limit)
        .execute()
    )
    return [_from_row(row) for row in result.data]


def recover_stale_jobs(client: Client, cutoff_minutes: int = 15) -> int:
    """Mark RUNNING jobs older than cutoff as FAILED. Recovers from prior worker crash."""
    stale_cutoff = (datetime.now(UTC) - timedelta(minutes=cutoff_minutes)).isoformat()
    result = (
        client.table("narrative_regen_jobs")
        .update({"status": RegenJobStatus.FAILED})
        .eq("status", RegenJobStatus.RUNNING)
        .lt("updated_at", stale_cutoff)
        .execute()
    )
    return len(result.data)


def update_job_status(client: Client, job_id: UUID, status: RegenJobStatus) -> None:
    result = (
        client.table("narrative_regen_jobs")
        .update({"status": status})
        .eq("id", str(job_id))
        .execute()
    )
    if not result.data:
        logger.warning("update_job_status matched no rows: job_id=%s status=%s", job_id, status)
