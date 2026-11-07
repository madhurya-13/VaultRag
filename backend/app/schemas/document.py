"""Document API schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.common import StrictRequestModel


class DocumentCreateRequest(StrictRequestModel):
    document_name: str = Field(min_length=1, max_length=255)
    document_type: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=50)
    classification: str = Field(min_length=1, max_length=20)
    required_clearance_level: int | None = Field(default=None, ge=0, le=5)


class DocumentResponse(StrictRequestModel):
    document_id: UUID
    tenant_id: UUID
    document_name: str
    document_type: str
    department: str
    classification: str
    required_clearance_level: int
    uploaded_by: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime
