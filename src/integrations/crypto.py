"""AES-256-GCM encryption helpers for integration credentials (ADR-020 D3).

The INTEGRATION_ENCRYPTION_KEY env var holds a 32-byte value base64-encoded.
The worker holds this key; it never leaves the Cloud Run environment.

Ciphertext layout on-wire / in Postgres:
    [12-byte nonce][ciphertext][16-byte GCM tag]

The nonce is randomly generated per-encrypt; GCM tag is appended by the
cryptography library as part of the ciphertext output.

TODO: migrate to Supabase Vault when integration count warrants it (ADR-020 note).
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def get_integration_encryption_key() -> bytes:
    """Read and decode INTEGRATION_ENCRYPTION_KEY. Raises RuntimeError if missing or wrong length.
    """
    raw = os.environ.get("INTEGRATION_ENCRYPTION_KEY", "")
    if not raw:
        raise RuntimeError("INTEGRATION_ENCRYPTION_KEY env var is not set")
    try:
        key = base64.b64decode(raw)
    except Exception as exc:
        raise RuntimeError("INTEGRATION_ENCRYPTION_KEY is not valid base64") from exc
    if len(key) != 32:
        raise RuntimeError(
            f"INTEGRATION_ENCRYPTION_KEY must decode to 32 bytes; got {len(key)}"
        )
    return key


def encrypt_secret(plaintext: str, key: bytes) -> bytes:
    """Encrypt plaintext with AES-256-GCM. Returns [nonce || ciphertext+tag]."""
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext_tag = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return nonce + ciphertext_tag


def decrypt_secret(ciphertext: bytes, key: bytes) -> str:
    """Decrypt ciphertext produced by encrypt_secret. Raises on tamper."""
    if len(ciphertext) < 29:  # 12-byte nonce + 1-byte min ciphertext + 16-byte tag
        raise ValueError("ciphertext too short to be valid AES-256-GCM output")
    nonce = ciphertext[:12]
    ciphertext_tag = ciphertext[12:]
    aesgcm = AESGCM(key)
    plaintext_bytes = aesgcm.decrypt(nonce, ciphertext_tag, None)
    return plaintext_bytes.decode()
