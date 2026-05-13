from datetime import datetime
from uuid import UUID

from src.domain.workspace import Workspace
from supabase import Client


def _from_row(row: dict) -> Workspace:
    return Workspace(
        id=UUID(row["id"]),
        organization_id=UUID(row["organization_id"]),
        slug=row["slug"],
        name=row["name"],
        internal_domains=tuple(row.get("internal_domains") or []),
        crm_url_template=row.get("crm_url_template"),
        crm_portal_id=row.get("crm_portal_id"),
        outbound_sender_email=row.get("outbound_sender_email"),
        outbound_sender_name=row.get("outbound_sender_name"),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        deleted_at=datetime.fromisoformat(row["deleted_at"]) if row.get("deleted_at") else None,
    )


def upsert_workspace(client: Client, workspace: Workspace) -> Workspace:
    data = {
        "id": str(workspace.id),
        "organization_id": str(workspace.organization_id),
        "slug": workspace.slug,
        "name": workspace.name,
        "internal_domains": list(workspace.internal_domains),
        "crm_url_template": workspace.crm_url_template,
        "crm_portal_id": workspace.crm_portal_id,
        "outbound_sender_email": workspace.outbound_sender_email,
        "outbound_sender_name": workspace.outbound_sender_name,
        "created_at": workspace.created_at.isoformat(),
        "updated_at": workspace.updated_at.isoformat(),
        "deleted_at": workspace.deleted_at.isoformat() if workspace.deleted_at else None,
    }
    result = client.table("workspaces").upsert(data, on_conflict="slug").execute()
    return _from_row(result.data[0])


def get_workspace_by_id(client: Client, workspace_id: UUID) -> Workspace | None:
    result = (
        client.table("workspaces")
        .select("*")
        .eq("id", str(workspace_id))
        .is_("deleted_at", "null")
        .execute()
    )
    if not result.data:
        return None
    return _from_row(result.data[0])


def get_workspace_by_slug(client: Client, slug: str) -> Workspace | None:
    result = (
        client.table("workspaces").select("*").eq("slug", slug).is_("deleted_at", "null").execute()
    )
    if not result.data:
        return None
    return _from_row(result.data[0])


def get_all_workspaces(client: Client) -> list[Workspace]:
    result = client.table("workspaces").select("*").is_("deleted_at", "null").execute()
    return [_from_row(row) for row in result.data]
