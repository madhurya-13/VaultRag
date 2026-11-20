"""Request and response schemas for enterprise integrations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

PermissionSyncMode = Literal["VAULTRAG_MANAGED", "EXTERNAL_MIRROR", "ADMIN_MAPPED"]
SyncMode = Literal["FULL", "INCREMENTAL"]


class ProviderInfoResponse(BaseModel):
    provider: str
    display_name: str
    available: bool
    unavailable_reason: str | None = None
    capabilities: list[str]
    supports_oauth: bool


class ProviderListResponse(BaseModel):
    providers: list[ProviderInfoResponse]


class IntegrationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=32)
    integration_name: str = Field(min_length=1, max_length=120)
    permission_sync_mode: PermissionSyncMode = "VAULTRAG_MANAGED"
    sync_interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    configuration: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] | None = None

    @field_validator("configuration")
    @classmethod
    def _bound_configuration(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 20:
            raise ValueError("Too many integration settings")
        return value


class IntegrationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integration_name: str | None = Field(default=None, min_length=1, max_length=120)
    permission_sync_mode: PermissionSyncMode | None = None
    sync_enabled: bool | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=1, le=10080)


class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credentials: dict[str, Any]
    scopes: str | None = Field(default=None, max_length=1000)


class CredentialSummary(BaseModel):
    configured: bool
    scopes: str | None = None
    token_expires_at: datetime | None = None
    updated_at: datetime
    revoked_at: datetime | None = None


class IntegrationResponse(BaseModel):
    integration_id: UUID
    provider: str
    provider_display_name: str
    integration_name: str
    connection_status: str
    permission_sync_mode: str
    sync_enabled: bool
    sync_interval_minutes: int
    next_sync_at: datetime | None
    last_sync_at: datetime | None
    configuration: dict[str, Any]
    credential: CredentialSummary | None
    supports_oauth: bool
    webhook_configured: bool
    created_at: datetime
    updated_at: datetime


class IntegrationListResponse(BaseModel):
    integrations: list[IntegrationResponse]


class ConnectResponse(BaseModel):
    connected: bool
    connection_status: str
    detail: str | None = None


class OAuthStartResponse(BaseModel):
    authorization_url: str
    expires_at: datetime


class RepositoryResponse(BaseModel):
    repository_id: UUID
    external_repository_id: str
    name: str
    repository_kind: str
    is_selected: bool
    sync_mode: str
    last_synced_at: datetime | None


class RepositoryListResponse(BaseModel):
    repositories: list[RepositoryResponse]


class RepositorySelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: bool = True
    sync_mode: SyncMode | None = None


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync_type: Literal["FULL", "INCREMENTAL", "MANUAL"] = "MANUAL"


class SyncJobResponse(BaseModel):
    sync_job_id: UUID
    integration_id: UUID
    sync_type: str
    sync_status: str
    files_discovered: int
    files_processed: int
    files_failed: int
    versions_created: int
    documents_deleted: int
    attempt_count: int
    started_at: datetime | None
    completed_at: datetime | None
    next_attempt_at: datetime | None
    error_code: str | None
    error_summary: str | None
    created_at: datetime


class SyncJobListResponse(BaseModel):
    jobs: list[SyncJobResponse]


class ExternalDocumentResponse(BaseModel):
    external_document_id: UUID
    internal_document_id: UUID
    external_file_id: str
    external_repository_id: str
    external_version_id: str | None
    source_url: str | None
    source_path: str | None
    content_checksum: str | None
    file_size: int | None
    sync_status: str
    permission_state: str
    last_modified_at: datetime | None
    last_synced_at: datetime | None
    deleted_at: datetime | None


class ExternalDocumentListResponse(BaseModel):
    documents: list[ExternalDocumentResponse]


class IdentityMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_principal_id: str = Field(min_length=1, max_length=512)
    external_principal_type: Literal["USER", "GROUP", "ROLE", "DOMAIN", "ANYONE"]
    user_id: UUID | None = None
    role_id: UUID | None = None


class IdentityMappingResponse(BaseModel):
    mapping_id: UUID
    external_principal_id: str
    external_principal_type: str
    user_id: UUID | None
    role_id: UUID | None
    is_active: bool
    created_at: datetime


class IdentityMappingListResponse(BaseModel):
    mappings: list[IdentityMappingResponse]


class DocumentVersionResponse(BaseModel):
    document_version_id: UUID
    document_id: UUID
    version_number: int
    external_version_id: str | None
    content_checksum: str
    file_size: int
    ingestion_status: str
    is_current: bool
    source_modified_at: datetime | None
    created_at: datetime


class DocumentVersionListResponse(BaseModel):
    versions: list[DocumentVersionResponse]


class WebhookResponse(BaseModel):
    accepted: bool
    duplicate: bool
    queued: bool
