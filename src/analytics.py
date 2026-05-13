"""
Analytics wrapper module (PostHog).

All call sites import only from here — never from posthog-python directly.
See ADR-014 for the wrapper-module and workspace-property decisions.
"""

import logging
import os
from uuid import UUID

logger = logging.getLogger(__name__)

_client = None


def _is_enabled() -> bool:
    return os.environ.get("POSTHOG_ENABLED", "false").lower() == "true"


def _get_client():
    """Return the module-level singleton PostHog client, initializing on first call."""
    global _client
    if _client is not None:
        return _client

    import posthog

    api_key = os.environ.get("POSTHOG_API_KEY", "")
    host = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")
    _client = posthog.Posthog(api_key, host=host)
    return _client


def _distinct_id(workspace_id: str | UUID) -> str:
    """
    Return the distinct_id for a backend event.

    Prefixes with '[dev]' in non-production environments so dev events can be
    filtered in the PostHog UI without needing a separate project (ADR-014 §6).
    """
    base = f"workspace:{workspace_id}"
    if os.environ.get("APP_ENV") != "production":
        return f"[dev]{base}"
    return base


def track(event_name: str, workspace_id: str | UUID, properties: dict) -> None:
    """
    Capture a backend analytics event.

    Fire-and-log: exceptions are caught and logged at WARNING; never re-raised.
    workspace_id is always merged into properties.
    """
    if not _is_enabled():
        return
    try:
        client = _get_client()
        props = dict(properties)
        props["workspace_id"] = str(workspace_id)
        client.capture(
            distinct_id=_distinct_id(workspace_id),
            event=event_name,
            properties=props,
        )
    except Exception:
        logger.warning("analytics.track failed for event %r", event_name, exc_info=True)


def flush() -> None:
    """
    Flush the PostHog event queue.

    Called on worker shutdown so buffered events are not dropped on Cloud Run
    instance termination (ADR-014 §Consequences).
    """
    if not _is_enabled():
        return
    try:
        _get_client().flush()
    except Exception:
        logger.warning("analytics.flush failed", exc_info=True)
