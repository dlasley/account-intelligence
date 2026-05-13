"""Unit tests for src.server.rate_limit (in-memory sliding window)."""

import time

from src.server.rate_limit import _reset_for_tests, check_rate_limit


def setup_function() -> None:
    _reset_for_tests()


def test_under_limit_returns_true():
    for _ in range(5):
        assert check_rate_limit("pk_live_aaa", 10) is True


def test_over_limit_returns_false():
    for _ in range(10):
        assert check_rate_limit("pk_live_aaa", 10) is True
    assert check_rate_limit("pk_live_aaa", 10) is False


def test_independent_buckets_per_key_prefix():
    for _ in range(10):
        assert check_rate_limit("pk_live_aaa", 10) is True
    assert check_rate_limit("pk_live_bbb", 10) is True


def test_window_slides(monkeypatch):
    """Simulate the sliding window by manipulating the underlying clock."""
    base = [1000.0]

    def fake_monotonic() -> float:
        return base[0]

    monkeypatch.setattr("src.server.rate_limit.time.monotonic", fake_monotonic)

    for _ in range(10):
        assert check_rate_limit("pk_live_slide", 10) is True
    assert check_rate_limit("pk_live_slide", 10) is False

    base[0] += 61.0
    assert check_rate_limit("pk_live_slide", 10) is True


def test_real_time_unaffected():
    """Sanity check: a single call uses real time and returns True."""
    assert check_rate_limit("pk_live_real", 100) is True
    assert isinstance(time.monotonic(), float)
