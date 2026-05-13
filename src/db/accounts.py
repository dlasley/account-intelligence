import logging
import re
from datetime import datetime
from uuid import UUID

import src.analytics as analytics
from src.domain.account import Account, AccountStatus, Vertical
from supabase import Client

logger = logging.getLogger(__name__)

# RFC-1035-shaped domain: labels of [a-z0-9-] (≤63 chars, no leading/trailing dash),
# joined by dots. Rejects PostgREST filter metacharacters (',', '{', '}', '.eq.', etc.)
# before they reach `or_()` interpolation in get_account_by_email_domain.
_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)+$"
)


def _from_row(row: dict) -> Account:
    return Account(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        slug=row["slug"],
        name=row["name"],
        primary_domain=row.get("primary_domain"),
        additional_domains=row.get("additional_domains") or [],
        vertical=Vertical(row["vertical"]) if row.get("vertical") else None,
        crm_record_id=row.get("crm_record_id"),
        status=AccountStatus(row["status"]),
        last_narrative_generated_at=(
            datetime.fromisoformat(row["last_narrative_generated_at"])
            if row.get("last_narrative_generated_at")
            else None
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        deleted_at=datetime.fromisoformat(row["deleted_at"]) if row.get("deleted_at") else None,
        frequency_multiplier=float(row.get("frequency_multiplier", 1.0)),
        overall_health_score=row.get("overall_health_score"),
    )


def upsert_account(client: Client, account: Account) -> Account:
    data = {
        "id": str(account.id),
        "workspace_id": str(account.workspace_id),
        "slug": account.slug,
        "name": account.name,
        "primary_domain": account.primary_domain,
        "additional_domains": account.additional_domains,
        "vertical": account.vertical,
        "crm_record_id": account.crm_record_id,
        "status": account.status,
        "last_narrative_generated_at": (
            account.last_narrative_generated_at.isoformat()
            if account.last_narrative_generated_at
            else None
        ),
        "created_at": account.created_at.isoformat(),
        "updated_at": account.updated_at.isoformat(),
        "deleted_at": account.deleted_at.isoformat() if account.deleted_at else None,
        "frequency_multiplier": account.frequency_multiplier,
    }
    result = client.table("accounts").upsert(data, on_conflict="workspace_id,slug").execute()
    return _from_row(result.data[0])


def get_accounts_for_workspace(client: Client, workspace_id: UUID) -> list[Account]:
    result = (
        client.table("accounts")
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .is_("deleted_at", "null")
        .execute()
    )
    return [_from_row(row) for row in result.data]


def update_account_last_generated(client: Client, account_id: UUID, generated_at: datetime) -> None:
    """Update accounts.last_narrative_generated_at after successful narrative generation."""
    result = (
        client.table("accounts")
        .update({"last_narrative_generated_at": generated_at.isoformat()})
        .eq("id", str(account_id))
        .execute()
    )
    if not result.data:
        logger.warning("update_account_last_generated matched no rows: account_id=%s", account_id)


def update_account_overall_health(
    client: Client,
    account_id: UUID,
    score: int | None,
    triggered_by: str = "narrative",
) -> None:
    """Update accounts.overall_health_score. Logs warning if no row updated."""
    result = (
        client.table("accounts")
        .update({"overall_health_score": score})
        .eq("id", str(account_id))
        .execute()
    )
    if not result.data:
        logger.warning("update_account_overall_health matched no rows: account_id=%s", account_id)

    # old_score omitted: fetching it requires an extra DB round-trip for a single analytics
    # property; the cost is not justified at this stage.
    workspace_id = result.data[0]["workspace_id"] if result.data else None
    if workspace_id:
        analytics.track(
            "Account Health Score Changed",
            workspace_id,
            {
                "account_id": str(account_id),
                "new_score": score,
                "triggered_by": triggered_by,
            },
        )


def get_account_by_email_domain(client: Client, workspace_id: UUID, email: str) -> UUID | None:
    """Return the account id whose primary_domain or additional_domains matches the email domain.

    Returns None if the email is malformed (no `@`), the domain is malformed (fails RFC-1035
    shape check), matches zero accounts, or matches 2+ accounts (multi-match ambiguity —
    ADR-013 Decision 1). The shape check exists because the domain is interpolated into a
    PostgREST `or_()` filter string; rejecting non-domain inputs prevents filter injection.
    """
    if "@" not in email:
        return None
    domain = email.lower().split("@")[-1]
    if not _DOMAIN_RE.match(domain):
        return None
    result = (
        client.table("accounts")
        .select("id")
        .eq("workspace_id", str(workspace_id))
        .is_("deleted_at", "null")
        .or_(f"primary_domain.eq.{domain},additional_domains.cs.{{{domain}}}")
        .execute()
    )
    if len(result.data) == 1:
        return UUID(result.data[0]["id"])
    return None


def get_account_by_slug(client: Client, workspace_id: UUID, slug: str) -> Account | None:
    result = (
        client.table("accounts")
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .eq("slug", slug)
        .is_("deleted_at", "null")
        .execute()
    )
    if not result.data:
        return None
    return _from_row(result.data[0])
