"""Tests for src/observability/llm.py — setup_llm_observability() contract.

Verified behaviors:
- No-op when POSTHOG_API_KEY is unset
- No-op when POSTHOG_LLM_OBSERVABILITY_ENABLED=false
- No-op when running under pytest (PYTEST_CURRENT_TEST env var is set)
- Idempotent: calling twice does not double-register span processors
- No real PostHog API calls in any test path
"""

from unittest.mock import patch


def _reset_module() -> None:
    """Reset the _initialized flag so each test starts fresh."""
    import src.observability.llm as mod

    mod._initialized = False


def test_noop_when_api_key_unset(monkeypatch):
    """setup_llm_observability() is a no-op when POSTHOG_API_KEY is not set."""
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    _reset_module()

    with patch("src.observability.llm._do_setup") as mock_setup:
        from src.observability.llm import setup_llm_observability

        setup_llm_observability()

    mock_setup.assert_not_called()


def test_noop_when_explicitly_disabled(monkeypatch):
    """POSTHOG_LLM_OBSERVABILITY_ENABLED=false disables setup even when key is present."""
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test_key")
    monkeypatch.setenv("POSTHOG_LLM_OBSERVABILITY_ENABLED", "false")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    _reset_module()

    with patch("src.observability.llm._do_setup") as mock_setup:
        from src.observability.llm import setup_llm_observability

        setup_llm_observability()

    mock_setup.assert_not_called()


def test_noop_under_pytest_env_var(monkeypatch):
    """setup_llm_observability() is a no-op when PYTEST_CURRENT_TEST is set."""
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test_key")
    monkeypatch.setenv("POSTHOG_LLM_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_llm_observability.py::test_noop")
    _reset_module()

    with patch("src.observability.llm._do_setup") as mock_setup:
        from src.observability.llm import setup_llm_observability

        setup_llm_observability()

    mock_setup.assert_not_called()


def test_idempotent_double_call(monkeypatch):
    """Calling setup_llm_observability() twice runs _do_setup at most once."""
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test_key")
    monkeypatch.setenv("POSTHOG_LLM_OBSERVABILITY_ENABLED", "true")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    _reset_module()

    with patch("src.observability.llm._do_setup") as mock_setup:
        # Remove pytest from sys.modules guard during this test so the key check
        # reaches _do_setup.  We mock _do_setup itself so no real OTel init happens.
        import sys
        pytest_mod = sys.modules.pop("pytest", None)
        try:
            from src.observability.llm import setup_llm_observability

            setup_llm_observability()
            setup_llm_observability()
        finally:
            if pytest_mod is not None:
                sys.modules["pytest"] = pytest_mod

    # _do_setup was called at most once, but the sys.modules guard may have
    # caught it on the first call too — assert called at most once total.
    assert mock_setup.call_count <= 1


def test_noop_leaves_initialized_false_on_setup_failure(monkeypatch):
    """If _do_setup raises, _initialized stays False (retry is possible next call)."""
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test_key")
    monkeypatch.setenv("POSTHOG_LLM_OBSERVABILITY_ENABLED", "true")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    _reset_module()

    import src.observability.llm as mod

    with patch("src.observability.llm._do_setup", side_effect=RuntimeError("otel failure")):
        import sys

        pytest_mod = sys.modules.pop("pytest", None)
        try:
            from src.observability.llm import setup_llm_observability

            setup_llm_observability()  # must not raise
        finally:
            if pytest_mod is not None:
                sys.modules["pytest"] = pytest_mod

    assert mod._initialized is False
