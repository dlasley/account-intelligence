from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class RegenJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class RegenTrigger(StrEnum):
    NEW_SIGNAL = "new_signal"
    REROUTE = "reroute"
    CONFIG_CHANGE = "config_change"
    MANUAL = "manual"


@dataclass
class NarrativeRegenJob:
    id: UUID
    workspace_id: UUID
    account_id: UUID
    created_at: datetime
    scheduled_for: datetime
    status: RegenJobStatus
    triggered_by: RegenTrigger
    triggered_by_user_id: UUID | None
    updated_at: datetime
    deleted_at: datetime | None
