# ADR-001: pgvector-First Storage for Evidence Embeddings

## Status
Accepted

## Context
We need to store text embeddings for semantic similarity search in the RAG layer. We have two primary options:
1. **pgvector**: PostgreSQL extension for vector storage and similarity search
2. **Vertex AI Vector Search**: Google Cloud managed vector database

## Decision
**Use pgvector first (Weeks 5-6), defer Vertex AI Vector Search to V3.**

## Rationale

### Advantages of pgvector-first
1. **No additional infrastructure**: Uses existing PostgreSQL database
2. **Data co-location**: Chunks and embeddings in same database, simplifies queries
3. **Transaction safety**: ACID guarantees for chunk + embedding atomicity
4. **Cost-effective**: Leverages existing database infrastructure
5. **Iterative development**: Can develop retrieval without cloud dependencies
6. **Deterministic results**: Full control over ranking and filtering logic

### Why not Vertex AI Vector Search immediately
1. **Complexity**: Requires GCP setup, authentication, different API paradigm
2. **Latency**: Adds external service call for every retrieval
3. **Cost**: Separate billing for vector search service
4. **Operational burden**: Manage two different data stores
5. **Development velocity**: Faster to iterate with embedded solution

## Timeline
- **Weeks 5-6 (V2)**: pgvector with PostgreSQL
- **Week 7-8**: If performance bottleneck identified, evaluate Vertex AI migration
- **V3**: Migrate to Vertex AI Vector Search for scale

## Implementation Details

### Storage
- `DocumentChunk.embedding` field stores vector as JSON array string
- pgvector extension enables native vector type in future
- Migration 0002 creates extension, Migration 0003 adds chunk table

### Retrieval Strategy
- Initial: Keyword + recency-based ranking (no vector search)
- Phase 2: Implement pgvector similarity search
- Phase 3: Add hybrid search (keyword + vector)
- V3: Migrate to Vertex AI Vector Search API

### Configuration
```python
# Environment variables for future parameterization
EMBEDDING_MODEL_NAME=text-embedding-004
EMBEDDING_DIMENSION=768
VERTEX_AI_PROJECT=  # Optional, for future migration
```

## Consequences

### Positive
- ✓ Minimal external dependencies
- ✓ Fast iteration and debugging
- ✓ ACID compliance for data integrity
- ✓ Easy to test locally without cloud setup
- ✓ Familiar PostgreSQL operations

### Negative
- ✗ Limited scale compared to managed vector DB
- ✗ May need optimization tuning for large datasets
- ✗ Migration complexity when scaling to Vertex AI
- ✗ No built-in replication across regions

### Mitigation
- Index on ticker + source_type for common queries
- Set chunk size limits to control dataset growth
- Monitor query performance in metrics (Week 9)
- Plan migration strategy for V3 milestone

## Alternatives Considered

### A. Vector Search in application layer
- Rejected: No semantic similarity without embeddings

### B. Vertex AI Vector Search from day 1
- Rejected: Over-engineered for Weeks 5-6; adds deployment complexity

### C. Separate NoSQL DB for vectors
- Rejected: Operational burden; pgvector simpler

## Related Decisions
- **ADR-002**: RAG synthesis output format and guardrails
- Follows principle of "simplest thing that works" for fast iteration

## Review Date
Review after 1000 chunks ingested or Q3 2026 performance metrics
