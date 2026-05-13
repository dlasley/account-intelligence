import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.domain.events import ActorType, AuditAction
from supabase import Client

logger = logging.getLogger(__name__)


def insert_audit_event(
    client: Client,
    workspace_id: UUID | None,
    actor_type: ActorType,
    actor_id: str,
    action: AuditAction,
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    metadata: dict | None = None,
) -> None:
    data = {
        "id": str(uuid4()),
        "workspace_id": str(workspace_id) if workspace_id else None,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id else None,
        "metadata": metadata,
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    try:
        client.table("audit_events").insert(data).execute()
    except Exception:
        logger.warning(
            "audit write failed: action=%s resource=%s", action, resource_id, exc_info=True
        )
        raise
