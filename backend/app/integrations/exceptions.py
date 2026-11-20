"""Safe, expected integration failures.

Every exception here subclasses :class:`app.core.exceptions.AppError`, so the
existing ``main.py`` handler renders them with the same ``{"detail": ...}``
envelope as the rest of the API. Nothing in this module may carry a provider
token, a raw provider response body or another tenant's identifier in
``detail``: these messages reach the browser.
"""

from typing import Any

from app.core.exceptions import AppError


class IntegrationError(AppError):
    """Base class for integration failures the caller is allowed to see."""

    status_code = 400
    default_detail = "Integration request could not be processed"
    #: Stable machine-readable code persisted in sync jobs and audit logs.
    error_code: str = "INTEGRATION_ERROR"


class IntegrationNotConfiguredError(IntegrationError):
    status_code = 503
    default_detail = "Enterprise integrations are not configured on this deployment"
    error_code = "INTEGRATION_NOT_CONFIGURED"


class IntegrationNotFoundError(IntegrationError):
    status_code = 404
    default_detail = "Integration not found"
    error_code = "INTEGRATION_NOT_FOUND"


class IntegrationConflictError(IntegrationError):
    status_code = 409
    default_detail = "The integration is in a state that does not allow this action"
    error_code = "INTEGRATION_CONFLICT"


class IntegrationValidationError(IntegrationError):
    status_code = 422
    default_detail = "Integration configuration is invalid"
    error_code = "INTEGRATION_INVALID"


class ConnectorNotFoundError(IntegrationError):
    status_code = 404
    default_detail = "No connector is registered for this provider"
    error_code = "INTEGRATION_PROVIDER_UNKNOWN"


class ConnectorUnavailableError(IntegrationError):
    status_code = 503
    default_detail = "The provider connector is not available on this deployment"
    error_code = "INTEGRATION_PROVIDER_UNAVAILABLE"


class UnsupportedCapabilityError(IntegrationError):
    status_code = 422
    default_detail = "The provider does not support this operation"
    error_code = "INTEGRATION_CAPABILITY_UNSUPPORTED"

    def __init__(
        self,
        capability: Any | None = None,
        detail: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.capability = capability
        if detail is None and capability is not None:
            name = getattr(capability, "value", str(capability))
            detail = f"The provider does not support the {name} capability"
        super().__init__(detail, **kwargs)


class ProviderAuthenticationError(IntegrationError):
    """The stored provider credential was rejected or has expired.

    This is a *server-side* credential problem, never the caller's session, so
    it is reported as a dependency failure and the integration is moved to
    ``REAUTH_REQUIRED`` by the caller.
    """

    status_code = 502
    default_detail = "The external provider rejected the stored credentials"
    error_code = "INTEGRATION_PROVIDER_AUTH_FAILED"


class ProviderRequestError(IntegrationError):
    status_code = 502
    default_detail = "The external provider request failed"
    error_code = "INTEGRATION_PROVIDER_REQUEST_FAILED"


class ProviderRateLimitedError(ProviderRequestError):
    status_code = 503
    default_detail = "The external provider rate limited this request"
    error_code = "INTEGRATION_PROVIDER_RATE_LIMITED"


class CredentialEncryptionError(IntegrationError):
    status_code = 500
    default_detail = "Stored provider credentials cannot be decrypted"
    error_code = "INTEGRATION_CREDENTIAL_ENCRYPTION_FAILED"


class DownloadTooLargeError(IntegrationError):
    status_code = 413
    default_detail = "The external document exceeds the configured download limit"
    error_code = "INTEGRATION_DOWNLOAD_TOO_LARGE"


class UnsupportedExternalFileError(IntegrationError):
    status_code = 415
    default_detail = "The external document type cannot be ingested"
    error_code = "INTEGRATION_UNSUPPORTED_FILE_TYPE"


class ExternalPathError(IntegrationError):
    status_code = 400
    default_detail = "The external document path is not allowed"
    error_code = "INTEGRATION_UNSAFE_PATH"


class SyncAlreadyRunningError(IntegrationConflictError):
    default_detail = "A synchronization for this integration is already running"
    error_code = "INTEGRATION_SYNC_ALREADY_RUNNING"


class WebhookValidationError(IntegrationError):
    status_code = 401
    default_detail = "The webhook request could not be verified"
    error_code = "INTEGRATION_WEBHOOK_INVALID"
