import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, field_validator

import src.analytics as analytics
from src.db.accounts import get_account_by_email_domain
from src.db.audit import insert_audit_event
from src.db.contacts import upsert_contact
from src.db.signals import insert_signal
from src.domain.contact import Contact
from src.domain.events import ActorType, AuditAction
from src.domain.raw_inbound_event import RawInboundEvent
from src.domain.signal import Channel, Direction, Signal, SourceType
from src.pipeline._contact_factory import make_contact
from supabase import Client

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")


class InboundPayload(BaseModel):
    from_email: str
    body: str
    occurred_at: str
    external_id: str
    source_type: str
    direction: str
    channel: str
    from_name: str | None = None
    to_emails: list[str] = []
    subject: str | None = None
    thread_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    metadata: dict | None = None

    @field_validator("from_email")
    @classmethod
    def validate_from_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError(f"Malformed from_email: {v!r}")
        return v

    @field_validator("body")
    @classmethod
    def validate_body(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("body must not be blank")
        return v


@dataclass
class NormalizeResult:
    signal: Signal
    # None for outbound signals — internal sender is a workspace User, not a Contact
    author_contact: Contact | None
    recipient_contacts: list[Contact]


def normalize(
    event: RawInboundEvent,
    workspace_id: UUID,
    internal_domains: list[str],
    client: Client,
) -> NormalizeResult:
    raw_payload = json.loads(event.raw_payload)
    payload = InboundPayload.model_validate(raw_payload)

    def is_internal(email: str) -> bool:
        domain = email.lower().split("@")[-1] if "@" in email else ""
        return domain in internal_domains

    outbound = is_internal(payload.from_email)

    if outbound:
        # Internal sender is a workspace user — do not create a contact record
        author: Contact | None = None
        author_contact_id: UUID | None = None
    else:
        author_account_id = get_account_by_email_domain(client, workspace_id, payload.from_email)
        author = make_contact(
            workspace_id,
            payload.from_email,
            display_name=payload.from_name,
            is_internal=False,
            account_id=author_account_id,
        )
        author = upsert_contact(client, author)
        author_contact_id = author.id

    # Upsert recipient contacts
    recipients: list[Contact] = []
    for to_email in payload.to_emails:
        recipient_account_id = get_account_by_email_domain(client, workspace_id, to_email)
        c = make_contact(
            workspace_id,
            to_email,
            display_name=None,
            is_internal=is_internal(to_email),
            account_id=recipient_account_id,
        )
        c = upsert_contact(client, c)
        recipients.append(c)

    # Direction: override payload value for outbound (shared_inbox.py always sets "inbound")
    direction = Direction.OUTBOUND if outbound else Direction(payload.direction)

    # Parse occurred_at — fixtures use "Z" suffix (Python 3.11+ fromisoformat supports it)
    occurred_at = datetime.fromisoformat(payload.occurred_at)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)

    now = datetime.now(UTC)
    signal = Signal(
        id=uuid4(),
        workspace_id=workspace_id,
        account_id=None,
        source_type=SourceType(payload.source_type),
        external_id=payload.external_id,
        thread_id=payload.thread_id,
        direction=direction,
        channel=Channel(payload.channel),
        occurred_at=occurred_at,
        created_at=now,
        updated_at=now,
        subject=payload.subject,
        body=payload.body,
        author_contact_id=author_contact_id,
        recipient_contact_ids=[r.id for r in recipients],
        routing_method=None,
        routing_confidence=None,
        routing_warning=None,
        deleted_at=None,
    )
    signal, _duplicate = insert_signal(client, signal)

    analytics.track(
        "Signal Ingested",
        workspace_id,
        {
            "account_id": str(signal.account_id),
            "routing_method": str(signal.routing_method) if signal.routing_method else None,
            "source_type": str(signal.source_type),
            "direction": str(signal.direction),
        },
    )

    insert_audit_event(
        client,
        workspace_id=workspace_id,
        actor_type=ActorType.WORKER,
        actor_id="worker",
        action=AuditAction.SIGNAL_INGESTED,
        resource_type="signal",
        resource_id=signal.id,
        metadata={"external_id": signal.external_id},
    )

    return NormalizeResult(signal=signal, author_contact=author, recipient_contacts=recipients)
