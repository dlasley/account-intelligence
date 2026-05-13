import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from supabase import Client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApiKeyInfo:
    id: UUID
    workspace_id: UUID
    key_prefix: str
    scopes: list[str]
    owner_user_id: UUID | None
    owner_service_account_id: UUID | None


def verify_api_key(client: Client, raw_key: str, required_scope: str) -> ApiKeyInfo:
    """Look up an API key by SHA-256 hash. Verify scope. Update last_used_at fire-and-log.

    Raises:
      ValueError on missing/invalid/revoked/expired key
      PermissionError when key lacks required_scope
    """
    if not (raw_key.startswith("pk_live_") or raw_key.startswith("sk_live_")):
        raise ValueError("malformed key")

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    row = (
        client.table("api_keys")
        .select("*")
        .eq("key_hash", key_hash)
        .is_("revoked_at", "null")
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    if not row.data:
        raise ValueError("invalid key")

    data = row.data[0]
    if data.get("expires_at"):
        expires = datetime.fromisoformat(data["expires_at"])
        if expires < datetime.now(UTC):
            raise ValueError("key expired")

    scopes = data.get("scopes") or []
    if required_scope not in scopes:
        raise PermissionError(f"key lacks scope: {required_scope}")

    try:
        client.table("api_keys").update({"last_used_at": datetime.now(UTC).isoformat()}).eq(
            "id", data["id"]
        ).execute()
    except Exception:
        logger.warning("failed to update last_used_at for key %s", data.get("key_prefix"))

    return ApiKeyInfo(
        id=UUID(data["id"]),
        workspace_id=UUID(data["workspace_id"]),
        key_prefix=data["key_prefix"],
        scopes=scopes,
        owner_user_id=UUID(data["owner_user_id"]) if data.get("owner_user_id") else None,
        owner_service_account_id=(
            UUID(data["owner_service_account_id"]) if data.get("owner_service_account_id") else None
        ),
    )


def generate_key(scope: str) -> tuple[str, str, str]:
    """Generate a new API key. Returns (full_key, key_prefix, key_hash).

    Prefix is pk_live_ for ingest scope, sk_live_ otherwise.
    The key_prefix returned is the first 24 characters of the full key.
    """
    prefix = "pk_live_" if scope == "ingest" else "sk_live_"
    body = secrets.token_hex(32)
    full = f"{prefix}{body}"
    key_hash = hashlib.sha256(full.encode()).hexdigest()
    return full, full[:24], key_hash
