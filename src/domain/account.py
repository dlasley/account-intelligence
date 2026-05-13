from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AccountStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    ARCHIVED = "archived"


class Vertical(StrEnum):
    SOFTWARE = "software"
    FINANCIAL_SERVICES = "financial_services"
    HEALTHCARE = "healthcare"
    LIFE_SCIENCES = "life_sciences"
    EDUCATION = "education"
    PUBLIC_SECTOR = "public_sector"
    RETAIL_CONSUMER = "retail_consumer"
    MEDIA_ENTERTAINMENT = "media_entertainment"
    MANUFACTURING = "manufacturing"
    ENERGY_UTILITIES = "energy_utilities"
    PROFESSIONAL_SERVICES = "professional_services"
    NONPROFIT = "nonprofit"
    OTHER = "other"


@dataclass
class Account:
    id: UUID
    workspace_id: UUID
    slug: str
    name: str
    primary_domain: str | None
    additional_domains: list[str]
    vertical: Vertical | None
    crm_record_id: str | None
    status: AccountStatus
    last_narrative_generated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    frequency_multiplier: float = 1.0
    overall_health_score: int | None = None

    def all_domains(self) -> list[str]:
        return [d for d in [self.primary_domain, *self.additional_domains] if d]
