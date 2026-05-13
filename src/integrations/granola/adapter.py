"""Granola note adapter (ADR-020 Phase 3).

Maps Granola's note shape onto StructuredSignalInput.

Granola note shape (completed notes only — in-progress excluded by API):
    {
        "id": "not_<alphanum>",
        "title": "<string>",
        "createdAt": "<ISO 8601>",
        "owner": { "name": "<string>", "email": "<string>" },
        "summary": "<string>",                   # AI-generated; non-empty for completed notes
        "transcript": [                          # present when ?include=transcript
            {
                "speaker": { "name": "<string>", "source": "microphone" | "speaker" },
                "text": "<string>"
            },
            ...
        ]
    }

Mapping decisions (per ADR-020 D1, D7):
    - external_id:   "granola:<note_id>"                  (D6 namespace convention)
    - kind:          "meeting_note"                        (Channel.MEETING_NOTE)
    - direction:     Direction.INTERNAL                    (bidirectional meeting; D1)
    - subject:       note["title"]
    - occurred_at:   note["createdAt"] (when the note/meeting was created)
    - body:          summary + transcript text concatenated (summary alone if no transcript)
    - participants:
        owner.email → role="internal" if email domain matches workspace internal_domains,
                      role="customer" otherwise (heuristic; ADR-020 open question 3)
        transcript speakers are NOT included as participants — Granola's transcript speaker
        metadata does not reliably carry email addresses (only name + microphone/speaker tag).
        Owner-only participant list is the safe default; per ADR-020 open question 3 this
        heuristic is accepted for v1.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from src.domain.signal import Direction
from src.pipeline.structured_signal import SignalParticipant, StructuredSignalInput

logger = logging.getLogger(__name__)


def parse_granola_note(
    note: dict,
    credential_id: UUID,  # reserved for future per-event tracing; unused by pure parser
    *,
    internal_domains: tuple[str, ...] = (),
) -> StructuredSignalInput | None:
    """Map a Granola note dict to StructuredSignalInput.

    Returns None if summary is empty (incomplete note — should not happen since the
    Granola API only returns completed notes, but defensive check per ADR-020 D7).

    Args:
        note: Raw note object from GET /v1/notes/{id}.
        credential_id: ID of the ExternalCredential row (unused in parsing; passed for
            future tracing or audit tagging at the adapter layer).
        internal_domains: Workspace internal domains for owner role assignment.
            Owner email matching any of these → role="internal"; otherwise "customer".

    Raises:
        ValueError: if the note lacks required fields (id, owner.email, createdAt).
    """
    summary = (note.get("summary") or "").strip()
    if not summary:
        logger.warning("granola_note_no_summary note_id=%s — skipping", note.get("id"))
        return None

    try:
        note_id = note["id"]
        created_at_raw = note["createdAt"]
        owner = note["owner"]
        owner_email = owner["email"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"granola note missing required field: {exc}") from exc

    title = (note.get("title") or "").strip() or None
    owner_name: str | None = owner.get("name") or None
    occurred_at = _parse_timestamp(created_at_raw)
    external_id = f"granola:{note_id}"

    # Owner role: internal if email domain matches workspace internal_domains
    owner_role = _infer_owner_role(owner_email, internal_domains)
    participants = [SignalParticipant(email=owner_email.lower(), name=owner_name, role=owner_role)]

    body = _build_body(summary, note.get("transcript"))

    metadata: dict = {
        "granola_note_id": note_id,
        "owner_email": owner_email.lower(),
    }
    if title:
        metadata["title"] = title

    return StructuredSignalInput(
        external_id=external_id,
        kind="meeting_note",
        occurred_at=occurred_at,
        body=body,
        participants=participants,
        subject=title,
        direction=Direction.INTERNAL,
        thread_id=None,  # Granola notes are standalone; no thread grouping
        metadata=metadata,
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _infer_owner_role(email: str, internal_domains: tuple[str, ...]) -> str:
    """Return "internal" if the email domain matches any internal_domain, else "customer".

    Heuristic: the meeting organizer (owner) is internal if they belong to the
    workspace's own domain(s). This works for the common CSM-customer call pattern.
    Edge case (customer-hosted meeting) is accepted for v1 per ADR-020 open question 3.
    """
    if not internal_domains:
        return "customer"
    domain = email.lower().split("@")[-1] if "@" in email else ""
    return "internal" if domain in internal_domains else "customer"


def _build_body(summary: str, transcript: list[dict] | None) -> str:
    """Combine AI summary with transcript text.

    Summary is the primary content; transcript is appended if present.
    """
    if not transcript:
        return summary
    transcript_lines = [
        f"{item.get('speaker', {}).get('name', 'Unknown')}: {item.get('text', '')}"
        for item in transcript
        if item.get("text")
    ]
    if not transcript_lines:
        return summary
    return summary + "\n\n---\n\n" + "\n".join(transcript_lines)


def _parse_timestamp(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
