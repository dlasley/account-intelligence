"""DB access functions for the integration_state table (ADR-020 D5).

Tracks poll cursor and error counters per credential. Mutable table with
created_at + updated_at (trigger-maintained). One row per credential (UNIQUE).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from supabase import Client


@dataclass
class IntegrationState:
    id: UUID
    workspace_id: UUID
    credential_id: UUID
    kind: str
    cursor: str | None
    last_polled_at: datetime | None
    last_success_at: datetime | None
    consecutive_errors: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


def _from_row(row: dict) -> IntegrationState:
    return IntegrationState(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        credential_id=UUID(row["credential_id"]),
        kind=row["kind"],
        cursor=row.get("cursor"),
        last_polled_at=(
            datetime.fromisoformat(row["last_polled_at"]) if row.get("last_polled_at") else None
        ),
        last_success_at=(
            datetime.fromisoformat(row["last_success_at"]) if row.get("last_success_at") else None
        ),
        consecutive_errors=row.get("consecutive_errors", 0),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        deleted_at=datetime.fromisoformat(row["deleted_at"]) if row.get("deleted_at") else None,
    )


def get_or_create_integration_state(
    client: Client,
    workspace_id: UUID,
    credential_id: UUID,
    kind: str,
) -> IntegrationState:
    """Fetch the state row for a credential, creating it if missing."""
    result = (
        client.table("integration_state")
        .select("*")
        .eq("credential_id", str(credential_id))
        .limit(1)
        .execute()
    )
    if result.data:
        return _from_row(result.data[0])

    # Row does not exist yet — insert and return.
    insert_result = (
        client.table("integration_state")
        .insert(
            {
                "workspace_id": str(workspace_id),
                "credential_id": str(credential_id),
                "kind": kind,
            }
        )
        .execute()
    )
    return _from_row(insert_result.data[0])


def advance_cursor(client: Client, state_id: UUID, cursor: str) -> None:
    """Update cursor to the last successfully-processed ID.

    Called only after a batch of signals is confirmed written. If the worker
    crashes before this call, the next poll re-fetches from the previous cursor;
    dedup handles re-seen IDs as no-ops (at-least-once semantics).
    """
    client.table("integration_state").update({"cursor": cursor}).eq(
        "id", str(state_id)
    ).execute()


def record_poll_success(client: Client, state_id: UUID) -> None:
    """Set last_polled_at, last_success_at = now(); reset consecutive_errors to 0."""
    now = datetime.now(UTC).isoformat()
    client.table("integration_state").update(
        {
            "last_polled_at": now,
            "last_success_at": now,
            "consecutive_errors": 0,
        }
    ).eq("id", str(state_id)).execute()


def record_poll_error(client: Client, state_id: UUID) -> None:
    """Set last_polled_at = now(); increment consecutive_errors via RPC-safe update.

    Uses a raw RPC-style increment via PostgREST update with `consecutive_errors`
    read from the current row. For v1 scale (single Cloud Run instance per poll
    cycle) this is safe; no concurrent writes to the same state row.
    """
    # Fetch current consecutive_errors first (PostgREST doesn't support field += 1 directly)
    row = (
        client.table("integration_state")
        .select("consecutive_errors")
        .eq("id", str(state_id))
        .limit(1)
        .execute()
    )
    current = row.data[0]["consecutive_errors"] if row.data else 0
    now = datetime.now(UTC).isoformat()
    client.table("integration_state").update(
        {
            "last_polled_at": now,
            "consecutive_errors": current + 1,
        }
    ).eq("id", str(state_id)).execute()
