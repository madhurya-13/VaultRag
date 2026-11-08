"""Application-level exceptions and their HTTP mapping.

Keeping HTTP concepts out of the service layer makes the business rules reusable
from the CLI bootstrap script, tests and future workers. ``main.py`` registers a
single handler that translates every ``AppError`` into a JSON response.
"""

from typing import Any


class AppError(Exception):
    """Base class for expected, safe-to-expose application failures."""

    status_code: int = 400
    default_detail: str = "Request could not be processed"

    def __init__(
        self,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.detail: Any = detail or self.default_detail
        self.headers = headers
        super().__init__(str(self.detail))


class BadRequestError(AppError):
    status_code = 400
    default_detail = "Invalid request"


class UnauthorizedError(AppError):
    status_code = 401
    default_detail = "Authentication required"

    def __init__(self, detail: str | None = None, **kwargs: Any) -> None:
        # RFC 6750: a 401 must advertise the authentication scheme.
        headers = {"WWW-Authenticate": "Bearer"}
        headers.update(kwargs.pop("headers", {}) or {})
        super().__init__(detail, headers=headers, **kwargs)


class ForbiddenError(AppError):
    status_code = 403
    default_detail = "You do not have permission to perform this action"


class NotFoundError(AppError):
    status_code = 404
    default_detail = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    default_detail = "Resource already exists"


class PayloadTooLargeError(AppError):
    status_code = 413
    default_detail = "Uploaded file is too large"


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    default_detail = "Unsupported file type"


class UnprocessableDocumentError(AppError):
    status_code = 422
    default_detail = "Document could not be processed"


class ServiceUnavailableError(AppError):
    status_code = 503
    default_detail = "Required processing service is unavailable"
