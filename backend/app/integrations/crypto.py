"""Envelope encryption for stored provider credentials.

Provider refresh tokens are the highest-value secret VaultRAG holds: they grant
access to a customer's document estate. They are therefore encrypted at rest
with AES-256-GCM under a deployment key supplied through
``INTEGRATION_TOKEN_ENCRYPTION_KEY``.

Properties:

* 96-bit random nonce per encryption, never reused with the same key.
* Associated data binds the ciphertext to its tenant/integration/provider, so a
  row copied into another integration or tenant cannot be decrypted even by an
  attacker with database write access.
* ``key_version`` is stored beside the ciphertext so a future key rotation can
  re-wrap existing secrets without guessing which key was used.
* No plaintext, key material or ciphertext ever reaches a log, an API response
  or an audit record.

The key is read lazily: a deployment that does not use integrations (or the
test suite) can boot without it, and any attempt to connect a provider fails
with a clear, safe error instead of a stack trace.
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings
from app.integrations.exceptions import CredentialEncryptionError

KEY_BYTES = 32
NONCE_BYTES = 12
CURRENT_KEY_VERSION = 1


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    """Ciphertext plus everything needed to decrypt it later."""

    ciphertext: bytes
    nonce: bytes
    key_version: int = CURRENT_KEY_VERSION


def generate_key() -> str:
    """Return a fresh base64url deployment key for operator configuration.

    Used by the setup docs and the key-rotation CLI. The value is a secret: it
    belongs in ``.env`` or the platform secret store, never in Git or chat.
    """
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode("ascii")


def decode_key(raw: str | bytes) -> bytes:
    """Decode a base64url (or base64) 32-byte key, rejecting anything else."""
    if isinstance(raw, bytes):
        candidate = raw.strip()
    else:
        candidate = str(raw).strip().encode("ascii", "ignore")
    padded = candidate + b"=" * (-len(candidate) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise CredentialEncryptionError(
            "INTEGRATION_TOKEN_ENCRYPTION_KEY is not valid base64"
        ) from exc
    if len(decoded) != KEY_BYTES:
        raise CredentialEncryptionError(
            "INTEGRATION_TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes"
        )
    return decoded


def get_encryption_key() -> bytes:
    """Read the deployment key from settings, failing closed when absent."""
    configured = get_settings().integration_token_encryption_key
    if configured is None or not configured.get_secret_value().strip():
        raise CredentialEncryptionError(
            "INTEGRATION_TOKEN_ENCRYPTION_KEY is not set; provider credentials "
            "cannot be stored or read"
        )
    return decode_key(configured.get_secret_value())


def build_aad(*, tenant_id: Any, integration_id: Any, provider: str) -> bytes:
    """Associated data binding a ciphertext to exactly one integration row."""
    scope = f"vaultrag.integration:{tenant_id}:{integration_id}:{provider.lower()}"
    return scope.encode("utf-8")


def encrypt_secret(
    plaintext: str | bytes,
    *,
    aad: bytes | None = None,
    key: bytes | None = None,
) -> EncryptedSecret:
    """Encrypt a credential payload. Returns nonce + ciphertext, never the key."""
    secret_key = key or get_encryption_key()
    if len(secret_key) != KEY_BYTES:
        raise CredentialEncryptionError("The credential encryption key is invalid")
    payload = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(secret_key).encrypt(nonce, payload, aad)
    return EncryptedSecret(ciphertext=ciphertext, nonce=nonce)


def decrypt_secret(
    secret: EncryptedSecret,
    *,
    aad: bytes | None = None,
    key: bytes | None = None,
) -> str:
    """Decrypt a credential payload, raising a safe error on any failure.

    A failed authentication tag (wrong key, tampered ciphertext, wrong tenant
    binding) is indistinguishable to the caller by design.
    """
    secret_key = key or get_encryption_key()
    if secret.key_version != CURRENT_KEY_VERSION:
        raise CredentialEncryptionError(
            "The stored credential uses an unsupported key version"
        )
    try:
        plaintext = AESGCM(secret_key).decrypt(secret.nonce, secret.ciphertext, aad)
    except Exception as exc:
        raise CredentialEncryptionError(
            "Stored provider credentials could not be decrypted"
        ) from exc
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - defensive
        raise CredentialEncryptionError(
            "Stored provider credentials are not valid text"
        ) from exc
