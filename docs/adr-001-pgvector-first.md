# ADR 001 — pgvector-first for RAG embeddings

**Status:** Accepted  
**Date:** 2026-08-10

## Context

The Forseti AI swing-trade analyst needs a vector store to support RAG (Retrieval-Augmented Generation). We evaluated:

1. **pgvector in existing Postgres** — uses the same database already present, zero new infrastructure.
2. **Vertex AI Vector Search** — managed, scalable, but requires additional GCP setup and cost.
3. **Pinecone / Weaviate** — third-party managed services, additional vendor dependency.

## Decision

Use **pgvector** (the `vector` Postgres extension) as the first-generation vector store, embedded in the existing Postgres instance.

The `document_chunk` table stores embeddings as `vector(768)` columns. Similarity search uses the HNSW index with cosine distance (`vector_cosine_ops`).

## Consequences

**Good:**
- Zero new infrastructure — Postgres is already running.
- Atomic transactions spanning chunks and other domain tables.
- No additional credential management or billing account.
- Easily testable via `pgvector/pgvector:pg15` container image.

**Accepted:**
- Does not scale to hundreds of millions of vectors; Vertex AI Vector Search (V3) will be adopted when needed.
- Similarity search latency is higher than a dedicated ANN service for large corpora.

## Upgrade path

When corpus size requires it: migrate to **Vertex AI Vector Search** (deferred to V3 roadmap). The `retrieval.py` interface is stable — only the repository layer changes.
