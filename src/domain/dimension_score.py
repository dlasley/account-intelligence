from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class ScoredBy(StrEnum):
    SYSTEM = "system"
    LLM = "llm"
    CSM = "csm"


@dataclass(frozen=True)
class DimensionScore:
    id: UUID
    workspace_id: UUID
    account_id: UUID
    dimension_id: UUID
    score: int
    rationale: str | None
    scored_by: ScoredBy
    metadata: dict | None
    scored_at: datetime
    superseded_at: datetime | None
