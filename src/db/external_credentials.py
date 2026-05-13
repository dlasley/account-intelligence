"""DB access functions for the external_credentials table (ADR-020 D3).

The worker reads secret_enc via service_role and decrypts in Python.
authenticated role is never granted secret_enc — callers using the
browser client must never call these functions.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from supabase import Client


@dataclass(frozen=True)
class ExternalCredential:
    id: UUID
    workspace_id: UUID
    kind: str
    direction: str
    label: str
    secret_enc: bytes  # AES-256-GCM ciphertext; decrypt with crypto.decrypt_secret
    key_hint: str
    metadata: dict
    is_active: bool
    last_verified_at: datetime | None
    error_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


def _from_row(row: dict) -> ExternalCredential:
    return ExternalCredential(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        kind=row["kind"],
        direction=row["direction"],
        label=row["label"],
        secret_enc=bytes(row["secret_enc"]),
        key_hint=row["key_hint"],
        metadata=row.get("metadata") or {},
        is_active=row["is_active"],
        last_verified_at=(
            datetime.fromisoformat(row["last_verified_at"]) if row.get("last_verified_at") else None
        ),
        error_at=datetime.fromisoformat(row["error_at"]) if row.get("error_at") else None,
        error_message=row.get("error_message"),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        deleted_at=datetime.fromisoformat(row["deleted_at"]) if row.get("deleted_at") else None,
    )


def get_credential_by_pylon_workspace_id(
    client: Client, pylon_workspace_id: str
) -> ExternalCredential | None:
    """Look up an active pylon_webhook_secret credential by its Pylon-side workspace ID.

    The Pylon workspace ID is stored in metadata->>'pylon_workspace_id'. Uses PostgREST
    JSON path filtering. Returns None if no matching credential exists.
    """
    result = (
        client.table("external_credentials")
        .select("*")
        .eq("kind", "pylon_webhook_secret")
        .eq("is_active", True)
        .is_("deleted_at", "null")
        .eq("metadata->>pylon_workspace_id", pylon_workspace_id)
        .limit(1)
        .execute()
    )
    return _from_row(result.data[0]) if result.data else None


def get_credential_by_plain_workspace_id(
    client: Client, plain_workspace_id: str
) -> ExternalCredential | None:
    """Look up an active plain_webhook_secret credential by its Plain-side workspace ID.

    The Plain workspace ID is stored in metadata->>'plain_workspace_id'. Uses PostgREST
    JSON path filtering. Returns None if no matching credential exists.
    """
    result = (
        client.table("external_credentials")
        .select("*")
        .eq("kind", "plain_webhook_secret")
        .eq("is_active", True)
        .is_("deleted_at", "null")
        .eq("metadata->>plain_workspace_id", plain_workspace_id)
        .limit(1)
        .execute()
    )
    return _from_row(result.data[0]) if result.data else None


def get_active_credentials_by_kind(
    client: Client, workspace_id: UUID, kind: str
) -> list[ExternalCredential]:
    """Return all active credentials of a given kind for a workspace."""
    result = (
        client.table("external_credentials")
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .eq("kind", kind)
        .eq("is_active", True)
        .is_("deleted_at", "null")
        .execute()
    )
    return [_from_row(row) for row in result.data]


def mark_credential_error(client: Client, credential_id: UUID, message: str) -> None:
    """Set error_at = now() and error_message on a credential row."""
    from datetime import UTC, datetime

    client.table("external_credentials").update(
        {
            "error_at": datetime.now(UTC).isoformat(),
            "error_message": message[:500],  # truncate per ADR-020 D3
        }
    ).eq("id", str(credential_id)).execute()


def clear_credential_error(client: Client, credential_id: UUID) -> None:
    """Clear error_at / error_message after a successful interaction."""
    client.table("external_credentials").update(
        {"error_at": None, "error_message": None}
    ).eq("id", str(credential_id)).execute()


def deactivate_credential(client: Client, credential_id: UUID) -> None:
    """Set is_active = false. Called when consecutive_errors exceeds threshold."""
    client.table("external_credentials").update({"is_active": False}).eq(
        "id", str(credential_id)
    ).execute()
