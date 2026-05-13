from datetime import UTC, datetime
from uuid import UUID

from src.domain.raw_inbound_event import ParseStatus, RawInboundEvent
from src.domain.signal import SourceType
from supabase import Client


def _from_row(row: dict) -> RawInboundEvent:
    return RawInboundEvent(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        received_at=datetime.fromisoformat(row["received_at"]),
        source_type=SourceType(row["source_type"]),
        raw_payload=row["raw_payload"],
        parse_status=ParseStatus(row["parse_status"]),
        signal_id=UUID(row["signal_id"]) if row.get("signal_id") else None,
        error_detail=row.get("error_detail"),
        processed_at=(
            datetime.fromisoformat(row["processed_at"]) if row.get("processed_at") else None
        ),
    )


def insert_raw_event(client: Client, event: RawInboundEvent) -> RawInboundEvent:
    data = {
        "id": str(event.id),
        "workspace_id": str(event.workspace_id),
        "received_at": event.received_at.isoformat(),
        "source_type": event.source_type,
        "raw_payload": event.raw_payload,
        "parse_status": event.parse_status,
        "signal_id": str(event.signal_id) if event.signal_id else None,
        "error_detail": event.error_detail,
        "processed_at": event.processed_at.isoformat() if event.processed_at else None,
    }
    result = client.table("raw_inbound_events").insert(data).execute()
    return _from_row(result.data[0])


def mark_processed(client: Client, event_id: UUID, signal_id: UUID) -> None:
    client.table("raw_inbound_events").update(
        {
            "parse_status": ParseStatus.PROCESSED,
            "signal_id": str(signal_id),
            "processed_at": datetime.now(UTC).isoformat(),
        }
    ).eq("id", str(event_id)).execute()


def mark_failed(client: Client, event_id: UUID, error: str) -> None:
    client.table("raw_inbound_events").update(
        {
            "parse_status": ParseStatus.FAILED,
            "error_detail": error,
            "processed_at": datetime.now(UTC).isoformat(),
        }
    ).eq("id", str(event_id)).execute()
