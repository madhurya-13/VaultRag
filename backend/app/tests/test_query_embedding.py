"""Query embedding validation and failure handling."""

import math

import pytest

from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.services.embedding_service import EmbeddingServiceError
from app.services.query_embedding import generate_query_embedding
from app.tests.helpers import FakeEmbeddingService


def test_query_embedding_uses_ingestion_model_label():
    embedder = FakeEmbeddingService()
    result = generate_query_embedding("annual leave policy", embedding_service=embedder)

    assert result.model_label == embedder.model_label
    assert len(result.vector) == 384
    assert all(math.isfinite(value) for value in result.vector)


def test_blank_question_is_rejected_without_calling_model():
    embedder = FakeEmbeddingService()
    with pytest.raises(BadRequestError):
        generate_query_embedding("   ", embedding_service=embedder)
    assert embedder.calls == []


def test_excessively_long_question_is_rejected():
    embedder = FakeEmbeddingService()
    with pytest.raises(BadRequestError):
        generate_query_embedding("x" * 20, embedding_service=embedder, max_chars=10)
    assert embedder.calls == []


def test_invalid_dimension_is_rejected(monkeypatch):
    embedder = FakeEmbeddingService()
    monkeypatch.setattr(embedder, "generate_embedding", lambda _text: [0.0, 1.0])

    with pytest.raises(ServiceUnavailableError):
        generate_query_embedding("question", embedding_service=embedder)


def test_non_finite_values_are_rejected(monkeypatch):
    embedder = FakeEmbeddingService()
    monkeypatch.setattr(
        embedder,
        "generate_embedding",
        lambda _text: [float("nan")] * 384,
    )

    with pytest.raises(ServiceUnavailableError):
        generate_query_embedding("question", embedding_service=embedder)


def test_embedding_model_failure_is_a_controlled_503():
    embedder = FakeEmbeddingService()

    def fail(_text):
        raise EmbeddingServiceError("model unavailable")

    embedder.generate_embedding = fail  # type: ignore[method-assign]

    with pytest.raises(ServiceUnavailableError) as exc:
        generate_query_embedding("question", embedding_service=embedder)
    assert exc.value.status_code == 503
