"""Schemas for permission-aware RAG requests and responses."""

from uuid import UUID

from pydantic import Field

from app.schemas.common import StrictRequestModel


class RAGAskRequest(StrictRequestModel):
    """Only the question and retrieval breadth are accepted from the client.

    Tenant identity, roles, attributes and permission decisions are never
    accepted here. ``StrictRequestModel`` rejects unknown fields such as
    ``tenant_id`` with a 422 instead of silently ignoring them.
    """

    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=10, ge=1, le=50)


class RAGSource(StrictRequestModel):
    document_id: UUID
    document_name: str
    chunk_id: UUID
    page_start: int
    page_end: int


class RAGAskResponse(StrictRequestModel):
    answer: str
    sources: list[RAGSource] = Field(default_factory=list)
    retrieved_chunks: int = 0
    model: str | None = None


class RAGHealthComponent(StrictRequestModel):
    status: str
    detail: str | None = None


class RAGHealthResponse(StrictRequestModel):
    status: str
    components: dict[str, RAGHealthComponent]


class RAGHistoryItem(StrictRequestModel):
    question: str | None = None
    answer: str | None = None
    sources: list[RAGSource] = Field(default_factory=list)


class RAGHistoryResponse(StrictRequestModel):
    items: list[RAGHistoryItem] = Field(default_factory=list)
    persistent: bool = False
