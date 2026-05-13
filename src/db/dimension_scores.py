import logging
from datetime import datetime
from uuid import UUID

from src.domain.dimension_score import DimensionScore, ScoredBy
from supabase import Client

logger = logging.getLogger(__name__)


def _from_row(row: dict) -> DimensionScore:
    return DimensionScore(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        account_id=UUID(row["account_id"]),
        dimension_id=UUID(row["dimension_id"]),
        score=int(row["score"]),
        rationale=row.get("rationale"),
        scored_by=ScoredBy(row["scored_by"]),
        metadata=row.get("metadata"),
        scored_at=datetime.fromisoformat(row["scored_at"]),
        superseded_at=(
            datetime.fromisoformat(row["superseded_at"]) if row.get("superseded_at") else None
        ),
    )


def insert_dimension_score(client: Client, score: DimensionScore) -> DimensionScore:
    data = {
        "id": str(score.id),
        "workspace_id": str(score.workspace_id),
        "account_id": str(score.account_id),
        "dimension_id": str(score.dimension_id),
        "score": score.score,
        "rationale": score.rationale,
        "scored_by": score.scored_by,
        "metadata": score.metadata,
        "scored_at": score.scored_at.isoformat(),
        "superseded_at": score.superseded_at.isoformat() if score.superseded_at else None,
    }
    result = client.table("account_dimension_scores").insert(data).execute()
    return _from_row(result.data[0])


def supersede_dimension_score(
    client: Client, account_id: UUID, dimension_id: UUID, superseded_at: datetime
) -> int:
    result = (
        client.table("account_dimension_scores")
        .update({"superseded_at": superseded_at.isoformat()})
        .eq("account_id", str(account_id))
        .eq("dimension_id", str(dimension_id))
        .is_("superseded_at", "null")
        .execute()
    )
    return len(result.data)


def get_current_scores(
    client: Client, workspace_id: UUID, account_id: UUID
) -> list[DimensionScore]:
    result = (
        client.table("account_dimension_scores")
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .eq("account_id", str(account_id))
        .is_("superseded_at", "null")
        .execute()
    )
    return [_from_row(row) for row in result.data]
