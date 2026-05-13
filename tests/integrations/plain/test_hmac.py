"""Unit tests for src.integrations.plain.hmac.verify_plain_signature."""

import hashlib
import hmac as hmac_stdlib

from src.integrations.plain.hmac import verify_plain_signature

_SECRET = "test-webhook-secret-abc"
_BODY = b'{"id":"evt_001","type":"thread.created"}'


def _make_signature(body: bytes, secret: str) -> str:
    hex_digest = hmac_stdlib.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={hex_digest}"


# ─── Valid signature ──────────────────────────────────────────────────────────


def test_valid_signature_returns_true():
    sig = _make_signature(_BODY, _SECRET)
    assert verify_plain_signature(_BODY, sig, _SECRET) is True


def test_valid_signature_different_body_returns_true():
    body = b'{"id":"evt_002","type":"email.received","workspaceId":"worksp_xyz"}'
    sig = _make_signature(body, _SECRET)
    assert verify_plain_signature(body, sig, _SECRET) is True


# ─── Invalid signature ────────────────────────────────────────────────────────


def test_wrong_secret_returns_false():
    sig = _make_signature(_BODY, "wrong-secret")
    assert verify_plain_signature(_BODY, sig, _SECRET) is False


def test_tampered_body_returns_false():
    sig = _make_signature(_BODY, _SECRET)
    tampered = _BODY + b" extra"
    assert verify_plain_signature(tampered, sig, _SECRET) is False


def test_wrong_hex_returns_false():
    assert verify_plain_signature(_BODY, "sha256=000000", _SECRET) is False


# ─── Missing / malformed header ───────────────────────────────────────────────


def test_missing_sha256_prefix_returns_false():
    hex_only = hmac_stdlib.new(_SECRET.encode(), _BODY, hashlib.sha256).hexdigest()
    # No "sha256=" prefix — should fail
    assert verify_plain_signature(_BODY, hex_only, _SECRET) is False


def test_empty_header_returns_false():
    assert verify_plain_signature(_BODY, "", _SECRET) is False


def test_header_with_wrong_prefix_returns_false():
    hex_digest = hmac_stdlib.new(_SECRET.encode(), _BODY, hashlib.sha256).hexdigest()
    assert verify_plain_signature(_BODY, f"md5={hex_digest}", _SECRET) is False
