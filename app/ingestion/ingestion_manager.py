"""Ingestion manager for RAG - orchestrates chunking and storage."""
from __future__ import annotations

from typing import List, Optional

from app.db.repository import bulk_create_document_chunks
from app.db.models import DocumentChunk, DocumentSourceType
from app.ingestion.rag import (
    Ingestor,
    ChunkingConfig,
    IngestorResult,
    BasicIngestor,
    EarningsCallIngestor,
    CompanyNewsIngestor,
    SectorNewsIngestor,
    FilingBusinessIngestor,
    FilingRiskIngestor,
)
from datetime import datetime


class IngestionManager:
    """Manages document ingestion, chunking, and storage."""
    
    def __init__(self, chunking_config: Optional[ChunkingConfig] = None):
        self.config = chunking_config or ChunkingConfig()
        self.ingestors = {
            DocumentSourceType.earnings_call: EarningsCallIngestor(),
            DocumentSourceType.company_news: CompanyNewsIngestor(),
            DocumentSourceType.sector_news: SectorNewsIngestor(),
            DocumentSourceType.filing_business: FilingBusinessIngestor(),
            DocumentSourceType.filing_risk: FilingRiskIngestor(),
        }
    
    async def ingest_document(
        self,
        ticker: str,
        source_type: DocumentSourceType,
        content: str,
        source_url: str,
        published_at: Optional[datetime] = None,
        engine=None,
    ) -> IngestorResult:
        """
        Ingest a single document.
        
        Args:
            ticker: Stock ticker
            source_type: Type of source
            content: Document content
            source_url: URL/location of document
            published_at: Optional publication date
            engine: Database engine
            
        Returns:
            IngestorResult with chunk counts and status
        """
        try:
            ingestor = self.ingestors.get(source_type)
            if not ingestor:
                return IngestorResult(
                    ticker=ticker,
                    source_type=source_type,
                    chunks_created=0,
                    chunks_skipped=0,
                    error=f"No ingestor for source type: {source_type}",
                )
            
            # Chunk the document
            chunks = await ingestor.ingest(
                ticker=ticker,
                content=content,
                source_url=source_url,
                published_at=published_at,
                config=self.config,
            )
            
            # Store chunks (with idempotency)
            created = bulk_create_document_chunks(chunks, engine=engine)
            skipped = len(chunks) - created
            
            return IngestorResult(
                ticker=ticker,
                source_type=source_type,
                chunks_created=created,
                chunks_skipped=skipped,
            )
        
        except Exception as e:
            return IngestorResult(
                ticker=ticker,
                source_type=source_type,
                chunks_created=0,
                chunks_skipped=0,
                error=str(e),
            )
    
    async def ingest_documents_batch(
        self,
        documents: List[dict],
        engine=None,
    ) -> List[IngestorResult]:
        """
        Ingest multiple documents.
        
        Each document dict should have:
        - ticker: str
        - source_type: DocumentSourceType
        - content: str
        - source_url: str
        - published_at: Optional[datetime]
        
        Args:
            documents: List of document dicts
            engine: Database engine
            
        Returns:
            List of IngestorResult for each document
        """
        results = []
        for doc in documents:
            result = await self.ingest_document(
                ticker=doc["ticker"],
                source_type=doc["source_type"],
                content=doc["content"],
                source_url=doc["source_url"],
                published_at=doc.get("published_at"),
                engine=engine,
            )
            results.append(result)
        
        return results
