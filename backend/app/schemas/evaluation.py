"""Strict request and response schemas for the evaluation API."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import StrictRequestModel


class GoldChunk(BaseModel):
    document_name: str = Field(min_length=1, max_length=255)
    chunk_index: int = Field(ge=0, le=100000)


class DatasetCaseImport(StrictRequestModel):
    key: str = Field(min_length=1, max_length=80)
    category: Literal[
        "single_document",
        "multi_document",
        "comparison",
        "numeric",
        "temporal",
        "conflicting",
        "graph_relationship",
        "insufficient_evidence",
        "unanswerable",
        "restricted_document",
        "metadata_filter",
        "document_version",
        "permission_change",
        "citation",
    ]
    question: str = Field(min_length=1, max_length=4000)
    expected_answer: str | None = Field(default=None, max_length=8000)
    expected_answerable: bool = True
    allowed_document_names: list[str] = Field(default_factory=list, max_length=50)
    gold_document_names: list[str] = Field(default_factory=list, max_length=50)
    gold_chunks: list[GoldChunk] = Field(default_factory=list, max_length=200)
    expected_citation_document_names: list[str] = Field(
        default_factory=list, max_length=50
    )
    restricted_document_names: list[str] = Field(default_factory=list, max_length=50)
    filters: dict[str, str] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=2000)


class SyntheticDocument(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    department: str = Field(min_length=1, max_length=50)
    classification: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=20000)


class DatasetImportRequest(StrictRequestModel):
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(default=1, ge=1, le=1000)
    description: str | None = Field(default=None, max_length=4000)
    cases: list[DatasetCaseImport] = Field(min_length=1, max_length=200)
    documents: list[SyntheticDocument] = Field(default_factory=list, max_length=50)


class SyntheticDatasetRequest(StrictRequestModel):
    """Import the bundled dataset and optionally ingest its corpus."""

    create_documents: bool = True


class EvaluationRunRequest(StrictRequestModel):
    dataset_id: UUID
    configuration: Literal[
        "semantic",
        "keyword",
        "hybrid",
        "hybrid_rerank",
        "hybrid_graph",
        "hybrid_graph_rerank",
    ] = "hybrid"
    top_k: int = Field(default=5, ge=1, le=50)
    answer_mode: Literal["retrieval", "generated"] = "retrieval"
    judge_enabled: bool = False
    viewer_user_id: UUID | None = None


class EvaluationDatasetResponse(BaseModel):
    dataset_id: UUID
    name: str
    version: int
    description: str | None
    source: str
    query_count: int
    created_by: UUID
    created_at: datetime


class EvaluationDatasetListResponse(BaseModel):
    datasets: list[EvaluationDatasetResponse]


class EvaluationCaseResponse(BaseModel):
    case_id: UUID
    case_key: str
    category: str
    question: str
    expected_answer: str | None
    expected_answerable: bool
    allowed_document_names: list[str]
    gold_document_names: list[str]
    gold_chunks: list[dict[str, Any]]
    expected_citation_document_names: list[str]
    restricted_document_names: list[str]
    filters: dict[str, Any]
    notes: str | None


class EvaluationDatasetDetailResponse(EvaluationDatasetResponse):
    cases: list[EvaluationCaseResponse]


class EvaluationRunResponse(BaseModel):
    run_id: UUID
    dataset_id: UUID
    dataset_name: str | None = None
    configuration: str
    top_k: int
    answer_mode: str
    judge_enabled: bool
    viewer_user_id: UUID | None
    status: str
    case_count: int
    completed_cases: int
    failed_cases: int
    authorization_violations: int
    metrics: dict[str, Any]
    error_code: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class EvaluationRunListResponse(BaseModel):
    runs: list[EvaluationRunResponse]


class EvaluationResultResponse(BaseModel):
    result_id: UUID
    run_id: UUID
    case_id: UUID
    case_key: str
    category: str
    question: str
    expected_answer: str | None
    expected_answerable: bool
    answer: str | None
    retrieved_chunk_ids: list[str]
    retrieved_document_ids: list[str]
    citations: list[dict[str, Any]]
    metrics: dict[str, Any]
    passed: bool
    error_code: str | None
    latency_ms: int
    created_at: datetime


class EvaluationResultListResponse(BaseModel):
    results: list[EvaluationResultResponse]


class EvaluationConfigurationResponse(BaseModel):
    configuration: str
    search_mode: str
    rerank: bool
    graph: bool
    description: str


class EvaluationConfigurationsResponse(BaseModel):
    configurations: list[EvaluationConfigurationResponse]


class EvaluationMetricsResponse(BaseModel):
    """Tenant-scoped, content-free aggregate across recent runs."""

    total_runs: int
    completed_runs: int
    failed_runs: int
    total_cases: int
    passed_cases: int
    failed_cases: int
    authorization_violations: int
    average_latency_ms: float
    by_configuration: list[dict[str, Any]]
    recent_runs: list[EvaluationRunResponse]
