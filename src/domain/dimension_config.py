from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class DimensionConfig:
    id: UUID
    workspace_id: UUID
    dimension_type: str
    name: str
    weight: float
    enabled: bool
    config: dict
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
