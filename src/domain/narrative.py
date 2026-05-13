from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Narrative:
    id: UUID
    workspace_id: UUID
    account_id: UUID
    narrative: str
    engagement: int
    engagement_rationale: str
    sentiment: int | None
    signal_window_start: datetime
    signal_window_end: datetime
    signals_considered: tuple[UUID, ...]
    model: str
    prompt_version: str
    generated_at: datetime
    superseded_at: datetime | None
