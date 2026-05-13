from datetime import datetime
from uuid import UUID

from postgrest.exceptions import APIError

from src.domain.outreach_draft import DraftIntent, DraftStatus, GeneratedBy, OutreachDraft
from supabase import Client


def _from_row(row: dict) -> OutreachDraft:
    return OutreachDraft(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        account_id=UUID(row["account_id"]),
        contact_id=UUID(row["contact_id"]) if row.get("contact_id") else None,
        intent=DraftIntent(row["intent"]),
        user_context=row.get("user_context"),
        subject=row["subject"],
        body=row["body"],
        generated_by=GeneratedBy(row["generated_by"]),
        status=DraftStatus(row["status"]),
        sent_at=datetime.fromisoformat(row["sent_at"]) if row.get("sent_at") else None,
        sent_by_user_id=UUID(row["sent_by_user_id"]) if row.get("sent_by_user_id") else None,
        model=row.get("model"),
        template_id=row.get("template_id"),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        deleted_at=datetime.fromisoformat(row["deleted_at"]) if row.get("deleted_at") else None,
    )


def get_draft_by_id(client: Client, draft_id: UUID) -> OutreachDraft | None:
    result = (
        client.table("outreach_drafts")
        .select("*")
        .eq("id", str(draft_id))
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    return _from_row(result.data[0]) if result.data else None


def get_active_draft(client: Client, account_id: UUID) -> OutreachDraft | None:
    """Return the unsent draft for this account, or None."""
    result = (
        client.table("outreach_drafts")
        .select("*")
        .eq("account_id", str(account_id))
        .eq("status", DraftStatus.DRAFT)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    return _from_row(result.data[0]) if result.data else None


def get_sent_drafts(client: Client, account_id: UUID) -> list[OutreachDraft]:
    result = (
        client.table("outreach_drafts")
        .select("*")
        .eq("account_id", str(account_id))
        .eq("status", DraftStatus.SENT)
        .is_("deleted_at", "null")
        .order("sent_at", desc=True)
        .execute()
    )
    return [_from_row(r) for r in result.data]


def save_draft(client: Client, draft: OutreachDraft) -> OutreachDraft:
    """Insert or update the active draft for this account."""
    existing = get_active_draft(client, draft.account_id)
    data = {
        "workspace_id": str(draft.workspace_id),
        "account_id": str(draft.account_id),
        "contact_id": str(draft.contact_id) if draft.contact_id else None,
        "intent": draft.intent,
        "user_context": draft.user_context,
        "subject": draft.subject,
        "body": draft.body,
        "generated_by": draft.generated_by,
        "status": DraftStatus.DRAFT,
        "model": draft.model,
        "template_id": draft.template_id,
    }
    if existing:
        result = client.table("outreach_drafts").update(data).eq("id", str(existing.id)).execute()
    else:
        data["id"] = str(draft.id)
        try:
            result = client.table("outreach_drafts").insert(data).execute()
        except APIError as exc:
            if exc.code == "23505":
                # Concurrent request inserted between our get_active_draft check and this insert.
                # Return the winning row rather than propagating a constraint error.
                existing = get_active_draft(client, draft.account_id)
                if existing:
                    return existing
                raise
            raise
    return _from_row(result.data[0])


def mark_draft_sent(
    client: Client,
    draft_id: UUID,
    sent_at: datetime,
    sent_by_user_id: UUID,
) -> OutreachDraft:
    result = (
        client.table("outreach_drafts")
        .update(
            {
                "status": DraftStatus.SENT,
                "sent_at": sent_at.isoformat(),
                "sent_by_user_id": str(sent_by_user_id),
            }
        )
        .eq("id", str(draft_id))
        .eq("status", DraftStatus.DRAFT)
        .execute()
    )
    if not result.data:
        raise ValueError("Draft already sent or not found")
    return _from_row(result.data[0])
