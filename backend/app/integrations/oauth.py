"""OAuth authorization-code flow for provider integrations.

The browser never receives a client secret, a PKCE verifier or a provider token.
The unauthenticated callback carries a signed, single-use state value from which
the tenant is derived; the state row is then read under that tenant's RLS
context. Nothing in the callback trusts a request parameter for identity.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BadRequestError
from app.integrations.crypto import encrypt_secret, decrypt_secret, EncryptedSecret
from app.integrations.credentials import load_credentials, store_credentials
from app.integrations.exceptions import (
    IntegrationValidationError,
    ProviderAuthenticationError,
    ProviderRequestError,
)
from app.integrations.models import Integration, IntegrationOAuthState
from app.integrations.service import audit
from app.core.request_context import RequestMeta
from app.core.security_constants import INTEGRATION_STATUS_CONNECTED, PROVIDER_GOOGLE_DRIVE

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
MICROSOFT_AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

GOOGLE_SCOPES = "https://www.googleapis.com/auth/drive.readonly"
MICROSOFT_SCOPES = "https://graph.microsoft.com/Files.Read.All offline_access"

_STATE_TTL_SECONDS = 600


def _secret() -> bytes:
    return get_settings().jwt_secret_key.get_secret_value().encode("utf-8")


def _mac(payload: str) -> str:
    digest = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def callback_url() -> str:
    base = get_settings().integration_oauth_redirect_base
    if not base:
        raise IntegrationValidationError(
            "INTEGRATION_OAUTH_REDIRECT_BASE must be configured for OAuth"
        )
    return base.rstrip("/") + "/integrations/oauth/callback"


def provider_endpoints(provider: str) -> tuple[str, str, str]:
    if provider == PROVIDER_GOOGLE_DRIVE:
        return GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL, GOOGLE_SCOPES
    if provider == "sharepoint":
        return MICROSOFT_AUTH_URL, MICROSOFT_TOKEN_URL, MICROSOFT_SCOPES
    raise IntegrationValidationError("This provider does not support OAuth")


def begin_oauth(
    db: Session,
    *,
    integration: Integration,
    actor_user_id: UUID,
    request_meta: RequestMeta | None = None,
) -> dict[str, Any]:
    """Create a single-use state row and return the authorization URL."""
    if not get_settings().integration_oauth_redirect_base:
        raise IntegrationValidationError(
            "OAuth is not configured on this deployment"
        )
    credentials = load_credentials(db, integration=integration)
    client_id = credentials.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise IntegrationValidationError(
            "Store the provider client_id before starting OAuth"
        )
    auth_url, _, scopes = provider_endpoints(integration.provider)
    state_id = uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=min(
            get_settings().integration_oauth_state_ttl_seconds, _STATE_TTL_SECONDS
        )
    )
    signing_payload = f"{state_id}:{integration.tenant_id}:{integration.integration_id}:{int(expires_at.timestamp())}"
    state = f"{signing_payload}:{_mac(signing_payload)}"
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    redirect_uri = callback_url()
    encrypted_verifier = encrypt_secret(verifier)

    db.add(
        IntegrationOAuthState(
            state_id=state_id,
            tenant_id=integration.tenant_id,
            integration_id=integration.integration_id,
            provider=integration.provider,
            state_hash=_state_hash(state),
            redirect_uri=redirect_uri,
            verifier_ciphertext=encrypted_verifier.ciphertext,
            verifier_nonce=encrypted_verifier.nonce,
            expires_at=expires_at,
            created_by=actor_user_id,
        )
    )
    db.flush()
    audit(
        db,
        tenant_id=integration.tenant_id,
        user_id=actor_user_id,
        integration_id=integration.integration_id,
        action="INTEGRATION_OAUTH_START",
        reason=f"provider={integration.provider}",
        request_meta=request_meta,
    )

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if integration.provider == PROVIDER_GOOGLE_DRIVE:
        params["access_type"] = "offline"
        params["prompt"] = "consent"
    return {
        "authorization_url": f"{auth_url}?{urlencode(params)}",
        "expires_at": expires_at,
    }


def _parse_state(state: str) -> tuple[UUID, UUID, UUID, int]:
    parts = state.split(":")
    if len(parts) != 5:
        raise IntegrationValidationError("Invalid OAuth state")
    state_id, tenant_raw, integration_raw, expiry_raw, mac = parts
    payload = ":".join(parts[:4])
    if not hmac.compare_digest(mac, _mac(payload)):
        raise IntegrationValidationError("Invalid OAuth state")
    try:
        return (
            UUID(state_id),
            UUID(tenant_raw),
            UUID(integration_raw),
            int(expiry_raw),
        )
    except ValueError as exc:
        raise IntegrationValidationError("Invalid OAuth state") from exc


def complete_oauth(
    db: Session,
    *,
    code: str,
    state: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Integration:
    """Exchange the authorization code and store the encrypted credentials."""
    from app.core.database import set_tenant_context

    state_id, tenant_id, integration_id, expiry = _parse_state(state)
    if expiry < int(time.time()):
        raise BadRequestError("The OAuth authorization expired; start again")
    set_tenant_context(db, tenant_id)

    row = db.scalar(
        select(IntegrationOAuthState).where(
            IntegrationOAuthState.tenant_id == tenant_id,
            IntegrationOAuthState.state_id == state_id,
            IntegrationOAuthState.integration_id == integration_id,
        )
    )
    if row is None or row.consumed_at is not None:
        raise BadRequestError("The OAuth authorization is no longer valid")
    if row.state_hash != _state_hash(state):
        raise BadRequestError("The OAuth authorization is no longer valid")
    if row.expires_at <= datetime.now(timezone.utc):
        raise BadRequestError("The OAuth authorization expired; start again")
    row.consumed_at = datetime.now(timezone.utc)
    db.flush()

    integration = db.scalar(
        select(Integration).where(
            Integration.tenant_id == tenant_id,
            Integration.integration_id == integration_id,
        )
    )
    if integration is None:
        raise BadRequestError("Integration not found")

    try:
        verifier = decrypt_secret(
            EncryptedSecret(
                ciphertext=row.verifier_ciphertext, nonce=row.verifier_nonce
            )
        )
    except Exception as exc:  # pragma: no cover - key rotation edge case
        raise BadRequestError("The OAuth verifier could not be read") from exc

    credentials = load_credentials(db, integration=integration)
    client_id = credentials.get("client_id")
    client_secret = credentials.get("client_secret")
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        raise IntegrationValidationError(
            "Store the provider client_id and client_secret before OAuth"
        )

    _, token_url, _ = provider_endpoints(integration.provider)
    try:
        response = httpx.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": row.redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": verifier,
            },
            timeout=httpx.Timeout(20.0, connect=5.0),
            transport=transport,
        )
    except httpx.HTTPError as exc:
        raise ProviderRequestError("The provider token exchange failed") from exc
    if response.status_code != 200:
        raise ProviderAuthenticationError(
            "The provider rejected the authorization code"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderRequestError(
            "The provider returned an unreadable token response"
        ) from exc
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ProviderAuthenticationError(
            "The provider returned no access token"
        )
    stored = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": access_token,
    }
    if isinstance(payload.get("refresh_token"), str):
        stored["refresh_token"] = payload["refresh_token"]
    expires_in = payload.get("expires_in")
    token_expires_at = None
    if isinstance(expires_in, (int, float)):
        token_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=float(expires_in)
        )
    store_credentials(
        db,
        integration=integration,
        payload=stored,
        scopes=str(payload.get("scope") or ""),
        token_expires_at=token_expires_at,
    )
    integration.connection_status = INTEGRATION_STATUS_CONNECTED
    audit(
        db,
        tenant_id=tenant_id,
        user_id=integration.created_by,
        integration_id=integration.integration_id,
        action="INTEGRATION_OAUTH_COMPLETE",
        reason="Authorization code exchanged",
    )
    return integration
