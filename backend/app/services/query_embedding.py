"""Query embedding generation for permission-aware retrieval.

The query must use exactly the same model family and version that produced the
document vectors. A 384-dimensional vector from a different model is still 384
dimensions but lives in a different embedding space, so retrieval also filters
on the stored ``documents.embedding_model`` label.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.services.embedding_service import (
    EmbeddingService,
    EmbeddingServiceError,
    get_embedding_service,
)


@dataclass(frozen=True, slots=True)
class QueryEmbedding:
    vector: list[float]
    model_label: str


def validate_question(question: str, *, max_chars: int | None = None) -> str:
    """Normalize and bound a user question before loading the model."""
    if question is None:
        raise BadRequestError("Question is required")

    normalized = question.strip()
    if not normalized:
        raise BadRequestError("Question cannot be empty")
    if "\x00" in normalized:
        raise BadRequestError("Question contains invalid characters")

    limit = max_chars if max_chars is not None else get_settings().rag_max_question_chars
    if len(normalized) > limit:
        raise BadRequestError(f"Question must be at most {limit} characters")
    return normalized


def _validate_vector(vector: list[float], dimension: int) -> list[float]:
    if len(vector) != dimension:
        raise ServiceUnavailableError(
            "The embedding model returned a vector with an unexpected dimension"
        )
    if any(not math.isfinite(float(value)) for value in vector):
        raise ServiceUnavailableError(
            "The embedding model returned a vector with invalid values"
        )
    return [float(value) for value in vector]


def generate_query_embedding(
    question: str,
    *,
    embedding_service: EmbeddingService | None = None,
    max_chars: int | None = None,
) -> QueryEmbedding:
    """Generate a normalized query vector using the ingestion embedding model."""
    normalized = validate_question(question, max_chars=max_chars)
    service = embedding_service or get_embedding_service()

    try:
        vector = service.generate_embedding(normalized)
    except EmbeddingServiceError as exc:
        # Never expose model paths, stack traces or partial vectors.
        raise ServiceUnavailableError("The embedding model is unavailable") from exc

    validated = _validate_vector(vector, service.dimension)
    return QueryEmbedding(vector=validated, model_label=service.model_label)
