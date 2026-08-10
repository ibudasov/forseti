"""Ingestion pipeline for RAG - handles document ingestion and chunking."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from app.db.models import DocumentChunk, DocumentSourceType


@dataclass
class ChunkingConfig:
    """Configuration for document chunking."""
    chunk_size_tokens: int = 800
    overlap_tokens: int = 100
    sentence_boundary_aware: bool = True


def _estimate_tokens(text: str) -> int:
    """
    Estimate token count for text.
    
    Simple heuristic: roughly 1 token per 4 characters.
    In production, would use actual tokenizer.
    """
    return max(1, len(text) // 4)


def chunk_text(
    text: str,
    config: Optional[ChunkingConfig] = None,
) -> List[str]:
    """
    Split text into chunks with optional overlap and sentence awareness.
    
    Args:
        text: The text to chunk
        config: Chunking configuration
        
    Returns:
        List of text chunks
    """
    if config is None:
        config = ChunkingConfig()
    
    # For now, simple split by token estimate
    # In production, would use sentence-boundary awareness
    chunks = []
    words = text.split()
    current_chunk = []
    current_tokens = 0
    
    for word in words:
        word_tokens = _estimate_tokens(word)
        
        if current_tokens + word_tokens > config.chunk_size_tokens and current_chunk:
            # Save current chunk
            chunk_text = " ".join(current_chunk)
            chunks.append(chunk_text)
            
            # Start new chunk with overlap
            overlap_words = max(1, len(current_chunk) // 4)
            current_chunk = current_chunk[-overlap_words:] + [word]
            current_tokens = sum(_estimate_tokens(w) for w in current_chunk)
        else:
            current_chunk.append(word)
            current_tokens += word_tokens
    
    # Add final chunk
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    
    return chunks if chunks else [text]


def _hash_content(text: str, source_url: str) -> str:
    """Generate deterministic hash of content for deduplication."""
    content = f"{text}:{source_url}".encode()
    return hashlib.sha256(content).hexdigest()


class IngestorResult:
    """Result of ingestion operation."""
    
    def __init__(
        self,
        ticker: str,
        source_type: DocumentSourceType,
        chunks_created: int,
        chunks_skipped: int,
        error: Optional[str] = None,
    ):
        self.ticker = ticker
        self.source_type = source_type
        self.chunks_created = chunks_created
        self.chunks_skipped = chunks_skipped
        self.error = error
        self.success = error is None

    def __repr__(self) -> str:
        return (
            f"IngestorResult(ticker={self.ticker}, source_type={self.source_type}, "
            f"created={self.chunks_created}, skipped={self.chunks_skipped})"
        )


class Ingestor(ABC):
    """Abstract base class for document ingestors."""
    
    @property
    @abstractmethod
    def source_type(self) -> DocumentSourceType:
        """The source type this ingestor handles."""
        pass
    
    @abstractmethod
    async def ingest(
        self,
        ticker: str,
        content: str,
        source_url: str,
        published_at: Optional[datetime] = None,
        config: Optional[ChunkingConfig] = None,
    ) -> List[DocumentChunk]:
        """
        Ingest a document into chunks.
        
        Args:
            ticker: The stock ticker
            content: The full document content
            source_url: The URL/source of the document
            published_at: Optional publication date
            config: Optional chunking configuration
            
        Returns:
            List of DocumentChunk objects ready to store
        """
        pass


class BasicIngestor(Ingestor):
    """Basic ingestor that chunks text and creates document chunks."""
    
    def __init__(self, source_type: DocumentSourceType):
        self._source_type = source_type
    
    @property
    def source_type(self) -> DocumentSourceType:
        return self._source_type
    
    async def ingest(
        self,
        ticker: str,
        content: str,
        source_url: str,
        published_at: Optional[datetime] = None,
        config: Optional[ChunkingConfig] = None,
    ) -> List[DocumentChunk]:
        """Basic ingestion with text chunking."""
        if config is None:
            config = ChunkingConfig()
        
        chunks = chunk_text(content, config)
        chunk_size_estimate = len(content) // max(1, len(chunks))
        
        document_chunks = []
        for chunk_index, chunk_text_content in enumerate(chunks):
            source_hash = _hash_content(chunk_text_content, source_url)
            
            chunk = DocumentChunk(
                ticker=ticker.strip().upper(),
                source_type=self.source_type,
                source_url=source_url,
                published_at=published_at,
                ingested_at=datetime.now(timezone.utc),
                text=chunk_text_content,
                chunk_size=chunk_size_estimate,
                chunk_index=chunk_index,
                source_hash=source_hash,
                chunk_metadata={
                    "content_length": len(chunk_text_content),
                    "chunk_count": len(chunks),
                },
            )
            document_chunks.append(chunk)
        
        return document_chunks


class EarningsCallIngestor(BasicIngestor):
    """Ingestor for earnings call transcripts/summaries."""
    
    def __init__(self):
        super().__init__(DocumentSourceType.earnings_call)


class CompanyNewsIngestor(BasicIngestor):
    """Ingestor for company news articles."""
    
    def __init__(self):
        super().__init__(DocumentSourceType.company_news)


class SectorNewsIngestor(BasicIngestor):
    """Ingestor for sector news articles."""
    
    def __init__(self):
        super().__init__(DocumentSourceType.sector_news)


class FilingBusinessIngestor(BasicIngestor):
    """Ingestor for SEC 10-K/10-Q Business sections."""
    
    def __init__(self):
        super().__init__(DocumentSourceType.filing_business)


class FilingRiskIngestor(BasicIngestor):
    """Ingestor for SEC 10-K/10-Q Risk Factors sections."""
    
    def __init__(self):
        super().__init__(DocumentSourceType.filing_risk)
