"""Vertex AI embedding client with batching and retry/back-off."""
from __future__ import annotations

import logging
import time
from typing import List, Protocol

logger = logging.getLogger(__name__)

_BATCH_SIZE = 20
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0


class EmbeddingClient(Protocol):
    """Protocol for embedding clients."""

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Return one embedding vector per input text."""
        ...


class VertexAIEmbeddingClient:
    """Calls Vertex AI text-embedding model with batching and exponential back-off."""

    def __init__(self, project: str, location: str, model: str, dimension: int = 768) -> None:
        from google.cloud import aiplatform
        from vertexai.language_models import TextEmbeddingModel

        aiplatform.init(project=project, location=location)
        self._model = TextEmbeddingModel.from_pretrained(model)
        self._dimension = dimension

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        results: List[List[float]] = []
        for batch_start in range(0, len(texts), _BATCH_SIZE):
            batch = texts[batch_start:batch_start + _BATCH_SIZE]
            results.extend(self._embed_batch_with_retry(batch))
        return results

    def _embed_batch_with_retry(self, batch: List[str]) -> List[List[float]]:
        for attempt in range(_MAX_RETRIES):
            try:
                embeddings = self._model.get_embeddings(batch)
                return [e.values for e in embeddings]
            except Exception as exc:
                if attempt == _MAX_RETRIES - 1:
                    raise
                wait = _BACKOFF_BASE ** attempt
                logger.warning("Embedding attempt %d failed: %s — retrying in %.1fs", attempt + 1, exc, wait)
                time.sleep(wait)
        return []  # unreachable


class MockEmbeddingClient:
    """Deterministic mock for unit tests — returns fixed-length zero vectors."""

    def __init__(self, dimension: int = 768) -> None:
        self._dimension = dimension

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return [[0.0] * self._dimension for _ in texts]
