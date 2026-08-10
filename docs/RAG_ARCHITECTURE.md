# RAG + Vector Database Architecture

## Overview

The RAG (Retrieval-Augmented Generation) layer provides evidence-backed explanations for trading recommendations. It works independently of the deterministic rules engine and never influences trade math (entry range, stop-loss, take-profit, position size).

## Architecture Layers

```
┌─────────────────────────────────────────────────────────┐
│                    API Layer                            │
│  GET /ticker/{symbol}/evidence                          │
│  POST /analyze (extended with evidence block)           │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│              Synthesis Layer                             │
│  • Vertex AI Gemini integration (future)                │
│  • Output schema with citations                         │
│  • Guardrails enforcement (no money math)               │
│  • Evidence categorization (bullish/bearish/etc)        │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│              Retrieval Layer                             │
│  • Vector similarity search (pgvector)                  │
│  • Source type filtering                                │
│  • Date window filtering                                │
│  • Deterministic ranking (recency-first)                │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│            Document Chunk Storage                        │
│  • PostgreSQL with pgvector extension                   │
│  • Idempotent ingestion via source_hash                 │
│  • Metadata: ticker, source_type, published_at, etc     │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│              Ingestion Layer                             │
│  • Source-specific ingestors (earnings, news, filings)  │
│  • Chunking with token-based splitting                  │
│  • Batch processing with error handling                 │
│  • Future: Vertex AI embedding integration              │
└─────────────────────────────────────────────────────────┘
```

## Module Structure

### `app/db/models.py`
- **DocumentChunk**: SQLModel representing a single text chunk with embeddings
- **DocumentSourceType**: Enum (filing_business, filing_risk, earnings_call, company_news, sector_news)

### `app/db/repository.py`
- **create_document_chunk()**: Insert single chunk
- **bulk_create_document_chunks()**: Batch insert with idempotency
- **get_document_chunks_by_ticker()**: Retrieve chunks with optional source type filter
- **delete_document_chunks_by_source_hash()**: Remove chunks by source hash

### `app/ingestion/rag.py`
- **ChunkingConfig**: Configuration for text chunking (chunk_size, overlap, sentence awareness)
- **Ingestor**: Abstract base class for document ingestors
- **BasicIngestor**: Generic ingestor that chunks text into DocumentChunk objects
- Specialized ingestors for each source type

### `app/ingestion/ingestion_manager.py`
- **IngestionManager**: Orchestrates ingestion, chunking, and storage
- **ingest_document()**: Single document ingestion with error handling
- **ingest_documents_batch()**: Batch ingestion of multiple documents

### `app/services/retrieval.py`
- **RetrievalResult**: Structured result from similarity search
- **retrieve()**: Main retrieval function with filtering and ordering
- **retrieve_by_source_type()**: Convenience function for single source type

### `app/services/synthesis.py`
- **EvidenceItem**: Single piece of evidence with source citation
- **EvidenceSynthesis**: Structured output with multiple evidence categories
- **synthesize_evidence()**: Generate evidence from retrieval results
- **synthesize_no_trade_evidence()**: Specialized synthesis for no-trade recommendations
- **_check_guardrails()**: Enforce hard constraint (no money math in synthesis)

### `app/schemas/evidence.py`
- **EvidenceItemSchema**: Pydantic schema for API serialization
- **EvidenceBlockSchema**: Complete evidence block with all categories
- **TickerEvidenceResponse**: Response model for GET /ticker/{symbol}/evidence

## Data Flow

### Ingestion Pipeline
```
Source Document (earnings call, news, filing)
    ↓
[Ingestor selects chunking strategy]
    ↓
[Text split by token count with overlap]
    ↓
[Hash generated for idempotency]
    ↓
[DocumentChunk objects created with metadata]
    ↓
[Stored in PostgreSQL with pgvector]
    ↓
[Embedding added asynchronously (future)]
```

### Retrieval & Synthesis Pipeline
```
Ticker + Optional Question
    ↓
[Retrieve recent chunks by source type]
    ↓
[Order by recency, then deterministically]
    ↓
[RetrievalResult objects returned]
    ↓
[Synthesize evidence from chunks]
    ↓
[Categorize into bullish/bearish/catalysts/etc]
    ↓
[Apply guardrails (block money math)]
    ↓
[Return EvidenceSynthesis with citations]
    ↓
[API serializes to JSON response]
```

## Key Design Decisions

### 1. **Idempotent Ingestion**
- Each chunk gets a deterministic `source_hash` computed from content + URL
- Unique constraint on source_hash prevents duplicates
- Re-ingesting same document creates no duplicates

### 2. **Recency-First Retrieval**
- Chunks ordered by `ingested_at DESC`, then `id DESC`
- Recent information takes precedence
- Deterministic ordering ensures consistent results

### 3. **Guardrails Before Synthesis**
- Hard constraint: evidence never contains numeric trade parameters
- Validated at synthesis time, before API response
- Helps prevent LLM drift into forbidden money math

### 4. **Token-Based Chunking**
- Simple heuristic: ~4 characters per token
- Configurable chunk_size and overlap
- Sentence-boundary awareness (future enhancement)

### 5. **pgvector First**
- Vector embeddings stored as JSON strings initially
- pgvector extension enables future similarity search
- Defers Vertex AI Vector Search to V3

## Configuration

Environment variables (see .env.example):
- `EMBEDDING_MODEL_NAME`: Model for embeddings (default: text-embedding-004)
- `EMBEDDING_DIMENSION`: Vector dimension (default: 768)
- `CHUNK_SIZE_TOKENS`: Tokens per chunk (default: 800)
- `CHUNK_OVERLAP_TOKENS`: Overlap between chunks (default: 100)
- `VERTEX_AI_PROJECT`: GCP project ID (optional, for future Vertex AI integration)
- `VERTEX_AI_LOCATION`: GCP location (default: us-central1)

## Testing

### Unit Tests (`tests/test_rag.py`)
- Repository CRUD operations
- Chunk retrieval with filters
- Evidence synthesis and guardrails
- Idempotency verification

### Ingestion Tests (`tests/ingestion/test_rag_ingestion.py`)
- Text chunking strategies
- Hash determinism
- All ingestor implementations
- Batch ingestion error handling

### Integration Tests (future)
- Full pipeline: ingest → retrieve → synthesize → API
- Mocked Vertex AI for synthesis
- Database transaction rollback

## Security & Constraints

### Hard Rule Enforcement
- **No trade parameters in synthesis**: Guardrails reject any mention of entry, stop-loss, take-profit, or position size with numbers
- **No LLM drift**: Synthesis output schema prevents numeric fields for risk parameters
- **Test coverage**: Dedicated test ensures rule cannot be violated

### Data Integrity
- `source_hash` unique constraint prevents duplicate ingestion
- `published_at` optional (allows news without publication date)
- `ingested_at` auto-timestamped for freshness tracking
- All chunks tagged with ticker for multi-tenant safety

## Future Enhancements

1. **Vector Similarity Search**: Replace recency-only with semantic similarity using pgvector
2. **Vertex AI Integration**: Call Gemini for synthesis instead of heuristic categorization
3. **Semantic Chunking**: Use sentence boundaries + NLP for smarter splits
4. **Freshness Strategy**: Archive chunks older than N days; alert on stale evidence
5. **LLM Observability**: Track tokens, latency, cost per synthesis (Week 9)
6. **Multi-language Support**: Ingest & synthesize news in multiple languages
