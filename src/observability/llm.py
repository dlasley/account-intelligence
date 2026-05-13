"""LLM observability setup via OpenTelemetry + PostHog.

Architectural boundary
----------------------
This module is parallel to, not part of, the product analytics layer in
``src/analytics.py``.  ``src/analytics.py`` emits *product* events (Narrative
Generated, Signal Ingested, etc.) via the PostHog capture API — these are
business-level events that feed dashboards and funnels.

This module instruments the *LLM call* layer via OpenTelemetry auto-instrumentation:
the Anthropic and OpenAI client libraries are monkey-patched by their respective
OTel instrumentors, and every API call emits a ``$ai_generation`` span that PostHog
captures via the ``PostHogSpanProcessor``.  The span carries
``$ai_model``, ``$ai_latency``, ``$ai_input_tokens``, ``$ai_output_tokens``,
and ``$ai_total_cost_usd``.  Prompt/response content is NEVER captured — the
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT env var is forced to "false"
at startup regardless of any environment-set value (SOC-2 Type II / ISO 27001
control — see _do_setup).

These two concerns stay separated:
- Product events (``src/analytics.py``): who did what, business-level dimensions
- LLM spans (this module): cost, latency, token counts per model call

Both flow to the same PostHog project; they are distinguished by event type
(``$ai_generation`` vs named product events) in the PostHog UI.

Content capture (developer opt-in only)
----------------------------------------
Content capture (prompt + response text in spans) is OFF by default and requires
a deliberate code change to enable temporarily.  Follow the recommended workflow
in ``docs/debugging-llm-output.md`` ("Re-enable PostHog content capture for a
session") — comment out the override line in ``_do_setup``, set
``POSTHOG_LLM_CAPTURE_CONTENT=true`` in your shell, run against a synthetic
workspace only, then revert before pushing.  The CI test
``tests/test_observability_content_capture.py`` will fail any branch that ships
with the override removed.

Usage
-----
Call ``setup_llm_observability()`` once at process startup, before any LLM client
is constructed.  The function is idempotent — subsequent calls after the first
successful initialization are no-ops.  It self-suppresses in test and disabled
environments so call sites do not need to guard it.

Environment variables
---------------------
POSTHOG_API_KEY               Required to enable.  If unset, function is a no-op.
POSTHOG_HOST                  PostHog ingest host (default: https://us.i.posthog.com).
POSTHOG_LLM_OBSERVABILITY_ENABLED
                              Explicit on/off switch (default: true when API key is set).
                              Set to "false" to disable even when the key is present.
DEPLOY_ENV                    Injected into resource attributes as deployment.environment
                              (default: "development").
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Tracks whether initialization has already run.  Guards against double-registering
# span processors if setup_llm_observability() is called from multiple entry points.
_initialized: bool = False


def setup_llm_observability() -> None:
    """Configure OTel + PostHog LLM observability instrumentation.

    No-ops in the following cases (safe to call unconditionally):
    - ``POSTHOG_API_KEY`` is unset
    - ``POSTHOG_LLM_OBSERVABILITY_ENABLED=false``
    - Running under pytest (``PYTEST_CURRENT_TEST`` env var is set, or ``pytest``
      is in ``sys.modules``)
    - Already initialized (idempotent)

    On success: the global OTel ``TracerProvider`` is configured with a
    ``PostHogSpanProcessor``, and both ``AnthropicInstrumentor`` and
    ``OpenAIInstrumentor`` are registered.  All subsequent ``Anthropic()`` and
    ``OpenAI()`` client calls auto-emit ``$ai_generation`` events to PostHog.
    """
    global _initialized

    if _initialized:
        return

    # Guard: pytest environment — never emit real PostHog events during tests.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    import sys
    if "pytest" in sys.modules:
        return

    api_key = os.environ.get("POSTHOG_API_KEY", "")
    if not api_key:
        return

    enabled = os.environ.get("POSTHOG_LLM_OBSERVABILITY_ENABLED", "true").lower()
    if enabled == "false":
        return

    try:
        _do_setup(api_key)
        _initialized = True
        logger.info("LLM observability initialized (PostHog OTel)")
    except Exception:
        logger.warning("LLM observability setup failed — continuing without it", exc_info=True)


def _do_setup(api_key: str) -> None:
    """Internal: perform the actual OTel + instrumentor registration.

    Separated so the idempotency guard and error handler in setup_llm_observability()
    remain readable.
    """
    from opentelemetry import trace
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
    from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from posthog.ai.otel import PostHogSpanProcessor

    host = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")
    deploy_env = os.environ.get("DEPLOY_ENV", "development")

    # SOC-2 Type II / ISO 27001 control: never capture LLM prompt/response content
    # in production observability. The OTel GenAI env var is FORCED to "false" here
    # regardless of any environment-set value, including a developer setting
    # POSTHOG_LLM_CAPTURE_CONTENT=true locally.
    #
    # To temporarily enable content capture for a synthetic-data debug session,
    # follow the recommended workflow in docs/debugging-llm-output.md
    # ("Re-enable PostHog content capture for a session"). Briefly:
    # (1) comment out the line below, (2) set the env var to "true" in your shell,
    # (3) run against a synthetic workspace only, (4) revert before pushing.
    # The CI regression test in tests/test_observability_content_capture.py will
    # fail any branch that lands with the override removed — that's the technical
    # backstop preventing accidental production deploys.
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"

    # posthog.distinct_id on the resource is the global identity for all LLM spans
    # from this process.  Per-call context (workspace_slug, account_slug) is a
    # deferred enhancement — see module docstring.
    distinct_id = f"account-intelligence-{deploy_env}"

    resource = Resource(
        attributes={
            SERVICE_NAME: "account-intelligence",
            "service.version": "0.1.0",
            "deployment.environment": deploy_env,
            "posthog.distinct_id": distinct_id,
        }
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(PostHogSpanProcessor(api_key=api_key, host=host))
    trace.set_tracer_provider(provider)

    # Instrument both LLM client libraries.  The OTel instrumentors wrap the client
    # constructors, so they must be registered before any Anthropic() / OpenAI() call.
    # content capture is always off (forced via the env var above); pass the kwarg
    # as belt-and-suspenders in case the instrumentor version reads it before the env var.
    AnthropicInstrumentor().instrument(capture_content=False)
    OpenAIInstrumentor().instrument(capture_content=False)
