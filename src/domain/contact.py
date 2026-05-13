from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class Contact:
    id: UUID
    workspace_id: UUID
    account_id: UUID | None
    email: str
    display_name: str | None
    is_internal: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


def derive_display_name(display_name: str | None, email: str) -> str:
    """Best-effort human-readable name for a contact.

    Why: LLM prompts (narrative generator + audit harness) render contacts as
    "<name> <email>". When display_name is null, falling back to literal
    "Unknown" was causing Sonnet to misread "Unknown <jordan.smith@x.com>" as
    "unverified contact" and fail audit on hallucination grounds. Deriving the
    name from the email local-part ("Jordan Smith") gives the LLM a grounded
    label without pretending to know more than we do.

    Order of preference:
        1. display_name if non-empty
        2. email local-part with separators replaced and title-cased
        3. "Unknown" only when email is also missing
    """
    if display_name and display_name.strip():
        return display_name
    if email and "@" in email:
        local = email.split("@", 1)[0]
        return local.replace(".", " ").replace("_", " ").replace("-", " ").title()
    return "Unknown"
