"""Request correlation metadata used by structured logs and audit events."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import Request


@dataclass(frozen=True, slots=True)
class RequestMeta:
    request_id: UUID
    ip_address: str | None


def get_request_meta(request: Request) -> RequestMeta:
    request_id = getattr(request.state, "request_id", None)
    if not isinstance(request_id, UUID):
        request_id = uuid4()

    ip_address = getattr(request.state, "client_ip", None)
    if not ip_address and request.client is not None:
        ip_address = request.client.host

    return RequestMeta(request_id=request_id, ip_address=ip_address)
