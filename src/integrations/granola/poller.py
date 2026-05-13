"""Granola workspace poller (ADR-020 Phase 3, D4 + D5).

poll_workspace_granola handles one (workspace, credential) pair per call.
The /run-polls handler drives the outer fan-out loop.

Cursor-advance discipline (ADR-020 D8):
    The cursor stored in integration_state is "granola:<note_id>" (namespaced).
    It is advanced ONLY after a batch of notes is confirmed written to signals.
    If the worker crashes between batch-write and cursor-update, the next poll
    re-fetches from the previous cursor; dedup treats re-seen notes as no-ops.

Deactivation threshold (ADR-020 D5):
    When consecutive_errors >= config.integrations.max_consecutive_errors (default 5),
    the credential is deactivated (is_active = false) and an ERROR log is emitted.
    At 15-minute cadence that is ~75 minutes of continuous failure before auto-disable.
"""

from __future__ import annotations

import logging

import httpx

from src.config.loader import load_config
from src.db.external_credentials import (
    ExternalCredential,
    clear_credential_error,
    deactivate_credential,
    mark_credential_error,
)
from src.db.integration_state import (
    IntegrationState,
    advance_cursor,
    record_poll_error,
    record_poll_success,
)
from src.domain.workspace import Workspace
from src.integrations.crypto import decrypt_secret, get_integration_encryption_key
from src.integrations.granola.adapter import parse_granola_note
from src.integrations.granola.client import (
    GranolaAuthError,
    GranolaClient,
    GranolaRateLimitError,
    GranolaServerError,
)
from src.pipeline.scheduler import schedule_regen
from src.pipeline.structured_signal import normalize_structured_signal
from supabase import Client

logger = logging.getLogger(__name__)


async def poll_workspace_granola(
    workspace: Workspace,
    credential: ExternalCredential,
    state: IntegrationState,
    client_db: Client,
) -> tuple[int, int]:
    """Poll Granola for new notes for one workspace credential.

    Returns (total_new, total_duplicate) for the run.

    On recoverable errors (429, 5xx, network timeout): logs and returns (0, 0);
    consecutive_errors is incremented; cursor is NOT advanced.

    On auth errors (401/403): marks credential error, increments consecutive_errors,
    may deactivate credential if threshold exceeded.

    All exceptions are caught and handled here — the /run-polls handler wraps calls
    in its own try/except for belt-and-suspenders, but well-behaved errors should not
    propagate out of this function.
    """
    logger.info("granola_poll_start workspace=%s cred=%s", workspace.slug, credential.id)

    api_key = decrypt_secret(credential.secret_enc, get_integration_encryption_key())
    granola = GranolaClient(api_key)

    # Strip "granola:" namespace prefix for the Granola API call; store namespaced
    raw_cursor = state.cursor
    api_cursor: str | None = None
    if raw_cursor is not None:
        # Strip our namespace prefix to get the bare Granola note_id
        api_cursor = raw_cursor.removeprefix("granola:")

    total_new = 0
    total_dup = 0

    try:
        while True:
            notes, next_cursor = await granola.list_notes(after=api_cursor, limit=50)
            if not notes:
                break

            # Process the batch
            for note in notes:
                input_ = parse_granola_note(
                    note,
                    credential.id,
                    internal_domains=workspace.internal_domains,
                )
                if input_ is None:
                    continue
                result = normalize_structured_signal(
                    input_,
                    workspace.id,
                    workspace.name,
                    credential.id,
                    credential.kind,
                    client_db,
                )
                if result.duplicate:
                    total_dup += 1
                else:
                    total_new += 1
                    if result.signal.account_id:
                        try:
                            schedule_regen(result.signal, workspace.id, client_db)
                        except Exception:
                            # fire-and-log: never fail polling because regen scheduling fails
                            logger.exception(
                                "granola_regen_schedule_error workspace=%s signal=%s",
                                workspace.slug,
                                result.signal.id,
                            )

            # Cursor advances only after the entire batch is confirmed written
            last_note_id = notes[-1]["id"]
            namespaced_cursor = f"granola:{last_note_id}"
            advance_cursor(client_db, state.id, namespaced_cursor)
            api_cursor = last_note_id  # bare ID for next Granola API call

            logger.info(
                "granola_batch_processed workspace=%s count=%d cursor=%s",
                workspace.slug,
                len(notes),
                namespaced_cursor,
            )

            if next_cursor is None:
                break

        record_poll_success(client_db, state.id)
        clear_credential_error(client_db, credential.id)
        logger.info(
            "granola_poll_complete workspace=%s total_new=%d total_duplicate=%d",
            workspace.slug,
            total_new,
            total_dup,
        )
        return total_new, total_dup

    except GranolaRateLimitError:
        logger.warning(
            "granola_rate_limited workspace=%s cred=%s — cursor unchanged",
            workspace.slug,
            credential.id,
        )
        record_poll_error(client_db, state.id)
        return 0, 0

    except GranolaAuthError:
        logger.error(
            "granola_auth_failure workspace=%s cred=%s",
            workspace.slug,
            credential.id,
        )
        mark_credential_error(client_db, credential.id, "API key rejected (401/403)")
        record_poll_error(client_db, state.id)
        _maybe_deactivate(client_db, credential, state, workspace.slug)
        return 0, 0

    except GranolaServerError:
        logger.warning(
            "granola_server_error workspace=%s cred=%s — cursor unchanged",
            workspace.slug,
            credential.id,
        )
        record_poll_error(client_db, state.id)
        _maybe_deactivate(client_db, credential, state, workspace.slug)
        return 0, 0

    except httpx.TimeoutException:
        logger.warning(
            "granola_timeout workspace=%s cred=%s — cursor unchanged",
            workspace.slug,
            credential.id,
        )
        record_poll_error(client_db, state.id)
        _maybe_deactivate(client_db, credential, state, workspace.slug)
        return 0, 0

    except Exception:
        logger.exception(
            "granola_poll_error workspace=%s cred=%s",
            workspace.slug,
            credential.id,
        )
        record_poll_error(client_db, state.id)
        _maybe_deactivate(client_db, credential, state, workspace.slug)
        return 0, 0


def _maybe_deactivate(
    client_db: Client,
    credential: ExternalCredential,
    state: IntegrationState,
    workspace_slug: str,
) -> None:
    """Deactivate the credential if consecutive_errors has hit the threshold.

    Uses the in-memory consecutive_errors value from the state object BEFORE
    the most-recent record_poll_error call, so we compare against the pre-increment
    value + 1. The record_poll_error call in the caller already ran; the state object
    is stale by one increment. We add 1 to compensate.

    Threshold is loaded from config/defaults.json integrations.max_consecutive_errors.
    """
    config = load_config()
    threshold = config.integrations.max_consecutive_errors
    # state.consecutive_errors is the value before the most-recent record_poll_error
    if (state.consecutive_errors + 1) >= threshold:
        logger.error(
            "granola_integration_broken workspace=%s cred=%s consecutive_errors=%d — deactivating",
            workspace_slug,
            credential.id,
            state.consecutive_errors + 1,
        )
        deactivate_credential(client_db, credential.id)
