from datetime import UTC, datetime
from uuid import NAMESPACE_DNS, UUID, uuid5

from src.domain.contact import Contact


def make_contact(
    workspace_id: UUID,
    email: str,
    *,
    display_name: str | None = None,
    is_internal: bool = False,
    account_id: UUID | None = None,
) -> Contact:
    """Build a Contact with deterministic ID via uuid5(NAMESPACE_DNS, "<ws>:<email>")."""
    now = datetime.now(UTC)
    return Contact(
        id=uuid5(NAMESPACE_DNS, f"{workspace_id}:{email.lower()}"),
        workspace_id=workspace_id,
        account_id=account_id,
        email=email.lower(),
        display_name=display_name,
        is_internal=is_internal,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
