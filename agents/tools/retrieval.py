"""Retriever tool: calls the RAG retrieval service for evidence questions.

Wraps `app.rag.retrieval.retrieve`. Retrieval only: it never synthesizes or
interprets the retrieved chunks, that is the analysts' job.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, List, Optional

from pydantic import BaseModel, Field

from app.rag.retrieval import retrieve

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from app.rag.embedding import EmbeddingClient

DEFAULT_TOP_K = 5

# The five standing evidence questions every analysis run must gather
# evidence for, per the agentic workflow execution plan.
EVIDENCE_QUESTIONS = (
    "What is the company's recent revenue and earnings growth trajectory?",
    "What is the company's cash flow and debt position?",
    "What recent news or events could affect this ticker's price?",
    "What is the sector/industry outlook for this ticker?",
    "Are there any risks or red flags analysts have raised recently?",
)


class RetrievedChunk(BaseModel):
    """A single evidence chunk returned by the retrieval service."""

    chunk_id: int = Field(description="Identifier of the source document chunk.")
    source_type: str = Field(description="Type of the source document.")
    content: str = Field(description="Text content of the chunk.")


class RetrieveEvidenceOutput(BaseModel):
    """Evidence chunks retrieved for a single question."""

    question: str
    chunks: List[RetrievedChunk]


RetrieveEvidence = Callable[[str, str], RetrieveEvidenceOutput]


def build_retriever_tool(
    embedding_client: "EmbeddingClient",
    engine: Optional["Engine"] = None,
    top_k: int = DEFAULT_TOP_K,
) -> RetrieveEvidence:
    """Create a `retrieve_evidence` tool bound to the given collaborators."""

    def retrieve_evidence(ticker: str, question: str) -> RetrieveEvidenceOutput:
        """Retrieve the top-k evidence chunks for a ticker and evidence question.

        Delegates to the existing pgvector similarity search; performs no
        interpretation or summarization of the retrieved text.
        """
        chunks = retrieve(
            ticker=ticker,
            question=question,
            embedding_client=embedding_client,
            top_k=top_k,
            engine=engine,
        )
        return RetrieveEvidenceOutput(
            question=question,
            chunks=[
                RetrievedChunk(
                    chunk_id=chunk.id,
                    source_type=str(chunk.source_type),
                    content=chunk.text,
                )
                for chunk in chunks
            ],
        )

    return retrieve_evidence
