"""Encrypted credential storage for integrations.

A credential payload is a small JSON object (an OAuth access/refresh token, a
client id and secret, or a local path). It is encrypted with AES-256-GCM under
``INTEGRATION_TOKEN_ENCRYPTION_KEY`` and bound to its tenant, integration and
provider with associated data. The plaintext never reaches an API response, a
log line or an audit row.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.crypto import (
    EncryptedSecret,
    build_aad,
    decrypt_secret,
    encrypt_secret,
)
from app.integrations.exceptions import (
    CredentialEncryptionError,
    IntegrationNotFoundError,
    IntegrationValidationError,
)
from app.integrations.models import Integration, IntegrationCredential

MAX_CREDENTIAL_BYTES = 16 * 1024


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(payload: Mapping[str, Any]) -> bytes:
    try:
        raw = json.dumps(dict(payload), separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise IntegrationValidationError(
            "Provider credentials must be a JSON object"
        ) from exc
    encoded = raw.encode("utf-8")
    if len(encoded) > MAX_CREDENTIAL_BYTES:
        raise IntegrationValidationError("Provider credentials are too large")
    return encoded


def store_credentials(
    db: Session,
    *,
    integration: Integration,
    payload: Mapping[str, Any],
    scopes: str | None = None,
    token_expires_at: datetime | None = None,
) -> IntegrationCredential:
    """Encrypt and upsert the credential row for one integration.

    ``payload`` is never logged. AAD binds the ciphertext to the integration, so
    copying the row to another tenant makes it undecryptable.
    """
    plaintext = _serialize(payload)
    secret = encrypt_secret(
        plaintext,
        aad=build_aad(
            tenant_id=integration.tenant_id,
            integration_id=integration.integration_id,
            provider=integration.provider,
        ),
    )
    credential = db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.tenant_id == integration.tenant_id,
            IntegrationCredential.integration_id == integration.integration_id,
            IntegrationCredential.provider == integration.provider,
        )
    )
    if credential is None:
        credential = IntegrationCredential(
            tenant_id=integration.tenant_id,
            integration_id=integration.integration_id,
            provider=integration.provider,
            ciphertext=secret.ciphertext,
            nonce=secret.nonce,
            key_version=secret.key_version,
            scopes=scopes,
            token_expires_at=token_expires_at,
        )
        db.add(credential)
    else:
        credential.ciphertext = secret.ciphertext
        credential.nonce = secret.nonce
        credential.key_version = secret.key_version
        credential.scopes = scopes
        credential.token_expires_at = token_expires_at
        credential.revoked_at = None
    db.flush()
    return credential


def has_credentials(db: Session, *, integration: Integration) -> bool:
    credential = db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.tenant_id == integration.tenant_id,
            IntegrationCredential.integration_id == integration.integration_id,
            IntegrationCredential.provider == integration.provider,
            IntegrationCredential.revoked_at.is_(None),
        )
    )
    return credential is not None


def load_credentials(
    db: Session, *, integration: Integration
) -> dict[str, Any]:
    """Decrypt the stored payload. Missing or revoked credentials fail closed."""
    credential = db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.tenant_id == integration.tenant_id,
            IntegrationCredential.integration_id == integration.integration_id,
            IntegrationCredential.provider == integration.provider,
            IntegrationCredential.revoked_at.is_(None),
        )
    )
    if credential is None:
        return {}
    try:
        plaintext = decrypt_secret(
            EncryptedSecret(
                ciphertext=credential.ciphertext,
                nonce=credential.nonce,
                key_version=credential.key_version,
            ),
            aad=build_aad(
                tenant_id=integration.tenant_id,
                integration_id=integration.integration_id,
                provider=integration.provider,
            ),
        )
        payload = json.loads(plaintext)
    except (CredentialEncryptionError, ValueError) as exc:
        raise CredentialEncryptionError(
            "Stored provider credentials could not be read"
        ) from exc
    if not isinstance(payload, dict):
        raise CredentialEncryptionError(
            "Stored provider credentials are invalid"
        )
    return payload


def revoke_credentials(db: Session, *, integration: Integration) -> None:
    """Make stored credentials unreadable while keeping the audit row."""
    credential = db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.tenant_id == integration.tenant_id,
            IntegrationCredential.integration_id == integration.integration_id,
            IntegrationCredential.provider == integration.provider,
        )
    )
    if credential is None:
        return
    credential.ciphertext = b"\x00"
    credential.nonce = b"\x00" * 12
    credential.revoked_at = _utcnow()
    db.flush()


def credential_summary(
    db: Session, *, integration: Integration
) -> dict[str, Any] | None:
    """Non-secret metadata for the API: presence, scopes and expiry."""
    credential = db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.tenant_id == integration.tenant_id,
            IntegrationCredential.integration_id == integration.integration_id,
            IntegrationCredential.provider == integration.provider,
        )
    )
    if credential is None:
        return None
    return {
        "configured": credential.revoked_at is None,
        "scopes": credential.scopes,
        "token_expires_at": credential.token_expires_at,
        "updated_at": credential.updated_at,
        "revoked_at": credential.revoked_at,
    }


def require_integration(
    db: Session, *, tenant_id: UUID, integration_id: UUID
) -> Integration:
    integration = db.scalar(
        select(Integration).where(
            Integration.tenant_id == tenant_id,
            Integration.integration_id == integration_id,
        )
    )
    if integration is None:
        raise IntegrationNotFoundError("Integration not found")
    return integration
