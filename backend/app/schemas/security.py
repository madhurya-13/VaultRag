"""Security audit API schemas."""

from datetime import datetime
from uuid import UUID

from app.schemas.common import StrictRequestModel


class SecurityAuditLogResponse(StrictRequestModel):
    event_id: UUID
    tenant_id: UUID
    user_id: UUID | None
    resource_type: str
    resource_id: UUID | None
    action: str
    decision: str
    reason: str
    policy: str
    ip_address: str | None
    request_id: UUID
    timestamp: datetime
