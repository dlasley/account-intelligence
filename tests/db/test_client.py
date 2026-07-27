"""get_client() must explain what is missing rather than raising a bare KeyError."""

import pytest

from src.db.client import get_client

_REQUIRED = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")


@pytest.mark.parametrize("missing", _REQUIRED)
def test_missing_var_names_itself_and_points_at_the_fix(monkeypatch, missing):
    for name in _REQUIRED:
        monkeypatch.setenv(name, "placeholder")
    monkeypatch.delenv(missing)

    with pytest.raises(RuntimeError) as excinfo:
        get_client()

    message = str(excinfo.value)
    assert missing in message
    assert ".env" in message


def test_all_missing_names_every_one(monkeypatch):
    for name in _REQUIRED:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        get_client()

    for name in _REQUIRED:
        assert name in str(excinfo.value)


def test_empty_string_counts_as_missing(monkeypatch):
    """An exported-but-blank var is the common .env mistake; treat it as absent."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")

    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        get_client()
