"""Shared normalizer for structured third-party signals (ADR-020 D7).

Each vendor adapter transforms its native payload into a StructuredSignalInput.
This module takes StructuredSignalInput and produces an IngestResult without
any knowledge of which vendor it came from.
"""

from __future__ import annotations

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
from src.pipeline.product_event import IngestResult
from supabase import Client

# ─── Input model ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SignalParticipant:
    email: str
    name: str | None = None
    role: str = "customer"  # "customer" | "internal" | "unknown"


@dataclass(frozen=True)
class StructuredSignalInput:
    # Required
    external_id: str  # adapter-namespaced: "plain:<event_id>", "granola:<note_id>"
    kind: str  # "ticket" | "meeting_note" — maps to Channel
    occurred_at: datetime  # tz-aware
    body: str  # full text content

    # Conditionally required
    participants: list[SignalParticipant]  # empty = UNMATCHED routing

    # Optional
    subject: str | None = None
    direction: Direction = Direction.INBOUND
    thread_id: str | None = None
    metadata: dict = field(default_factory=dict)


# ─── Inference tables (credential_kind → SourceType, kind → Channel) ─────────

_CREDENTIAL_KIND_TO_SOURCE_TYPE: dict[str, SourceType] = {
    "plain_webhook_secret": SourceType.PLAIN_TICKET,
    "pylon_webhook_secret": SourceType.PYLON_TICKET,
    "granola_api_key": SourceType.GRANOLA_NOTE,
}

_KIND_TO_CHANNEL: dict[str, Channel] = {
    "ticket": Channel.TICKET,
    "meeting_note": Channel.MEETING_NOTE,
}

# Confidence values mirroring product_event normalizer (ADR-020 D11)
_ROUTING_CONFIDENCE: dict[RoutingMethod, float] = {
    RoutingMethod.API_KEY_IDENTITY: 1.0,
    RoutingMethod.AUTO_DISCOVERY: 0.3,
    RoutingMethod.UNMATCHED: 0.0,
}


# ─── Normalizer ──────────────────────────────────────────────────────────────


def normalize_structured_signal(
    input: StructuredSignalInput,
    workspace_id: UUID,
    workspace_name: str,
    credential_id: UUID,
    credential_kind: str,
    client: Client,
) -> IngestResult:
    """Normalize a StructuredSignalInput into a Signal row.

    Contact routing (mirrors normalize_product_event):
      - Participant email matches existing Contact  -> API_KEY_IDENTITY
      - Participant email new, domain matches acct  -> AUTO_DISCOVERY, account_id set
      - Participant email new, no domain match      -> AUTO_DISCOVERY, account_id = None
      - No participants                             -> UNMATCHED

    Dedup: unique constraint on (workspace_id, external_id). On collision, returns
    the existing signal with duplicate=True — never raises.

    Audit event: fire-and-log; never fails signal ingestion.
    """
    source_type = _CREDENTIAL_KIND_TO_SOURCE_TYPE.get(credential_kind)
    if source_type is None:
        raise ValueError(f"unknown credential_kind: {credential_kind!r}")

    channel = _KIND_TO_CHANNEL.get(input.kind)
    if channel is None:
        raise ValueError(f"unknown structured signal kind: {input.kind!r}")

    now = datetime.now(UTC)

    # ── Contact routing ────────────────────────────────────────────────────
    resolved_contacts: list[tuple[Contact, str]] = []  # (contact, role)
    routing_method = RoutingMethod.UNMATCHED
    account_id: UUID | None = None

    for participant in input.participants:
        if not participant.email:
            continue

        email_lower = participant.email.lower()
        existing = get_contact_by_email(client, workspace_id, email_lower)
        if existing is not None:
            resolved_contacts.append((existing, participant.role))
            if routing_method != RoutingMethod.API_KEY_IDENTITY:
                routing_method = RoutingMethod.API_KEY_IDENTITY
            if account_id is None:
                account_id = existing.account_id
        else:
            discovered_account_id = get_account_by_email_domain(client, workspace_id, email_lower)
            new_contact = make_contact(
                workspace_id,
                email_lower,
                display_name=participant.name,
                account_id=discovered_account_id,
            )
            contact = upsert_contact(client, new_contact)
            resolved_contacts.append((contact, participant.role))
            if routing_method == RoutingMethod.UNMATCHED:
                routing_method = RoutingMethod.AUTO_DISCOVERY
            if account_id is None:
                account_id = discovered_account_id

    # ── Determine author and recipient contacts ────────────────────────────
    # author = first participant with role='customer', or first overall if none
    author_contact: Contact | None = None
    recipient_contacts: list[Contact] = []

    if resolved_contacts:
        # Prefer the first customer-role contact as author
        author_idx = 0
        for i, (_, role) in enumerate(resolved_contacts):
            if role == "customer":
                author_idx = i
                break
        author_contact = resolved_contacts[author_idx][0]
        recipient_contacts = [c for j, (c, _) in enumerate(resolved_contacts) if j != author_idx]

    signal = Signal(
        id=uuid4(),
        workspace_id=workspace_id,
        account_id=account_id,
        source_type=source_type,
        external_id=input.external_id,
        thread_id=input.thread_id,
        direction=input.direction,
        channel=channel,
        occurred_at=input.occurred_at,
        created_at=now,
        updated_at=now,
        subject=input.subject,
        body=input.body,
        author_contact_id=author_contact.id if author_contact else None,
        recipient_contact_ids=[c.id for c in recipient_contacts],
        routing_method=routing_method,
        routing_confidence=_ROUTING_CONFIDENCE[routing_method],
        routing_warning=None,
        deleted_at=None,
        signal_metadata=input.metadata,
    )

    persisted, duplicate = insert_signal(client, signal)

    if not duplicate:
        try:
            insert_audit_event(
                client,
                workspace_id=workspace_id,
                actor_type=ActorType.API_KEY,
                actor_id=str(credential_id),
                action=AuditAction.SIGNAL_INGESTED,
                resource_type="signal",
                resource_id=persisted.id,
                metadata={"source": credential_kind, "kind": input.kind},
            )
        except Exception:
            pass  # fire-and-log; never fail signal ingestion

    return IngestResult(signal=persisted, duplicate=duplicate)
