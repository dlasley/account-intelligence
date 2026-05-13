from datetime import datetime
from uuid import UUID

from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType
from supabase import Client


def _from_row(row: dict) -> Signal:
    return Signal(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        account_id=UUID(row["account_id"]) if row.get("account_id") else None,
        source_type=SourceType(row["source_type"]),
        external_id=row["external_id"],
        thread_id=row.get("thread_id"),
        direction=Direction(row["direction"]),
        channel=Channel(row["channel"]),
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        subject=row.get("subject"),
        body=row["body"],
        author_contact_id=UUID(row["author_contact_id"]) if row.get("author_contact_id") else None,
        recipient_contact_ids=[UUID(r) for r in (row.get("recipient_contact_ids") or [])],
        routing_method=RoutingMethod(row["routing_method"]) if row.get("routing_method") else None,
        routing_confidence=row.get("routing_confidence"),
        routing_warning=row.get("routing_warning"),
        deleted_at=datetime.fromisoformat(row["deleted_at"]) if row.get("deleted_at") else None,
        event_name=row.get("event_name"),
        event_properties=row.get("event_properties") or {},
        event_id=row.get("event_id"),
        signal_metadata=row.get("signal_metadata") or {},
    )


def _to_dict(signal: Signal) -> dict:
    return {
        "id": str(signal.id),
        "workspace_id": str(signal.workspace_id),
        "account_id": str(signal.account_id) if signal.account_id else None,
        "source_type": signal.source_type,
        "external_id": signal.external_id,
        "thread_id": signal.thread_id,
        "direction": signal.direction,
        "channel": signal.channel,
        "occurred_at": signal.occurred_at.isoformat(),
        "created_at": signal.created_at.isoformat(),
        "updated_at": signal.updated_at.isoformat(),
        "subject": signal.subject,
        "body": signal.body,
        "author_contact_id": str(signal.author_contact_id) if signal.author_contact_id else None,
        "recipient_contact_ids": [str(r) for r in signal.recipient_contact_ids],
        "routing_method": signal.routing_method,
        "routing_confidence": signal.routing_confidence,
        "routing_warning": signal.routing_warning,
        "deleted_at": signal.deleted_at.isoformat() if signal.deleted_at else None,
        "event_name": signal.event_name,
        "event_properties": signal.event_properties,
        "event_id": signal.event_id,
        "signal_metadata": signal.signal_metadata,
    }


def insert_signal(client: Client, signal: Signal) -> tuple[Signal, bool]:
    """Insert a signal. Returns (signal, duplicate).

    Idempotent on (workspace_id, external_id) and (workspace_id, event_id) when
    event_id is set. On either conflict, returns the existing row unchanged with
    duplicate=True. Callers must not trust routing_method/account_id on the
    returned object after a (workspace_id, external_id) conflict — those fields
    may already be set from a prior run; update_signal_routing() is always
    called after this in the email pipeline, so stale routing fields are
    overwritten.
    """
    # First pass: try insert with conflict-ignore on the (workspace_id, external_id) constraint
    result = (
        client.table("signals")
        .upsert(_to_dict(signal), on_conflict="workspace_id,external_id", ignore_duplicates=True)
        .execute()
    )
    if result.data:
        return _from_row(result.data[0]), False

    # Conflict on (workspace_id, external_id). Fetch the existing row.
    existing = (
        client.table("signals")
        .select("*")
        .eq("workspace_id", str(signal.workspace_id))
        .eq("external_id", signal.external_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        return _from_row(existing.data[0]), True

    # Edge case: conflict on (workspace_id, event_id) instead. Fetch by event_id.
    if signal.event_id:
        by_event = (
            client.table("signals")
            .select("*")
            .eq("workspace_id", str(signal.workspace_id))
            .eq("event_id", signal.event_id)
            .limit(1)
            .execute()
        )
        if by_event.data:
            return _from_row(by_event.data[0]), True

    raise RuntimeError("insert_signal: upsert returned empty and no existing row found")


def get_signals_for_account(client: Client, workspace_id: UUID, account_id: UUID) -> list[Signal]:
    """Return all signals for an account, ordered by occurred_at DESC."""
    result = (
        client.table("signals")
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .eq("account_id", str(account_id))
        .is_("deleted_at", "null")
        .order("occurred_at", desc=True)
        .execute()
    )
    return [_from_row(row) for row in result.data]


def get_signals_by_thread_id(client: Client, workspace_id: UUID, thread_id: str) -> list[Signal]:
    # Returns routed signals only (account_id IS NOT NULL), ordered most-recent first
    result = (
        client.table("signals")
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .eq("thread_id", thread_id)
        .not_.is_("account_id", "null")
        .order("created_at", desc=True)
        .execute()
    )
    return [_from_row(row) for row in result.data]


def update_signal_routing(
    client: Client,
    signal_id: UUID,
    account_id: UUID | None,
    routing_method: RoutingMethod,
    routing_confidence: float,
    routing_warning: str | None,
) -> None:
    client.table("signals").update(
        {
            "account_id": str(account_id) if account_id else None,
            "routing_method": routing_method,
            "routing_confidence": routing_confidence,
            "routing_warning": routing_warning,
        }
    ).eq("id", str(signal_id)).execute()
