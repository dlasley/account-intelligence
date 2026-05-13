from datetime import datetime
from uuid import UUID

from src.domain.organization import Organization
from supabase import Client


def _from_row(row: dict) -> Organization:
    return Organization(
        id=UUID(row["id"]),
        slug=row["slug"],
        name=row["name"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        deleted_at=datetime.fromisoformat(row["deleted_at"]) if row.get("deleted_at") else None,
    )


def upsert_organization(client: Client, org: Organization) -> Organization:
    data = {
        "id": str(org.id),
        "slug": org.slug,
        "name": org.name,
        "created_at": org.created_at.isoformat(),
        "updated_at": org.updated_at.isoformat(),
        "deleted_at": org.deleted_at.isoformat() if org.deleted_at else None,
    }
    result = client.table("organizations").upsert(data, on_conflict="slug").execute()
    return _from_row(result.data[0])
