"""Unit tests for the encryption helpers in src.integrations.crypto."""

import base64
import os

import pytest

from src.integrations.crypto import decrypt_secret, encrypt_secret, get_integration_encryption_key


def _make_key() -> bytes:
    return os.urandom(32)


# ─── Encryption round-trip ────────────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip():
    """encrypt then decrypt returns the original plaintext."""
    key = _make_key()
    plaintext = "grn_supersecretkey_12345"
    ciphertext = encrypt_secret(plaintext, key)
    assert decrypt_secret(ciphertext, key) == plaintext


def test_encrypt_produces_different_ciphertext_each_call():
    """Each call to encrypt_secret generates a fresh random nonce -> unique output."""
    key = _make_key()
    plaintext = "same_secret"
    ct1 = encrypt_secret(plaintext, key)
    ct2 = encrypt_secret(plaintext, key)
    assert ct1 != ct2


def test_ciphertext_longer_than_plaintext():
    """Ciphertext is at least 28 bytes longer than plaintext (12 nonce + 16 tag)."""
    key = _make_key()
    plaintext = "short"
    ciphertext = encrypt_secret(plaintext, key)
    assert len(ciphertext) == 12 + len(plaintext.encode()) + 16


def test_tamper_detected():
    """Bit-flip in ciphertext raises on decrypt (GCM authentication tag fails)."""
    from cryptography.exceptions import InvalidTag

    key = _make_key()
    ciphertext = bytearray(encrypt_secret("secret", key))
    ciphertext[20] ^= 0xFF  # flip a byte in the ciphertext portion
    with pytest.raises(InvalidTag):
        decrypt_secret(bytes(ciphertext), key)


def test_wrong_key_raises():
    """Decrypting with a different key fails the GCM tag check."""
    from cryptography.exceptions import InvalidTag

    key1 = _make_key()
    key2 = _make_key()
    ciphertext = encrypt_secret("supersecret", key1)
    with pytest.raises(InvalidTag):
        decrypt_secret(ciphertext, key2)


def test_too_short_ciphertext_raises_value_error():
    """Ciphertext shorter than minimum length raises ValueError before GCM attempt."""
    key = _make_key()
    with pytest.raises(ValueError, match="too short"):
        decrypt_secret(b"\x00" * 10, key)


# ─── get_integration_encryption_key ──────────────────────────────────────────


def test_get_key_success(monkeypatch):
    """Valid base64-encoded 32-byte key returns the raw bytes."""
    raw = os.urandom(32)
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY", base64.b64encode(raw).decode())
    assert get_integration_encryption_key() == raw


def test_get_key_missing_raises(monkeypatch):
    """Missing env var raises RuntimeError."""
    monkeypatch.delenv("INTEGRATION_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="not set"):
        get_integration_encryption_key()


def test_get_key_wrong_length_raises(monkeypatch):
    """16-byte key (wrong length) raises RuntimeError."""
    raw = os.urandom(16)
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY", base64.b64encode(raw).decode())
    with pytest.raises(RuntimeError, match="32 bytes"):
        get_integration_encryption_key()


def test_get_key_invalid_base64_raises(monkeypatch):
    """Non-base64 string raises RuntimeError."""
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY", "not-valid-base64!!!")
    with pytest.raises(RuntimeError, match="valid base64"):
        get_integration_encryption_key()
