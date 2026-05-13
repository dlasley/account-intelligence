from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class UserRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


@dataclass(frozen=True)
class User:
    id: UUID
    workspace_id: UUID
    email: str
    display_name: str
    role: UserRole
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
