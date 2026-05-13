"""Unit tests for src.integrations.granola.poller.poll_workspace_granola.

All external I/O is mocked:
    - GranolaClient.list_notes
    - decrypt_secret / get_integration_encryption_key
    - normalize_structured_signal
    - schedule_regen
    - DB functions (advance_cursor, record_poll_success, record_poll_error,
                    clear_credential_error, mark_credential_error, deactivate_credential)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import ANY, MagicMock, patch
from uuid import uuid4

import httpx

from src.db.external_credentials import ExternalCredential
from src.db.integration_state import IntegrationState
from src.domain.signal import Channel, Direction, RoutingMethod, Signal, SourceType
from src.domain.workspace import Workspace
from src.integrations.granola.client import (
    GranolaAuthError,
    GranolaRateLimitError,
)
from src.integrations.granola.poller import poll_workspace_granola
from src.pipeline.product_event import IngestResult

# ─── Constants ────────────────────────────────────────────────────────────────

_WS_ID = uuid4()
_CRED_ID = uuid4()
_STATE_ID = uuid4()


# ─── Fixture helpers ──────────────────────────────────────────────────────────


def _workspace(slug: str = "acme") -> Workspace:
    return Workspace(
        id=_WS_ID,
        organization_id=uuid4(),
        slug=slug,
        name="Acme Corp",
        internal_domains=("acme.com",),
        crm_url_template=None,
        crm_portal_id=None,
        outbound_sender_email=None,
        outbound_sender_name=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        deleted_at=None,
    )


def _credential(consecutive_errors: int = 0) -> ExternalCredential:
    now = datetime.now(UTC)
    return ExternalCredential(
        id=_CRED_ID,
        workspace_id=_WS_ID,
        kind="granola_api_key",
        direction="outbound",
        label="Granola prod",
        secret_enc=b"\x00" * 29,  # dummy; decrypt_secret is mocked
        key_hint="test",
        metadata={},
        is_active=True,
        last_verified_at=None,
        error_at=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def _state(
    cursor: str | None = None,
    consecutive_errors: int = 0,
) -> IntegrationState:
    now = datetime.now(UTC)
    return IntegrationState(
        id=_STATE_ID,
        workspace_id=_WS_ID,
        credential_id=_CRED_ID,
        kind="granola_api_key",
        cursor=cursor,
        last_polled_at=None,
        last_success_at=None,
        consecutive_errors=consecutive_errors,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def _note(note_id: str = "not_abc") -> dict:
    return {
        "id": note_id,
        "title": "Sync",
        "createdAt": "2026-05-08T10:00:00Z",
        "owner": {"name": "Alice", "email": "alice@acme.com"},
        "summary": "Discussed Q2 plans.",
    }


def _signal() -> Signal:
    now = datetime.now(UTC)
    account_id = uuid4()
    return Signal(
        id=uuid4(),
        workspace_id=_WS_ID,
        account_id=account_id,
        source_type=SourceType.GRANOLA_NOTE,
        external_id="granola:not_abc",
        thread_id=None,
        direction=Direction.INTERNAL,
        channel=Channel.MEETING_NOTE,
        occurred_at=now,
        created_at=now,
        updated_at=now,
        subject="Sync",
        body="Discussed Q2 plans.",
        author_contact_id=uuid4(),
        recipient_contact_ids=[],
        routing_method=RoutingMethod.API_KEY_IDENTITY,
        routing_confidence=1.0,
        routing_warning=None,
        deleted_at=None,
        signal_metadata={},
    )


def _ingest_result(*, duplicate: bool = False) -> IngestResult:
    return IngestResult(signal=_signal(), duplicate=duplicate)


# ─── Base patch context manager ───────────────────────────────────────────────


def _base_patches(
    *,
    list_notes_return: list[tuple[list[dict], str | None]] | None = None,
    ingest_result: IngestResult | None = None,
):
    """Return a dict of patches that cover all external I/O in the poller.

    list_notes_return: sequence of (notes, next_cursor) pairs to return on successive calls.
    """
    if list_notes_return is None:
        list_notes_return = [([], None)]
    if ingest_result is None:
        ingest_result = _ingest_result()

    call_index = 0

    async def fake_list_notes(**kwargs):
        nonlocal call_index
        val = list_notes_return[min(call_index, len(list_notes_return) - 1)]
        call_index += 1
        return val

    return {
        "src.integrations.granola.poller.decrypt_secret": patch(
            "src.integrations.granola.poller.decrypt_secret", return_value="grn_fakekey"
        ),
        "src.integrations.granola.poller.get_integration_encryption_key": patch(
            "src.integrations.granola.poller.get_integration_encryption_key",
            return_value=b"\x00" * 32,
        ),
        "src.integrations.granola.poller.GranolaClient": patch(
            "src.integrations.granola.poller.GranolaClient",
            return_value=MagicMock(list_notes=fake_list_notes),
        ),
        "src.integrations.granola.poller.parse_granola_note": patch(
            "src.integrations.granola.poller.parse_granola_note",
            return_value=MagicMock(),  # truthy StructuredSignalInput stub
        ),
        "src.integrations.granola.poller.normalize_structured_signal": patch(
            "src.integrations.granola.poller.normalize_structured_signal",
            return_value=ingest_result,
        ),
        "src.integrations.granola.poller.schedule_regen": patch(
            "src.integrations.granola.poller.schedule_regen"
        ),
        "src.integrations.granola.poller.advance_cursor": patch(
            "src.integrations.granola.poller.advance_cursor"
        ),
        "src.integrations.granola.poller.record_poll_success": patch(
            "src.integrations.granola.poller.record_poll_success"
        ),
        "src.integrations.granola.poller.record_poll_error": patch(
            "src.integrations.granola.poller.record_poll_error"
        ),
        "src.integrations.granola.poller.clear_credential_error": patch(
            "src.integrations.granola.poller.clear_credential_error"
        ),
        "src.integrations.granola.poller.mark_credential_error": patch(
            "src.integrations.granola.poller.mark_credential_error"
        ),
        "src.integrations.granola.poller.deactivate_credential": patch(
            "src.integrations.granola.poller.deactivate_credential"
        ),
    }


# ─── Tests: cursor discipline ─────────────────────────────────────────────────


async def test_poll_advances_cursor_after_batch():
    """Cursor is updated to the last note_id after each batch is written."""
    notes = [_note("not_first"), _note("not_last")]
    patches = _base_patches(list_notes_return=[(notes, None)])

    with (
        patches["src.integrations.granola.poller.decrypt_secret"],
        patches["src.integrations.granola.poller.get_integration_encryption_key"],
        patches["src.integrations.granola.poller.GranolaClient"],
        patches["src.integrations.granola.poller.parse_granola_note"],
        patches["src.integrations.granola.poller.normalize_structured_signal"],
        patches["src.integrations.granola.poller.schedule_regen"],
        patches["src.integrations.granola.poller.advance_cursor"] as mock_advance,
        patches["src.integrations.granola.poller.record_poll_success"],
        patches["src.integrations.granola.poller.record_poll_error"],
        patches["src.integrations.granola.poller.clear_credential_error"],
        patches["src.integrations.granola.poller.mark_credential_error"],
        patches["src.integrations.granola.poller.deactivate_credential"],
    ):
        await poll_workspace_granola(
            _workspace(), _credential(), _state(), MagicMock()
        )

    mock_advance.assert_called_once_with(ANY, _STATE_ID, "granola:not_last")


async def test_poll_does_not_advance_cursor_on_rate_limit():
    """429 from Granola: cursor is NOT advanced; error counter is incremented."""
    patches = _base_patches()

    async def raise_rate_limit(**kwargs):
        raise GranolaRateLimitError("429")

    with (
        patches["src.integrations.granola.poller.decrypt_secret"],
        patches["src.integrations.granola.poller.get_integration_encryption_key"],
        patch(
            "src.integrations.granola.poller.GranolaClient",
            return_value=MagicMock(list_notes=raise_rate_limit),
        ),
        patches["src.integrations.granola.poller.parse_granola_note"],
        patches["src.integrations.granola.poller.normalize_structured_signal"],
        patches["src.integrations.granola.poller.schedule_regen"],
        patches["src.integrations.granola.poller.advance_cursor"] as mock_advance,
        patches["src.integrations.granola.poller.record_poll_success"],
        patches["src.integrations.granola.poller.record_poll_error"] as mock_err,
        patches["src.integrations.granola.poller.clear_credential_error"],
        patches["src.integrations.granola.poller.mark_credential_error"],
        patches["src.integrations.granola.poller.deactivate_credential"],
    ):
        new, dup = await poll_workspace_granola(
            _workspace(), _credential(), _state(), MagicMock()
        )

    assert new == 0
    assert dup == 0
    mock_advance.assert_not_called()
    mock_err.assert_called_once()


async def test_poll_does_not_advance_cursor_on_timeout():
    """Network timeout: cursor is NOT advanced; error counter is incremented."""
    patches = _base_patches()

    async def raise_timeout(**kwargs):
        raise httpx.TimeoutException("timeout")

    with (
        patches["src.integrations.granola.poller.decrypt_secret"],
        patches["src.integrations.granola.poller.get_integration_encryption_key"],
        patch(
            "src.integrations.granola.poller.GranolaClient",
            return_value=MagicMock(list_notes=raise_timeout),
        ),
        patches["src.integrations.granola.poller.parse_granola_note"],
        patches["src.integrations.granola.poller.normalize_structured_signal"],
        patches["src.integrations.granola.poller.schedule_regen"],
        patches["src.integrations.granola.poller.advance_cursor"] as mock_advance,
        patches["src.integrations.granola.poller.record_poll_success"],
        patches["src.integrations.granola.poller.record_poll_error"] as mock_err,
        patches["src.integrations.granola.poller.clear_credential_error"],
        patches["src.integrations.granola.poller.mark_credential_error"],
        patches["src.integrations.granola.poller.deactivate_credential"],
    ):
        new, _dup = await poll_workspace_granola(
            _workspace(), _credential(), _state(), MagicMock()
        )

    assert new == 0
    mock_advance.assert_not_called()
    mock_err.assert_called_once()


# ─── Tests: auth failure ──────────────────────────────────────────────────────


async def test_poll_marks_credential_error_on_auth_failure():
    """401/403 from Granola: credential error is marked; error counter incremented."""
    patches = _base_patches()

    async def raise_auth(**kwargs):
        raise GranolaAuthError("401")

    with (
        patches["src.integrations.granola.poller.decrypt_secret"],
        patches["src.integrations.granola.poller.get_integration_encryption_key"],
        patch(
            "src.integrations.granola.poller.GranolaClient",
            return_value=MagicMock(list_notes=raise_auth),
        ),
        patches["src.integrations.granola.poller.parse_granola_note"],
        patches["src.integrations.granola.poller.normalize_structured_signal"],
        patches["src.integrations.granola.poller.schedule_regen"],
        patches["src.integrations.granola.poller.advance_cursor"] as mock_advance,
        patches["src.integrations.granola.poller.record_poll_success"],
        patches["src.integrations.granola.poller.record_poll_error"] as mock_err,
        patches["src.integrations.granola.poller.clear_credential_error"],
        patches["src.integrations.granola.poller.mark_credential_error"] as mock_mark,
        patches["src.integrations.granola.poller.deactivate_credential"],
    ):
        await poll_workspace_granola(
            _workspace(), _credential(), _state(), MagicMock()
        )

    mock_advance.assert_not_called()
    mock_err.assert_called_once()
    mock_mark.assert_called_once_with(ANY, _CRED_ID, "API key rejected (401/403)")


# ─── Tests: deactivation threshold ───────────────────────────────────────────


async def test_poll_deactivates_credential_after_max_errors():
    """At consecutive_errors=4 (before this poll), 5th error triggers deactivation."""
    patches = _base_patches()

    async def raise_auth(**kwargs):
        raise GranolaAuthError("401")

    # consecutive_errors=4: one more error crosses the default threshold of 5
    state = _state(consecutive_errors=4)

    with (
        patches["src.integrations.granola.poller.decrypt_secret"],
        patches["src.integrations.granola.poller.get_integration_encryption_key"],
        patch(
            "src.integrations.granola.poller.GranolaClient",
            return_value=MagicMock(list_notes=raise_auth),
        ),
        patches["src.integrations.granola.poller.parse_granola_note"],
        patches["src.integrations.granola.poller.normalize_structured_signal"],
        patches["src.integrations.granola.poller.schedule_regen"],
        patches["src.integrations.granola.poller.advance_cursor"],
        patches["src.integrations.granola.poller.record_poll_success"],
        patches["src.integrations.granola.poller.record_poll_error"],
        patches["src.integrations.granola.poller.clear_credential_error"],
        patches["src.integrations.granola.poller.mark_credential_error"],
        patches["src.integrations.granola.poller.deactivate_credential"] as mock_deactivate,
    ):
        await poll_workspace_granola(
            _workspace(), _credential(), state, MagicMock()
        )

    mock_deactivate.assert_called_once_with(ANY, _CRED_ID)


async def test_poll_does_not_deactivate_below_threshold():
    """At consecutive_errors=3 (before this poll), 4th error does NOT trigger deactivation."""
    patches = _base_patches()

    async def raise_auth(**kwargs):
        raise GranolaAuthError("401")

    state = _state(consecutive_errors=3)

    with (
        patches["src.integrations.granola.poller.decrypt_secret"],
        patches["src.integrations.granola.poller.get_integration_encryption_key"],
        patch(
            "src.integrations.granola.poller.GranolaClient",
            return_value=MagicMock(list_notes=raise_auth),
        ),
        patches["src.integrations.granola.poller.parse_granola_note"],
        patches["src.integrations.granola.poller.normalize_structured_signal"],
        patches["src.integrations.granola.poller.schedule_regen"],
        patches["src.integrations.granola.poller.advance_cursor"],
        patches["src.integrations.granola.poller.record_poll_success"],
        patches["src.integrations.granola.poller.record_poll_error"],
        patches["src.integrations.granola.poller.clear_credential_error"],
        patches["src.integrations.granola.poller.mark_credential_error"],
        patches["src.integrations.granola.poller.deactivate_credential"] as mock_deactivate,
    ):
        await poll_workspace_granola(
            _workspace(), _credential(), state, MagicMock()
        )

    mock_deactivate.assert_not_called()


# ─── Tests: success path ──────────────────────────────────────────────────────


async def test_poll_success_resets_error_counter():
    """Successful poll calls record_poll_success and clears credential error."""
    notes = [_note("not_001")]
    patches = _base_patches(list_notes_return=[(notes, None)])

    with (
        patches["src.integrations.granola.poller.decrypt_secret"],
        patches["src.integrations.granola.poller.get_integration_encryption_key"],
        patches["src.integrations.granola.poller.GranolaClient"],
        patches["src.integrations.granola.poller.parse_granola_note"],
        patches["src.integrations.granola.poller.normalize_structured_signal"],
        patches["src.integrations.granola.poller.schedule_regen"],
        patches["src.integrations.granola.poller.advance_cursor"],
        patches["src.integrations.granola.poller.record_poll_success"] as mock_success,
        patches["src.integrations.granola.poller.record_poll_error"],
        patches["src.integrations.granola.poller.clear_credential_error"] as mock_clear,
        patches["src.integrations.granola.poller.mark_credential_error"],
        patches["src.integrations.granola.poller.deactivate_credential"],
    ):
        new, dup = await poll_workspace_granola(
            _workspace(), _credential(), _state(), MagicMock()
        )

    assert new == 1
    assert dup == 0
    mock_success.assert_called_once()
    mock_clear.assert_called_once()


async def test_poll_empty_page_is_success():
    """Empty first page: no cursor advance, record_poll_success still called."""
    patches = _base_patches(list_notes_return=[([], None)])

    with (
        patches["src.integrations.granola.poller.decrypt_secret"],
        patches["src.integrations.granola.poller.get_integration_encryption_key"],
        patches["src.integrations.granola.poller.GranolaClient"],
        patches["src.integrations.granola.poller.parse_granola_note"],
        patches["src.integrations.granola.poller.normalize_structured_signal"],
        patches["src.integrations.granola.poller.schedule_regen"],
        patches["src.integrations.granola.poller.advance_cursor"] as mock_advance,
        patches["src.integrations.granola.poller.record_poll_success"] as mock_success,
        patches["src.integrations.granola.poller.record_poll_error"],
        patches["src.integrations.granola.poller.clear_credential_error"],
        patches["src.integrations.granola.poller.mark_credential_error"],
        patches["src.integrations.granola.poller.deactivate_credential"],
    ):
        new, dup = await poll_workspace_granola(
            _workspace(), _credential(), _state(), MagicMock()
        )

    assert new == 0
    assert dup == 0
    mock_advance.assert_not_called()
    mock_success.assert_called_once()


async def test_poll_dedup_counted_separately():
    """Duplicate notes increment dup count, not new count."""
    notes = [_note("not_dup")]
    patches = _base_patches(
        list_notes_return=[(notes, None)],
        ingest_result=_ingest_result(duplicate=True),
    )

    with (
        patches["src.integrations.granola.poller.decrypt_secret"],
        patches["src.integrations.granola.poller.get_integration_encryption_key"],
        patches["src.integrations.granola.poller.GranolaClient"],
        patches["src.integrations.granola.poller.parse_granola_note"],
        patches["src.integrations.granola.poller.normalize_structured_signal"],
        patches["src.integrations.granola.poller.schedule_regen"] as mock_regen,
        patches["src.integrations.granola.poller.advance_cursor"],
        patches["src.integrations.granola.poller.record_poll_success"],
        patches["src.integrations.granola.poller.record_poll_error"],
        patches["src.integrations.granola.poller.clear_credential_error"],
        patches["src.integrations.granola.poller.mark_credential_error"],
        patches["src.integrations.granola.poller.deactivate_credential"],
    ):
        new, dup = await poll_workspace_granola(
            _workspace(), _credential(), _state(), MagicMock()
        )

    assert new == 0
    assert dup == 1
    # schedule_regen should NOT be called for duplicates
    mock_regen.assert_not_called()


async def test_poll_cursor_namespace_stripped_for_api():
    """Stored cursor 'granola:not_abc' passes bare 'not_abc' to GranolaClient."""
    patches = _base_patches(list_notes_return=[([], None)])

    captured_after: list = []

    async def capturing_list_notes(after=None, limit=50):
        captured_after.append(after)
        return [], None

    with (
        patches["src.integrations.granola.poller.decrypt_secret"],
        patches["src.integrations.granola.poller.get_integration_encryption_key"],
        patch(
            "src.integrations.granola.poller.GranolaClient",
            return_value=MagicMock(list_notes=capturing_list_notes),
        ),
        patches["src.integrations.granola.poller.parse_granola_note"],
        patches["src.integrations.granola.poller.normalize_structured_signal"],
        patches["src.integrations.granola.poller.schedule_regen"],
        patches["src.integrations.granola.poller.advance_cursor"],
        patches["src.integrations.granola.poller.record_poll_success"],
        patches["src.integrations.granola.poller.record_poll_error"],
        patches["src.integrations.granola.poller.clear_credential_error"],
        patches["src.integrations.granola.poller.mark_credential_error"],
        patches["src.integrations.granola.poller.deactivate_credential"],
    ):
        # Stored cursor is namespaced
        state = _state(cursor="granola:not_abc_stored")
        await poll_workspace_granola(_workspace(), _credential(), state, MagicMock())

    # list_notes was called with the bare note_id, not the "granola:" prefix
    assert captured_after == ["not_abc_stored"]


# ─── Tests: workspace isolation ──────────────────────────────────────────────


async def test_poll_workspace_isolation_errors_dont_propagate():
    """poll_workspace_granola for workspace A does not raise for workspace B's poll."""
    patches = _base_patches()

    async def raise_auth(**kwargs):
        raise GranolaAuthError("401")

    # First call: workspace A fails. This should NOT raise out of the function.
    with (
        patches["src.integrations.granola.poller.decrypt_secret"],
        patches["src.integrations.granola.poller.get_integration_encryption_key"],
        patch(
            "src.integrations.granola.poller.GranolaClient",
            return_value=MagicMock(list_notes=raise_auth),
        ),
        patches["src.integrations.granola.poller.parse_granola_note"],
        patches["src.integrations.granola.poller.normalize_structured_signal"],
        patches["src.integrations.granola.poller.schedule_regen"],
        patches["src.integrations.granola.poller.advance_cursor"],
        patches["src.integrations.granola.poller.record_poll_success"],
        patches["src.integrations.granola.poller.record_poll_error"],
        patches["src.integrations.granola.poller.clear_credential_error"],
        patches["src.integrations.granola.poller.mark_credential_error"],
        patches["src.integrations.granola.poller.deactivate_credential"],
    ):
        # Should not raise — returns (0, 0) gracefully
        result = await poll_workspace_granola(
            _workspace("workspace-a"), _credential(), _state(), MagicMock()
        )
        assert result == (0, 0)
