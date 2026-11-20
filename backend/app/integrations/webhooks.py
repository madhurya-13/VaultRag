"""Provider webhook verification.

A webhook URL is public, so it carries a signed token that binds it to one
tenant and integration. The token is not an authorization mechanism for data:
it only lets the endpoint locate the integration under the correct RLS context.
The provider's own signature/secret is then checked against the stored
credential, and any doubt fails closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping
from uuid import UUID

from app.core.config import get_settings
from app.integrations.exceptions import WebhookValidationError
from app.integrations.models import Integration
from app.integrations.oauth import _mac
from app.core.security_constants import PROVIDER_GOOGLE_DRIVE, PROVIDER_SHAREPOINT


def webhook_token(tenant_id: UUID, integration_id: UUID) -> str:
    payload = f"{tenant_id}:{integration_id}"
    return f"{tenant_id}.{_mac(payload)}"


def verify_webhook_token(integration_id: UUID, token: str) -> UUID:
    if not isinstance(token, str) or "." not in token:
        raise WebhookValidationError("Invalid webhook token")
    tenant_raw, mac = token.split(".", 1)
    try:
        tenant_id = UUID(tenant_raw)
    except ValueError as exc:
        raise WebhookValidationError("Invalid webhook token") from exc
    payload = f"{tenant_id}:{integration_id}"
    if not hmac.compare_digest(mac, _mac(payload)):
        raise WebhookValidationError("Invalid webhook token")
    return tenant_id


def webhook_url(integration: Integration) -> str:
    base = get_settings().integration_oauth_redirect_base
    if not base:
        return ""
    token = webhook_token(integration.tenant_id, integration.integration_id)
    return (
        f"{base.rstrip('/')}/integrations/webhooks/{integration.integration_id}"
        f"?token={token}"
    )


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _google_event(headers: Mapping[str, str], body: bytes) -> tuple[str, str]:
    channel_id = headers.get("x-goog-channel-id") or ""
    resource_state = (headers.get("x-goog-resource-state") or "").lower()
    message_number = headers.get("x-goog-message-number") or body_digest(body)[:12]
    if not channel_id or not resource_state:
        raise WebhookValidationError("The Google notification is incomplete")
    if resource_state == "sync":
        return f"{channel_id}:{message_number}", "SYNC"
    return f"{channel_id}:{message_number}", resource_state.upper()


def _microsoft_event(body: bytes) -> tuple[str, str]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WebhookValidationError("The Microsoft notification is not JSON") from exc
    values = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(values, list) or not values:
        raise WebhookValidationError("The Microsoft notification has no events")
    item = values[0]
    if not isinstance(item, dict):
        raise WebhookValidationError("The Microsoft notification is invalid")
    event_id = str(item.get("subscriptionId") or "") + ":" + str(item.get("id") or "")
    if not item.get("id"):
        raise WebhookValidationError("The Microsoft notification has no id")
    return event_id, str(item.get("changeType") or "UPDATED").upper()


def verify_and_parse(
    *,
    provider: str,
    headers: Mapping[str, str],
    body: bytes,
    secrets: Mapping[str, Any],
) -> tuple[str, str]:
    """Verify the provider notification and return ``(event_id, event_type)``."""
    configured = secrets.get("webhook_secret")
    if provider == PROVIDER_GOOGLE_DRIVE:
        received = headers.get("x-goog-channel-token") or ""
        if not isinstance(configured, str) or not configured:
            raise WebhookValidationError(
                "This integration has no webhook secret configured"
            )
        if not hmac.compare_digest(received, configured):
            raise WebhookValidationError("The webhook secret does not match")
        return _google_event(headers, body)

    if provider == PROVIDER_SHAREPOINT:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise WebhookValidationError(
                "The Microsoft notification is not JSON"
            ) from exc
        values = payload.get("value") if isinstance(payload, dict) else None
        if not isinstance(values, list) or not values:
            raise WebhookValidationError("The Microsoft notification has no events")
        states = {
            str(item.get("clientState") or "")
            for item in values
            if isinstance(item, dict)
        }
        if not isinstance(configured, str) or not configured:
            raise WebhookValidationError(
                "This integration has no webhook secret configured"
            )
        if states != {configured}:
            raise WebhookValidationError("The webhook client state does not match")
        return _microsoft_event(body)

    raise WebhookValidationError("This provider does not send webhooks")
