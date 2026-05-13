from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Workspace:
    id: UUID
    organization_id: UUID
    slug: str
    name: str
    internal_domains: tuple[str, ...]
    crm_url_template: str | None
    crm_portal_id: str | None
    outbound_sender_email: str | None
    outbound_sender_name: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @property
    def inbound_address(self) -> str:
        from src.config import get_inbound_domain

        return f"{self.slug}@{get_inbound_domain()}"
