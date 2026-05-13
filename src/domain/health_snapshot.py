from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class HealthSnapshot:
    id: UUID
    workspace_id: UUID
    account_id: UUID
    overall_score: int | None
    dimension_scores: dict  # {"email": 74, "csm_score": 90}
    formula_version: str
    computed_at: datetime
    superseded_at: datetime | None
