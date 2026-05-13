"""Unit tests for src.integrations.granola.adapter.parse_granola_note."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.domain.signal import Direction
from src.integrations.granola.adapter import parse_granola_note

_CRED_ID = uuid4()


# ─── Fixture helpers ──────────────────────────────────────────────────────────


def _note(
    *,
    note_id: str = "not_abc123",
    title: str = "Q2 Sync with Acme",
    created_at: str = "2026-05-08T10:00:00Z",
    owner_email: str = "alice@acme.com",
    owner_name: str = "Alice CSM",
    summary: str = "Discussed expansion and Q2 roadmap.",
    transcript: list[dict] | None = None,
) -> dict:
    base: dict = {
        "id": note_id,
        "title": title,
        "createdAt": created_at,
        "owner": {"name": owner_name, "email": owner_email},
        "summary": summary,
    }
    if transcript is not None:
        base["transcript"] = transcript
    return base


def _transcript_items() -> list[dict]:
    return [
        {"speaker": {"name": "Alice CSM", "source": "microphone"}, "text": "Hello everyone."},
        {"speaker": {"name": "Bob Customer", "source": "speaker"}, "text": "Great to be here."},
    ]


# ─── Tests ────────────────────────────────────────────────────────────────────


def test_parse_note_standard():
    """Standard note with all fields maps to StructuredSignalInput correctly."""
    note = _note(transcript=_transcript_items())
    result = parse_granola_note(note, _CRED_ID)

    assert result is not None
    assert result.external_id == "granola:not_abc123"
    assert result.kind == "meeting_note"
    assert result.subject == "Q2 Sync with Acme"
    assert result.direction == Direction.INTERNAL
    assert len(result.participants) == 1
    assert result.participants[0].email == "alice@acme.com"
    assert result.participants[0].name == "Alice CSM"
    # occurred_at is tz-aware
    assert result.occurred_at.tzinfo is not None


def test_parse_note_no_summary_returns_none():
    """Notes with empty or missing summary are skipped (incomplete AI processing)."""
    assert parse_granola_note(_note(summary=""), _CRED_ID) is None
    assert parse_granola_note(_note(summary="   "), _CRED_ID) is None


def test_external_id_namespaced():
    """external_id must be prefixed with 'granola:' per ADR-020 D6."""
    result = parse_granola_note(_note(note_id="not_xyz"), _CRED_ID)
    assert result is not None
    assert result.external_id == "granola:not_xyz"


def test_direction_is_internal():
    """Meeting notes are always Direction.INTERNAL."""
    result = parse_granola_note(_note(), _CRED_ID)
    assert result is not None
    assert result.direction == Direction.INTERNAL


def test_participants_owner_default_role_customer():
    """Without internal_domains hint, owner defaults to role='customer'."""
    result = parse_granola_note(_note(owner_email="alice@vendor.com"), _CRED_ID)
    assert result is not None
    assert result.participants[0].role == "customer"


def test_participants_owner_role_internal_when_domain_matches():
    """Owner email domain matching internal_domains → role='internal'."""
    result = parse_granola_note(
        _note(owner_email="alice@acme.com"),
        _CRED_ID,
        internal_domains=("acme.com",),
    )
    assert result is not None
    assert result.participants[0].role == "internal"


def test_participants_owner_role_customer_when_domain_no_match():
    """Owner email domain NOT in internal_domains → role='customer'."""
    result = parse_granola_note(
        _note(owner_email="alice@customer.io"),
        _CRED_ID,
        internal_domains=("acme.com",),
    )
    assert result is not None
    assert result.participants[0].role == "customer"


def test_body_includes_summary_and_transcript():
    """Body combines AI summary and transcript text when transcript is present."""
    result = parse_granola_note(_note(transcript=_transcript_items()), _CRED_ID)
    assert result is not None
    assert "Discussed expansion and Q2 roadmap." in result.body
    assert "Alice CSM" in result.body
    assert "Hello everyone." in result.body
    assert "Great to be here." in result.body


def test_body_summary_only_when_no_transcript():
    """Body is just the summary when transcript is absent."""
    result = parse_granola_note(_note(transcript=None), _CRED_ID)
    assert result is not None
    assert result.body == "Discussed expansion and Q2 roadmap."


def test_occurred_at_is_tz_aware():
    """occurred_at must be a tz-aware datetime (from createdAt)."""
    result = parse_granola_note(_note(created_at="2026-05-08T10:00:00Z"), _CRED_ID)
    assert result is not None
    assert result.occurred_at.tzinfo is not None
    assert result.occurred_at == datetime(2026, 5, 8, 10, 0, 0, tzinfo=UTC)


def test_occurred_at_naive_gets_utc():
    """Naive timestamp is coerced to UTC."""
    result = parse_granola_note(_note(created_at="2026-05-08T10:00:00"), _CRED_ID)
    assert result is not None
    assert result.occurred_at.tzinfo is not None


def test_missing_owner_email_raises():
    """Note missing owner email raises ValueError."""
    note = _note()
    del note["owner"]["email"]
    with pytest.raises(ValueError):
        parse_granola_note(note, _CRED_ID)


def test_missing_created_at_raises():
    """Note missing createdAt raises ValueError."""
    note = _note()
    del note["createdAt"]
    with pytest.raises(ValueError):
        parse_granola_note(note, _CRED_ID)


def test_missing_note_id_raises():
    """Note missing id raises ValueError."""
    note = _note()
    del note["id"]
    with pytest.raises(ValueError):
        parse_granola_note(note, _CRED_ID)


def test_metadata_includes_note_id_and_owner():
    """metadata JSONB contains granola_note_id and owner_email."""
    result = parse_granola_note(
        _note(note_id="not_meta_test", owner_email="CSM@ACME.COM"), _CRED_ID
    )
    assert result is not None
    assert result.metadata["granola_note_id"] == "not_meta_test"
    assert result.metadata["owner_email"] == "csm@acme.com"  # lowercased


def test_owner_email_is_lowercased_in_participant():
    """Participant email is lowercased regardless of input casing."""
    result = parse_granola_note(_note(owner_email="Alice@ACME.COM"), _CRED_ID)
    assert result is not None
    assert result.participants[0].email == "alice@acme.com"


def test_thread_id_is_none():
    """Granola notes have no thread concept; thread_id must be None."""
    result = parse_granola_note(_note(), _CRED_ID)
    assert result is not None
    assert result.thread_id is None
