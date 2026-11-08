"""Embedding validation and one real local-model integration test."""

import math
from typing import Any

import numpy as np
import pytest

from app.core.config import get_settings
from app.services.embedding_service import (
    EmbeddingService,
    InvalidEmbeddingError,
)
from app.tests.helpers import WhitespaceTokenizer


class _StubModel:
    def __init__(self, vectors: np.ndarray, tokenizer=None) -> None:
        self._vectors = vectors
        self.tokenizer = tokenizer or WhitespaceTokenizer()

    def encode(self, texts: list[str], **_: Any) -> np.ndarray:
        return self._vectors


def _service(dimension: int, vectors: np.ndarray) -> EmbeddingService:
    service = EmbeddingService(
        model_name="stub-model",
        model_version="test",
        dimension=dimension,
        batch_size=2,
    )
    service._model = _StubModel(vectors)
    return service


def test_generate_embeddings_returns_validated_vectors():
    service = _service(4, np.array([[0.1, 0.2, 0.3, 0.4]], dtype=np.float32))
    assert service.generate_embedding("hello") == pytest.approx(
        [0.1, 0.2, 0.3, 0.4]
    )
    assert service.model_label == "stub-model@test"


def test_generate_embeddings_rejects_wrong_dimension():
    service = _service(384, np.ones((1, 3), dtype=np.float32))
    with pytest.raises(InvalidEmbeddingError):
        service.generate_embeddings(["hello"])


def test_generate_embeddings_rejects_non_finite_values():
    vectors = np.ones((1, 4), dtype=np.float32)
    vectors[0, 0] = np.nan
    service = _service(4, vectors)
    with pytest.raises(InvalidEmbeddingError):
        service.generate_embeddings(["hello"])


def test_generate_embeddings_empty_batch_does_not_load_model():
    service = EmbeddingService(model_name="never-loaded", dimension=384)
    assert service.generate_embeddings([]) == []


def test_real_local_model_generates_384_dimension_normalized_vectors():
    settings = get_settings()
    service = EmbeddingService(
        model_name=settings.embedding_model_name,
        model_version=settings.embedding_model_version,
        dimension=settings.embedding_dimension,
        batch_size=2,
        device="cpu",
    )

    vectors = service.generate_embeddings(
        ["Security policies define access rules.", "Financial reports contain totals."]
    )

    assert len(vectors) == 2
    assert all(len(vector) == 384 for vector in vectors)
    assert all(math.isfinite(value) for vector in vectors for value in vector)
    for vector in vectors:
        assert abs(sum(value * value for value in vector) - 1.0) < 0.01
    assert service.count_tokens("hello world") >= 2
