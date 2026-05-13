"""SOC-2 / ISO 27001 regression gate for LLM content-capture suppression.

This module exists solely to prevent accidental removal of the forced
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false override in
src/observability/llm.py.  If that override is removed or weakened, this
test fails CI before the branch can merge.
"""

import os
from unittest.mock import MagicMock, patch


def test_setup_llm_observability_forces_otel_content_capture_off():
    """SOC-2 Type II / ISO 27001 control gate.

    src/observability/llm.py must force OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT
    to "false" at startup, regardless of any environment-set value. This test fails
    any branch that removes or weakens the override.

    To temporarily enable content capture for a synthetic-data debug session,
    see docs/debugging-llm-output.md ("Re-enable PostHog content capture for
    a session") — but the change must NOT land in main. This test is the CI
    backstop.
    """
    from src.observability.llm import _do_setup

    # Start with the env var explicitly set to "true" so we'd detect if the
    # override is absent (a no-op _do_setup would leave it as "true").
    env_before = os.environ.pop("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", None)
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "true"

    try:
        # Patch all heavy OTel/PostHog imports inside _do_setup so the test
        # runs without real instrumentation packages being fully initialized.
        fake_instrumentor = MagicMock()
        fake_instrumentor.instrument = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.trace": MagicMock(),
                "opentelemetry.instrumentation.anthropic": MagicMock(
                    AnthropicInstrumentor=lambda: fake_instrumentor
                ),
                "opentelemetry.instrumentation.openai_v2": MagicMock(
                    OpenAIInstrumentor=lambda: fake_instrumentor
                ),
                "opentelemetry.sdk.resources": MagicMock(
                    SERVICE_NAME="service.name",
                    Resource=MagicMock(return_value=MagicMock()),
                ),
                "opentelemetry.sdk.trace": MagicMock(
                    TracerProvider=MagicMock(return_value=MagicMock())
                ),
                "posthog.ai.otel": MagicMock(
                    PostHogSpanProcessor=MagicMock(return_value=MagicMock())
                ),
            },
        ):
            _do_setup("phc_test_key_for_ci")

        # THE ASSERTION: the override must have forced the env var to "false".
        assert os.environ.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT") == "false", (
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT must be forced to 'false' by "
            "src/observability/llm.py._do_setup — the SOC-2 content-capture override is missing. "
            "See docs/debugging-llm-output.md for the approved opt-in workflow."
        )
    finally:
        # Restore env state.
        if env_before is not None:
            os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = env_before
        else:
            os.environ.pop("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", None)
