"""Unit tests for src.integrations.pylon.hmac.verify_pylon_signature."""

import hashlib
import hmac as hmac_stdlib

from src.integrations.pylon.hmac import verify_pylon_signature

_SECRET = "test-pylon-webhook-secret-abc"
_BODY = b'{"data":{"id":"evt_001","type":"issue.created"}}'
_TIMESTAMP = "1746700800"  # arbitrary fixed Unix timestamp


def _make_signature(body: bytes, timestamp: str, secret: str) -> str:
    """Construct expected Pylon signature: HMAC-SHA256(timestamp + "." + body)."""
    signing_payload = (timestamp + ".").encode() + body
    return hmac_stdlib.new(secret.encode(), signing_payload, hashlib.sha256).hexdigest()


# ─── Valid signature ──────────────────────────────────────────────────────────


def test_valid_signature_returns_true():
    sig = _make_signature(_BODY, _TIMESTAMP, _SECRET)
    assert verify_pylon_signature(_BODY, sig, _TIMESTAMP, _SECRET) is True


def test_valid_signature_different_body_returns_true():
    body = b'{"data":{"id":"evt_002","type":"issue.message_added"}}'
    sig = _make_signature(body, _TIMESTAMP, _SECRET)
    assert verify_pylon_signature(body, sig, _TIMESTAMP, _SECRET) is True


# ─── Invalid signature ────────────────────────────────────────────────────────


def test_wrong_secret_returns_false():
    sig = _make_signature(_BODY, _TIMESTAMP, "wrong-secret")
    assert verify_pylon_signature(_BODY, sig, _TIMESTAMP, _SECRET) is False


def test_tampered_body_returns_false():
    sig = _make_signature(_BODY, _TIMESTAMP, _SECRET)
    tampered = _BODY + b" extra"
    assert verify_pylon_signature(tampered, sig, _TIMESTAMP, _SECRET) is False


def test_wrong_timestamp_returns_false():
    sig = _make_signature(_BODY, "9999999999", _SECRET)
    # Signature was computed with a different timestamp — should fail
    assert verify_pylon_signature(_BODY, sig, _TIMESTAMP, _SECRET) is False


def test_wrong_hex_returns_false():
    assert verify_pylon_signature(_BODY, "000000deadbeef", _TIMESTAMP, _SECRET) is False


# ─── Missing headers ─────────────────────────────────────────────────────────


def test_empty_signature_returns_false():
    assert verify_pylon_signature(_BODY, "", _TIMESTAMP, _SECRET) is False


def test_empty_timestamp_returns_false():
    sig = _make_signature(_BODY, _TIMESTAMP, _SECRET)
    assert verify_pylon_signature(_BODY, sig, "", _SECRET) is False


def test_both_headers_empty_returns_false():
    assert verify_pylon_signature(_BODY, "", "", _SECRET) is False
