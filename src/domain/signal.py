from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class SourceType(StrEnum):
    INBOUND_EMAIL = "inbound_email"
    JSON_FIXTURE = "json_fixture"
    OUTBOUND_EMAIL = "outbound_email"
    PRODUCT_EVENT = "product_event"
    PLAIN_TICKET = "plain_ticket"
    GRANOLA_NOTE = "granola_note"
    PYLON_TICKET = "pylon_ticket"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    INTERNAL = "internal"


class Channel(StrEnum):
    EMAIL = "email"
    TICKET = "ticket"
    CHAT = "chat"
    PRODUCT = "product"
    MEETING_NOTE = "meeting_note"


class RoutingMethod(StrEnum):
    PLUS_ADDRESSING = "plus_addressing"
    HEADER_DOMAIN = "header_domain"
    FORWARD_PARSE = "forward_parse"
    THREAD_INHERIT = "thread_inherit"
    THREAD_INHERIT_SPLIT = "thread_inherit_split"
    AUTO_DISCOVERY = "auto_discovery"
    MANUAL = "manual"
    UNMATCHED = "unmatched"
    OUTBOUND_BCC = "outbound_bcc"
    API_KEY_IDENTITY = "api_key_identity"


@dataclass
class Signal:
    id: UUID
    workspace_id: UUID
    account_id: UUID | None
    source_type: SourceType
    external_id: str
    thread_id: str | None
    direction: Direction
    channel: Channel
    occurred_at: datetime
    created_at: datetime
    updated_at: datetime
    subject: str | None
    body: str
    author_contact_id: UUID | None
    recipient_contact_ids: list[UUID]
    routing_method: RoutingMethod | None
    routing_confidence: float | None
    routing_warning: str | None
    deleted_at: datetime | None
    event_name: str | None = None
    event_properties: dict = field(default_factory=dict)
    event_id: str | None = None
    signal_metadata: dict = field(default_factory=dict)
