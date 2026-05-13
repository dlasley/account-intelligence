"""Granola HTTP client (ADR-020 Phase 3).

Thin async client for the Granola notes API. One AsyncClient per poll cycle
(instantiated by the poller, not shared across workspaces or requests).

API surface (verified):
    GET https://api.granola.ai/v1/notes
        ?after=<cursor>   cursor-paginated; omit for first page
        ?limit=<int>      page size; max 50
        Returns: { data: [...], next_cursor: str | null }

    GET https://api.granola.ai/v1/notes/{id}
        ?include=transcript
        Returns: note object with transcript field

Auth: Authorization: Bearer <grn_*>
Rate limit: 25 burst / 5 per second sustained (~300 rpm)
Granola only returns notes with a completed AI summary + transcript.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.granola.ai"
_TIMEOUT = httpx.Timeout(30.0)


class GranolaRateLimitError(Exception):
    """Raised when Granola returns 429 Too Many Requests."""


class GranolaAuthError(Exception):
    """Raised when Granola returns 401 or 403 (key revoked or invalid)."""


class GranolaServerError(Exception):
    """Raised on 5xx responses from Granola."""


class GranolaClient:
    """Async HTTP client for the Granola API.

    Instantiate once per poll cycle. Pass the decrypted grn_* API key.
    """

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    async def list_notes(
        self,
        after: str | None = None,
        limit: int = 50,
    ) -> tuple[list[dict], str | None]:
        """Fetch one page of notes.

        Args:
            after: Cursor from the previous page (bare note ID, not namespaced).
            limit: Page size, max 50.

        Returns:
            (notes, next_cursor). next_cursor is None when no more pages.

        Raises:
            GranolaRateLimitError: on 429.
            GranolaAuthError: on 401 or 403.
            GranolaServerError: on 5xx.
            httpx.TimeoutException: propagates to caller (poller handles it).
        """
        params: dict[str, str | int] = {"limit": limit}
        if after is not None:
            params["after"] = after

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                f"{BASE_URL}/v1/notes",
                params=params,
                headers={"Authorization": f"Bearer {self._key}"},
            )

        _raise_for_status(response)
        body = response.json()
        notes = body.get("data") or []
        next_cursor = body.get("next_cursor")
        return notes, next_cursor

    async def get_note(self, note_id: str, *, include_transcript: bool = True) -> dict:
        """Fetch a single note by ID, optionally including transcript.

        Args:
            note_id: Granola note ID (not_ prefix).
            include_transcript: If True, appends ?include=transcript.

        Returns:
            Note dict with all fields, including transcript if requested.

        Raises:
            GranolaRateLimitError, GranolaAuthError, GranolaServerError,
            httpx.TimeoutException.
        """
        params: dict[str, str] = {}
        if include_transcript:
            params["include"] = "transcript"

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                f"{BASE_URL}/v1/notes/{note_id}",
                params=params,
                headers={"Authorization": f"Bearer {self._key}"},
            )

        _raise_for_status(response)
        return response.json()


def _raise_for_status(response: httpx.Response) -> None:
    """Map HTTP error status codes to typed exceptions."""
    status = response.status_code
    if status == 429:
        raise GranolaRateLimitError(
            f"Granola rate limited (429). Retry-After: {response.headers.get('Retry-After')}"
        )
    if status in (401, 403):
        raise GranolaAuthError(f"Granola auth failure ({status})")
    if status >= 500:
        raise GranolaServerError(f"Granola server error ({status})")
    # Let 4xx other than 401/403/429 propagate as httpx.HTTPStatusError
    response.raise_for_status()
