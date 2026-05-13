from datetime import datetime
from uuid import UUID

from src.domain.narrative import Narrative
from supabase import Client


def _from_row(row: dict) -> Narrative:
    return Narrative(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        account_id=UUID(row["account_id"]),
        narrative=row["narrative"],
        engagement=row["engagement"],
        engagement_rationale=row["engagement_rationale"],
        sentiment=row.get("sentiment"),
        signal_window_start=datetime.fromisoformat(row["signal_window_start"]),
        signal_window_end=datetime.fromisoformat(row["signal_window_end"]),
        signals_considered=tuple(UUID(s) for s in (row.get("signals_considered") or [])),
        model=row["model"],
        prompt_version=row["prompt_version"],
        generated_at=datetime.fromisoformat(row["generated_at"]),
        superseded_at=(
            datetime.fromisoformat(row["superseded_at"]) if row.get("superseded_at") else None
        ),
    )


def insert_narrative(client: Client, narrative: Narrative) -> Narrative:
    """Insert a new narrative row. Caller must call supersede_current_narrative first."""
    data = {
        "id": str(narrative.id),
        "workspace_id": str(narrative.workspace_id),
        "account_id": str(narrative.account_id),
        "narrative": narrative.narrative,
        "engagement": narrative.engagement,
        "engagement_rationale": narrative.engagement_rationale,
        "sentiment": narrative.sentiment,
        "signal_window_start": narrative.signal_window_start.isoformat(),
        "signal_window_end": narrative.signal_window_end.isoformat(),
        "signals_considered": [str(s) for s in narrative.signals_considered],
        "model": narrative.model,
        "prompt_version": narrative.prompt_version,
        "generated_at": narrative.generated_at.isoformat(),
        "superseded_at": (narrative.superseded_at.isoformat() if narrative.superseded_at else None),
    }
    result = client.table("narratives").insert(data).execute()
    return _from_row(result.data[0])


def get_current_narrative(client: Client, workspace_id: UUID, account_id: UUID) -> Narrative | None:
    """Return the active (superseded_at IS NULL) narrative for this account, or None."""
    result = (
        client.table("narratives")
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .eq("account_id", str(account_id))
        .is_("superseded_at", "null")
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return _from_row(result.data[0])


def supersede_current_narrative(client: Client, account_id: UUID, superseded_at: datetime) -> int:
    """
    Set superseded_at on the current active narrative.
    Returns number of rows updated (0 if no prior narrative, 1 if superseded).
    """
    result = (
        client.table("narratives")
        .update({"superseded_at": superseded_at.isoformat()})
        .eq("account_id", str(account_id))
        .is_("superseded_at", "null")
        .execute()
    )
    return len(result.data)
