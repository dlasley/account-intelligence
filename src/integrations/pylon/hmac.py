"""HMAC-SHA256 signature verification for Pylon webhooks (ADR-020 Phase 2.5).

Pylon sends three relevant headers:
  X-Pylon-Signature       — HMAC-SHA256 hex digest of the signing payload
  Pylon-Webhook-Timestamp — Unix timestamp string (used to construct the signing payload)
  Pylon-Webhook-Version   — currently "v1"; reserved for future scheme changes

Signing payload (current Pylon docs, https://docs.usepylon.com/pylon-docs/developer/webhooks):
    <timestamp> + "." + <raw body bytes (decoded as UTF-8)>

ALTERNATE SCHEME NOTE:
  An older Pylon Go SDK example signs only the raw body bytes without the timestamp prefix.
  The implementation here follows the current documentation (timestamp + "." + body).
  If real-traffic verification fails after wiring up a live Pylon webhook, try the
  body-only scheme:
      expected = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
  Swap the `_signing_payload` helper below and re-test against a known-good Pylon delivery.
  Do NOT implement both branches conditionally — that creates a security smell.

Reference: https://docs.usepylon.com/pylon-docs/developer/webhooks
"""

import hashlib
import hmac


def verify_pylon_signature(
    body_bytes: bytes,
    signature_header: str,
    timestamp_header: str,
    secret: str,
) -> bool:
    """Verify a Pylon webhook request signature.

    Args:
        body_bytes: Raw (unparsed) request body bytes.
        signature_header: Value of the X-Pylon-Signature header (hex digest, no prefix).
        timestamp_header: Value of the Pylon-Webhook-Timestamp header (Unix timestamp string).
        secret: Plaintext webhook signing secret from external_credentials.

    Returns:
        True if the signature is valid; False otherwise (including missing/empty headers).
    """
    if not signature_header or not timestamp_header:
        return False

    signing_payload = _signing_payload(timestamp_header, body_bytes)
    expected_hex = hmac.new(secret.encode(), signing_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_hex, signature_header)


def _signing_payload(timestamp: str, body_bytes: bytes) -> bytes:
    """Construct the Pylon signing payload: timestamp + "." + body."""
    return (timestamp + ".").encode() + body_bytes
