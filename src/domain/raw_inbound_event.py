from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.domain.signal import SourceType


class ParseStatus(StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class RawInboundEvent:
    id: UUID
    workspace_id: UUID
    received_at: datetime
    source_type: SourceType
    raw_payload: str  # JSON string of provider webhook body or fixture JSON
    parse_status: ParseStatus
    signal_id: UUID | None
    error_detail: str | None
    processed_at: datetime | None
