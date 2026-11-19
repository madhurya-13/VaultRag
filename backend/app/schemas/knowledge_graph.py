"""Strict request/response schemas for the knowledge graph API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import StrictRequestModel

GraphSearchMode = Literal["graph", "graph_hybrid"]


class KGQueryRequest(StrictRequestModel):
    question: str = Field(min_length=1, max_length=2000)
    search_mode: GraphSearchMode = "graph"
    max_depth: int | None = Field(default=None, ge=1, le=5)
    include_findings: bool = True


class KGEvidence(BaseModel):
    evidence_id: UUID
    document_id: UUID
    document_name: str
    chunk_id: UUID
    page_start: int
    page_end: int
    quote: str
    confidence: float


class KGNode(BaseModel):
    node_id: UUID
    node_type: str
    name: str
    description: str | None = None
    confidence: float
    mention_count: int
    evidence: list[KGEvidence] = Field(default_factory=list)


class KGEdge(BaseModel):
    edge_id: UUID
    source_node_id: UUID
    target_node_id: UUID
    relation_type: str
    confidence: float
    evidence: list[KGEvidence] = Field(default_factory=list)


class KGFinding(BaseModel):
    subject: str
    relation: str
    object: str
    quote: str
    document_id: UUID
    document_name: str
    chunk_id: UUID
    page_start: int
    page_end: int
    confidence: float


class KGQueryResponse(BaseModel):
    search_mode: str
    seed_count: int
    denied_documents: int
    truncated: bool
    depth: int
    traversal_ms: int
    nodes: list[KGNode]
    edges: list[KGEdge]
    findings: list[KGFinding]
    candidate_chunks: int


class KGNodeDetailResponse(BaseModel):
    node: KGNode


class KGConflictValue(BaseModel):
    value: str
    document_id: UUID
    document_name: str
    quote: str


class KGConflict(BaseModel):
    conflict_id: UUID
    subject: str
    relation_type: str
    description: str
    status: str
    values: list[KGConflictValue]
    detected_at: datetime | None = None


class KGConflictListResponse(BaseModel):
    conflicts: list[KGConflict]


class KGReviewDecisionRequest(StrictRequestModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


class KGMergeRequest(StrictRequestModel):
    source_node_id: UUID
    target_node_id: UUID
    reason: str = Field(min_length=3, max_length=500)


class KGSplitRequest(StrictRequestModel):
    node_id: UUID
    alias: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=3, max_length=500)


class KGGovernanceResponse(BaseModel):
    action: str
    source_node_id: UUID
    target_node_id: UUID | None = None
    aliases_moved: int = 0
    edges_moved: int = 0
    evidence_moved: int = 0


class KGReprocessResponse(BaseModel):
    document_id: UUID
    job_id: UUID | None
    queued: bool


class KGAnalyticsMode(BaseModel):
    search_mode: str
    queries: int
    seed_count: int
    node_count: int
    edge_count: int
    evidence_count: int
    denied_documents: int
    average_traversal_ms: float
    average_total_ms: float
    retrieval_errors: int


class KGAnalyticsResponse(BaseModel):
    window_days: int
    total_queries: int
    modes: list[KGAnalyticsMode]
