"""Evidence service: orchestrates retrieval + synthesis for a ticker."""
from __future__ import annotations

import logging
from typing import List, Optional

from app.db.models import DocumentChunk
from app.rag.embedding import EmbeddingClient, MockEmbeddingClient
from app.rag.retrieval import retrieve
from app.rag.synthesis import SynthesisOutput, synthesize
from app.settings import get_settings

logger = logging.getLogger(__name__)

_RETRIEVAL_QUESTIONS = [
    "What are the main bullish drivers?",
    "What are the main bearish risks?",
    "What near-term catalysts matter?",
    "Does recent news support or weaken the setup?",
    "Are there major red flags not visible in price action alone?",
]


def build_evidence(
    ticker: str,
    embedding_client: Optional[EmbeddingClient] = None,
    engine=None,
) -> SynthesisOutput:
    """Retrieve evidence chunks for all retrieval questions and synthesise.

    When *embedding_client* is not provided, the application settings are used
    to construct a Vertex AI client.  If the project is not configured, a
    :class:`~app.rag.embedding.MockEmbeddingClient` is used (returns zero
    vectors — useful for local development and tests).
    """
    settings = get_settings()

    if embedding_client is None:
        if settings.VERTEX_AI_PROJECT:
            from app.rag.embedding import VertexAIEmbeddingClient

            embedding_client = VertexAIEmbeddingClient(
                project=settings.VERTEX_AI_PROJECT,
                location=settings.VERTEX_AI_LOCATION,
                model=settings.EMBEDDING_MODEL,
                dimension=settings.EMBEDDING_DIM,
            )
        else:
            logger.info("VERTEX_AI_PROJECT not set — using MockEmbeddingClient for %s", ticker)
            embedding_client = MockEmbeddingClient(dimension=settings.EMBEDDING_DIM)

    all_chunks: List[DocumentChunk] = []
    seen_ids: set[int] = set()
    for question in _RETRIEVAL_QUESTIONS:
        chunks = retrieve(
            ticker=ticker,
            question=question,
            embedding_client=embedding_client,
            top_k=5,
            engine=engine,
        )
        for chunk in chunks:
            if chunk.id not in seen_ids:
                all_chunks.append(chunk)
                seen_ids.add(chunk.id)

    return synthesize(
        ticker=ticker,
        chunks=all_chunks,
        gemini_model=settings.GEMINI_MODEL,
        vertex_project=settings.VERTEX_AI_PROJECT,
        vertex_location=settings.VERTEX_AI_LOCATION,
    )
