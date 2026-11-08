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

    # Milestone 3 ingestion information. Storage paths are intentionally not
    # exposed through the API.
    file_hash: str | None = None
    file_size: int | None = None
    processing_status: str = "UPLOADED"
    total_pages: int = 0
    total_chunks: int = 0
    embedding_model: str | None = None


class DocumentStatusResponse(StrictRequestModel):
    document_id: UUID
    processing_status: str
    total_pages: int
    total_chunks: int
    embedding_model: str | None = None
    file_size: int | None = None
    job_id: UUID | None = None
    job_status: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class DocumentChunkResponse(StrictRequestModel):
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    chunk_text: str
    page_start: int
    page_end: int
    metadata: dict
    created_at: datetime
