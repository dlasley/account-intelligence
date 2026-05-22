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


def get_feature_flag(
    flag_key: str,
    distinct_id: str,
    *,
    default: str | bool | None = None,
) -> str | bool | None:
    """
    Evaluate a PostHog feature flag for a given distinct_id.

    Returns the variant string for multivariate flags, a bool for boolean flags,
    or ``default`` if the flag is undefined, PostHog is disabled, or the call
    fails. Fire-and-log: exceptions are caught and logged at WARNING.

    Use to gate product behavior (e.g., prompt variant selection) on a PostHog
    flag so the variant assignment can drive an Experiment that compares
    metrics across variants.
    """
    if not _is_enabled():
        return default
    try:
        client = _get_client()
        variant = client.get_feature_flag(flag_key, distinct_id)
        if variant is None:
            return default
        return variant
    except Exception:
        logger.warning(
            "analytics.get_feature_flag failed for flag %r distinct_id %r",
            flag_key,
            distinct_id,
            exc_info=True,
        )
        return default


def track_ai_evaluation(
    *,
    workspace_id: str | UUID,
    audit_run_id: str,
    narrative_id: str,
    criterion: str,
    passed: bool,
    score: int | None,
    audit_source: str,
    auditor_model: str,
    trace_id: str | None = None,
) -> None:
    """
    Capture a `$ai_evaluation` event for a single audit-criterion verdict.

    PostHog LLM Analytics treats `$ai_evaluation` as a distinct event type that
    surfaces on the trace view alongside the `$ai_generation` it evaluates.
    Distinct_id matches the OTel-emitted generation (`account-intelligence-<env>`)
    so the events correlate cleanly in the LLM Analytics dashboard.

    Properties follow PostHog's conventional `$ai_metric_*` shape so the events
    light up the LLM Analytics evaluations surface without custom config.
    Fire-and-log: exceptions are caught and logged at WARNING.
    """
    if not _is_enabled():
        return
    try:
        deploy_env = os.environ.get("DEPLOY_ENV", "development")
        distinct_id = f"account-intelligence-{deploy_env}"
        properties: dict = {
            "$ai_metric_name": criterion,
            "$ai_metric_value": "pass" if passed else "fail",
            "$ai_provider": "openai",
            "$ai_model": auditor_model,
            "audit_run_id": audit_run_id,
            "narrative_id": narrative_id,
            "audit_source": audit_source,
            "workspace_id": str(workspace_id),
            "passed": passed,
            "score": score,
        }
        if trace_id:
            properties["$ai_trace_id"] = trace_id
        client = _get_client()
        client.capture(
            distinct_id=distinct_id,
            event="$ai_evaluation",
            properties=properties,
        )
    except Exception:
        logger.warning(
            "analytics.track_ai_evaluation failed for criterion %r", criterion, exc_info=True
        )
