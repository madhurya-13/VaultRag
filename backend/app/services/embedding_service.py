"""Local Sentence Transformers embedding service.

The model is loaded once per process and reused for every document. No paid
embedding API is involved. Vectors are validated for exact dimension and
finite values before they can reach PostgreSQL.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Sequence

import numpy as np

from app.core.config import get_settings
from app.core.security_constants import (
    ERROR_EMBEDDING_MODEL,
    ERROR_INVALID_EMBEDDING,
)

logger = logging.getLogger(__name__)


class EmbeddingServiceError(Exception):
    code = ERROR_EMBEDDING_MODEL
    default_message = "The embedding model could not be loaded"

    def __init__(self, message: str | None = None) -> None:
        self.code = type(self).code
        self.safe_message = message or self.default_message
        super().__init__(self.safe_message)


class InvalidEmbeddingError(EmbeddingServiceError):
    code = ERROR_INVALID_EMBEDDING
    default_message = "The embedding model returned an invalid vector"


class EmbeddingService:
    """Lazy, reusable, local embedding model wrapper."""

    def __init__(
        self,
        *,
        model_name: str,
        model_version: str = "main",
        dimension: int = 384,
        batch_size: int = 32,
        device: str = "cpu",
    ) -> None:
        self.model_name = model_name
        self.model_version = model_version
        self.dimension = dimension
        self.batch_size = batch_size
        self.device = device
        self._model = None

    @property
    def model_label(self) -> str:
        return f"{self.model_name}@{self.model_version}"

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            # Imported lazily so unit tests that inject a fake embedding service
            # never pay the torch/sentence-transformers import or model cost.
            from sentence_transformers import SentenceTransformer

            logger.info(
                "Loading embedding model name=%s version=%s device=%s",
                self.model_name,
                self.model_version,
                self.device,
            )
            self._model = SentenceTransformer(
                self.model_name,
                device=self.device,
                revision=self.model_version,
            )
            return self._model
        except Exception as exc:
            logger.exception("Failed to load embedding model %s", self.model_name)
            raise EmbeddingServiceError() from exc

    @property
    def tokenizer(self):
        """Tokenizer compatible with the selected embedding model."""
        return self._load_model().tokenizer

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def generate_embedding(self, text: str) -> list[float]:
        return self.generate_embeddings([text])[0]

    def generate_embeddings(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        model = self._load_model()
        try:
            vectors = model.encode(
                list(texts),
                batch_size=self.batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            logger.exception(
                "Embedding generation failed model=%s batch_size=%s",
                self.model_label,
                len(texts),
            )
            raise EmbeddingServiceError() from exc

        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.ndim != 2 or matrix.shape[0] != len(texts):
            raise InvalidEmbeddingError("Embedding batch has an unexpected shape")
        if matrix.shape[1] != self.dimension:
            raise InvalidEmbeddingError(
                f"Embedding dimension {matrix.shape[1]} does not match "
                f"the configured {self.dimension}"
            )
        if not np.isfinite(matrix).all():
            raise InvalidEmbeddingError("Embedding contains NaN or infinite values")

        return [[float(value) for value in row] for row in matrix]


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    settings = get_settings()
    return EmbeddingService(
        model_name=settings.embedding_model_name,
        model_version=settings.embedding_model_version,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
        device=settings.embedding_device,
    )
