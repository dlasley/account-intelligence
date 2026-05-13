from datetime import datetime
from uuid import UUID

from src.domain.contact import Contact
from supabase import Client


def _from_row(row: dict) -> Contact:
    return Contact(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        account_id=UUID(row["account_id"]) if row.get("account_id") else None,
        email=row["email"],
        display_name=row.get("display_name"),
        is_internal=row["is_internal"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        deleted_at=datetime.fromisoformat(row["deleted_at"]) if row.get("deleted_at") else None,
    )


def upsert_contact(client: Client, contact: Contact) -> Contact:
    # Uses upsert_contact_safe RPC (ADR-013) — COALESCE guards prevent NULL-clobbering
    # an existing account_id or display_name. The RPC executes under the caller's role
    # so RLS on contacts continues to apply.
    params = {
        "p_workspace_id": str(contact.workspace_id),
        "p_email": contact.email,
        "p_display_name": contact.display_name,
        "p_is_internal": contact.is_internal,
        "p_account_id": str(contact.account_id) if contact.account_id else None,
    }
    result = client.rpc("upsert_contact_safe", params).execute()
    # supabase-py: .rpc() with RETURNS <row_type> unwraps the row to a dict directly,
    # unlike .table().upsert() which returns list[dict]. Both shapes seen in tests.
    row = result.data[0] if isinstance(result.data, list) else result.data
    return _from_row(row)


def get_contact_by_id(client: Client, contact_id: UUID, workspace_id: UUID) -> Contact | None:
    result = (
        client.table("contacts")
        .select("*")
        .eq("id", str(contact_id))
        .eq("workspace_id", str(workspace_id))
        .limit(1)
        .execute()
    )
    return _from_row(result.data[0]) if result.data else None


def get_contact_by_email(client: Client, workspace_id: UUID, email: str) -> Contact | None:
    result = (
        client.table("contacts")
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .eq("email", email.lower())
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    return _from_row(result.data[0]) if result.data else None


def get_contacts_by_ids(client: Client, contact_ids: list[UUID]) -> dict[UUID, Contact]:
    """Fetch contacts by a list of IDs. Returns a dict keyed by contact UUID."""
    if not contact_ids:
        return {}
    result = client.table("contacts").select("*").in_("id", [str(c) for c in contact_ids]).execute()
    return {UUID(row["id"]): _from_row(row) for row in result.data}


def get_contacts_for_account(
    client: Client,
    workspace_id: UUID,
    account_id: UUID,
) -> list[Contact]:
    """Return all non-deleted contacts at the account, ordered by created_at.
    Used by the narrative generator to populate the VALID CONTACTS whitelist."""
    result = (
        client.table("contacts")
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .eq("account_id", str(account_id))
        .is_("deleted_at", "null")
        .order("created_at")
        .execute()
    )
    return [_from_row(row) for row in result.data]
