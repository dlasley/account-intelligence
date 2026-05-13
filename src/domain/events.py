from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AuditAction(StrEnum):
    SIGNAL_INGESTED = "signal.ingested"
    SIGNAL_REROUTED = "signal.rerouted"
    NARRATIVE_GENERATED = "narrative.generated"
    ACCOUNT_CONFIRMED = "account.confirmed"
    ACCOUNT_REJECTED = "account.rejected"
    ACCOUNT_ARCHIVED = "account.archived"
    USER_LOGIN = "user.login"
    ROUTING_THREAD_SPLIT = "routing.thread_split"


class ActorType(StrEnum):
    USER = "user"
    WORKER = "worker"
    SYSTEM = "system"
    API_KEY = "api_key"


@dataclass(frozen=True)
class AuditEvent:
    id: UUID
    workspace_id: UUID | None
    actor_type: ActorType
    actor_id: str
    action: AuditAction
    resource_type: str | None
    resource_id: UUID | None
    metadata: dict | None
    occurred_at: datetime
