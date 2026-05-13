from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class DraftIntent(StrEnum):
    CHECK_IN = "check_in"
    EXPANSION = "expansion"
    RENEWAL = "renewal"
    CUSTOM = "custom"


class DraftStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"


class GeneratedBy(StrEnum):
    LLM = "llm"
    HUMAN = "human"
    TEMPLATE = "template"


@dataclass
class OutreachDraft:
    id: UUID
    workspace_id: UUID
    account_id: UUID
    contact_id: UUID | None
    intent: DraftIntent
    user_context: str | None
    subject: str
    body: str
    generated_by: GeneratedBy
    status: DraftStatus
    sent_at: datetime | None
    sent_by_user_id: UUID | None
    model: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    template_id: str | None = None
