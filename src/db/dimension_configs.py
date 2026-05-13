import logging
from datetime import datetime
from uuid import UUID

from src.config.schema import DimensionScoringConfig
from src.domain.dimension_config import DimensionConfig
from supabase import Client

logger = logging.getLogger(__name__)


def _from_row(row: dict) -> DimensionConfig:
    return DimensionConfig(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        dimension_type=row["dimension_type"],
        name=row["name"],
        weight=float(row["weight"]),
        enabled=row["enabled"],
        config=row.get("config") or {},
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        deleted_at=datetime.fromisoformat(row["deleted_at"]) if row.get("deleted_at") else None,
    )


def get_dimension_configs(client: Client, workspace_id: UUID) -> list[DimensionConfig]:
    result = (
        client.table("health_dimension_configs")
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .is_("deleted_at", "null")
        .execute()
    )
    return [_from_row(row) for row in result.data]


def seed_dimension_configs(
    client: Client, workspace_id: UUID, dimensions: list[DimensionScoringConfig]
) -> None:
    existing = get_dimension_configs(client, workspace_id)
    existing_types = {d.dimension_type for d in existing}

    for dim in dimensions:
        if dim.dimension_type in existing_types:
            continue
        client.table("health_dimension_configs").insert(
            {
                "workspace_id": str(workspace_id),
                "dimension_type": dim.dimension_type,
                "name": dim.name,
                "weight": dim.weight,
                "enabled": dim.enabled,
                "config": dim.config,
            }
        ).execute()
        logger.info(
            "Seeded dimension config: %s for workspace %s", dim.dimension_type, workspace_id
        )
