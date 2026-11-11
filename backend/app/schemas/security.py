"""Security audit API schemas."""

from datetime import date, datetime
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


class SecurityCount(StrictRequestModel):
    key: str
    count: int


class SecurityDailyCount(StrictRequestModel):
    date: date
    allowed: int
    denied: int
    total: int


class SecurityActorCount(StrictRequestModel):
    user_id: UUID
    username: str | None = None
    count: int


class SecurityAnalyticsResponse(StrictRequestModel):
    days: int
    since: datetime
    total_events: int
    allowed_events: int
    denied_events: int
    by_action: list[SecurityCount]
    by_resource_type: list[SecurityCount]
    daily: list[SecurityDailyCount]
    top_actors: list[SecurityActorCount]
