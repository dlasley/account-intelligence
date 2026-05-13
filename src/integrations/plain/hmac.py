"""HMAC-SHA256 signature verification for Plain webhooks (ADR-020 D10).

Plain sends a `Plain-Request-Signature` header with value `sha256=<hex>`.
Verification is done by recomputing HMAC-SHA256 over the raw request body
using the per-workspace webhook secret stored in external_credentials.

Reference: https://www.plain.com/docs/api-reference/webhooks
"""

import hashlib
import hmac


def verify_plain_signature(body_bytes: bytes, signature_header: str, secret: str) -> bool:
    """Verify a Plain webhook request signature.

    Args:
        body_bytes: Raw (unparsed) request body bytes.
        signature_header: Value of the Plain-Request-Signature header (e.g. "sha256=abc123").
        secret: Plaintext webhook signing secret from external_credentials.

    Returns:
        True if the signature is valid; False otherwise.
    """
    if not signature_header.startswith("sha256="):
        return False
    received_hex = signature_header[len("sha256="):]
    expected_hex = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_hex, received_hex)
