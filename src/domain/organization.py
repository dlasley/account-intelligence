from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Organization:
    id: UUID
    slug: str
    name: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
