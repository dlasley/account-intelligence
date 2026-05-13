from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.db.accounts import get_account_by_email_domain
from src.db.audit import insert_audit_event
from src.db.contacts import get_contact_by_email, upsert_contact
from src.db.signals import insert_signal
from src.domain.contact import Contact
from src.domain.events import ActorType, AuditAction
from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType
from src.pipeline._contact_factory import make_contact
from supabase import Client

# Allowlist: ASCII alphanumeric, underscore, hyphen, dot. Max 100 chars.
# Guards against prompt-injection via caller-controlled event_name strings that
# are interpolated into the LLM narrative prompt (ADR-016 security review).
_EVENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def validate_event_name(event_name: str) -> str:
    """Validate event_name at the ingestion boundary.

    Raises ValueError if the name contains characters outside the allowed set
    or exceeds the length cap. Call before constructing ProductEvent from
    untrusted input.
    """
    if not _EVENT_NAME_PATTERN.match(event_name):
        raise ValueError(
            f"Invalid event_name: must be 1-100 chars of [A-Za-z0-9._-], got {event_name!r}"
        )
    return event_name


@dataclass(frozen=True)
class ProductEvent:
    contact_email: str | None
    event_name: str
    event_properties: dict = field(default_factory=dict)
    event_id: str | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class IngestResult:
    signal: Signal
    duplicate: bool


def _synthesize_subject(contact_email: str | None, event_name: str) -> str:
    who = contact_email or "anonymous"
    return f'{who} performed "{event_name}"'


def _synthesize_body(
    contact_email: str | None,
    event_name: str,
    event_properties: dict,
    workspace_name: str,
    occurred_at: datetime,
) -> str:
    who = contact_email or "anonymous"
    props = ", ".join(f"{k}={v}" for k, v in event_properties.items()) or "(none)"
    return (
        f"At {occurred_at.isoformat()}, contact {who} performed event "
        f'"{event_name}" in {workspace_name}\'s product.\n'
        f"Properties: {props}"
    )


def normalize_product_event(
    event: ProductEvent,
    workspace_id: UUID,
    workspace_name: str,
    api_key_id: UUID,
    client: Client,
) -> IngestResult:
    """Insert a product-event signal. Auto-create Contact if email is new.

    Routing rules:
      - email present + Contact exists  -> 'api_key_identity', account inherited
      - email present + Contact missing -> 'auto_discovery', account=NULL
      - email missing                   -> 'unmatched', account=NULL
    """
    occurred_at = event.occurred_at or datetime.now(UTC)
    now = datetime.now(UTC)

    author_contact: Contact | None = None
    routing_method: RoutingMethod = RoutingMethod.UNMATCHED
    account_id: UUID | None = None

    if event.contact_email:
        existing = get_contact_by_email(client, workspace_id, event.contact_email.lower())
        if existing is not None:
            author_contact = existing
            account_id = existing.account_id
            routing_method = RoutingMethod.API_KEY_IDENTITY
        else:
            discovered_account_id = get_account_by_email_domain(
                client, workspace_id, event.contact_email
            )
            new_contact = make_contact(
                workspace_id, event.contact_email, account_id=discovered_account_id
            )
            author_contact = upsert_contact(client, new_contact)
            account_id = discovered_account_id  # carry account linkage to the signal
            routing_method = RoutingMethod.AUTO_DISCOVERY

    signal = Signal(
        id=uuid4(),
        workspace_id=workspace_id,
        account_id=account_id,
        source_type=SourceType.PRODUCT_EVENT,
        external_id=event.event_id or str(uuid4()),
        thread_id=None,
        direction=Direction.INBOUND,
        channel=Channel.PRODUCT,
        occurred_at=occurred_at,
        created_at=now,
        updated_at=now,
        subject=_synthesize_subject(event.contact_email, event.event_name),
        body=_synthesize_body(
            event.contact_email,
            event.event_name,
            event.event_properties,
            workspace_name,
            occurred_at,
        ),
        author_contact_id=author_contact.id if author_contact else None,
        recipient_contact_ids=[],
        routing_method=routing_method,
        routing_confidence=(
            1.0
            if routing_method == RoutingMethod.API_KEY_IDENTITY
            else 0.3
            if routing_method == RoutingMethod.AUTO_DISCOVERY
            else 0.0
        ),
        routing_warning=None,
        deleted_at=None,
        event_name=event.event_name,
        event_properties=event.event_properties,
        event_id=event.event_id,
    )

    persisted, duplicate = insert_signal(client, signal)

    if not duplicate:
        try:
            insert_audit_event(
                client,
                workspace_id=workspace_id,
                actor_type=ActorType.API_KEY,
                actor_id=str(api_key_id),
                action=AuditAction.SIGNAL_INGESTED,
                resource_type="signal",
                resource_id=persisted.id,
                metadata={"source": "product_event", "event_name": event.event_name},
            )
        except Exception:
            pass

    return IngestResult(signal=persisted, duplicate=duplicate)
