from datetime import datetime
from uuid import UUID

from src.domain.health_snapshot import HealthSnapshot
from supabase import Client


def _from_row(row: dict) -> HealthSnapshot:
    return HealthSnapshot(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        account_id=UUID(row["account_id"]),
        overall_score=int(row["overall_score"]) if row.get("overall_score") is not None else None,
        dimension_scores=row.get("dimension_scores") or {},
        formula_version=row["formula_version"],
        computed_at=datetime.fromisoformat(row["computed_at"]),
        superseded_at=(
            datetime.fromisoformat(row["superseded_at"]) if row.get("superseded_at") else None
        ),
    )


def insert_health_snapshot(client: Client, snapshot: HealthSnapshot) -> HealthSnapshot:
    data = {
        "id": str(snapshot.id),
        "workspace_id": str(snapshot.workspace_id),
        "account_id": str(snapshot.account_id),
        "overall_score": snapshot.overall_score,
        "dimension_scores": snapshot.dimension_scores,
        "formula_version": snapshot.formula_version,
        "computed_at": snapshot.computed_at.isoformat(),
        "superseded_at": snapshot.superseded_at.isoformat() if snapshot.superseded_at else None,
    }
    result = client.table("account_health_snapshots").insert(data).execute()
    return _from_row(result.data[0])


def supersede_health_snapshot(client: Client, account_id: UUID, superseded_at: datetime) -> int:
    result = (
        client.table("account_health_snapshots")
        .update({"superseded_at": superseded_at.isoformat()})
        .eq("account_id", str(account_id))
        .is_("superseded_at", "null")
        .execute()
    )
    return len(result.data)
