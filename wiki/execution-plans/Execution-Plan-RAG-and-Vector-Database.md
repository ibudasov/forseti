Execution plan for the <b>Weeks 5–6 — RAG + vector database</b> scope of [Forseti](Roadmap). Written to be handed to a coding agent as the single source of truth for implementation.

All code must follow [Coding Agent Clean Code Instructions](Coding-Agent-Clean-Code-Instructions).

---

## 1. Summary for the Product Owner

> 🎯 <b>What we are delivering:</b> a "reading comprehension" layer for the trade analyst. The system learns to ingest earnings calls, SEC filings (10-K/10-Q), and news, index them by <i>meaning</i> in a vector database, and use them to produce evidence-backed explanations for every recommendation — including clear justifications for <b>No trade</b> decisions.

<b>Why it matters:</b>

- Up to Week 4 the engine decides purely on numbers (price, RSI, fundamentals). This phase adds the <i>narrative</i>: bullish drivers, bearish risks, catalysts, red flags.
- It is the highest-priority gap area and the most demo-able AI capability of the project.
- Every claim in the AI summary is grounded in a retrieved source document — no free-floating hallucinated theses.

<b>Hard product rule (non-negotiable):</b>

- The RAG layer <b>explains</b> the thesis. It <b>never</b> calculates or modifies entry range, stop-loss, take-profit, or position size. All money math stays in the deterministic risk engine.

<b>Tech choices:</b>

- Embeddings stored in <b>pgvector</b> on our existing Postgres first (no new infrastructure).
- <b>Vertex AI Vector Search</b> only as an optional V3 upgrade — explicitly out of scope now.
- LLM: <b>Vertex AI (Gemini)</b> for summarization and synthesis.

<b>Exit criteria (what "done" means):</b>

- Given a ticker, the app retrieves relevant evidence documents.
- The app generates a grounded summary (thesis, risks, catalysts, red flags) traceable to sources.
- A <b>No trade</b> recommendation can be justified with retrieved evidence.
- Everything above is covered by automated tests and reproducible locally in Docker.

<b>Main risks:</b> stale/poor-quality source documents (mitigated by ingestion timestamps + freshness checks), token cost growth (tracked in Week 9 observability), scope creep toward letting the LLM touch risk numbers (blocked by the hard rule above and enforced by code review + tests).

---

## 2. Scope

### In scope

- Document ingestion pipeline for: earnings-call summaries, 10-K/10-Q business sections, 10-K/10-Q risk sections, recent company news, sector news.
- Chunking + embedding + storage in Postgres with `pgvector`.
- Retrieval service: given a ticker and a question, return top-k relevant chunks with source metadata.
- Grounded synthesis: LLM-generated evidence summary per ticker answering the five retrieval questions (§5).
- API surface: extend `POST /analyze` response with an `evidence` section; add `GET /ticker/{symbol}/evidence`.
- Database migrations for new tables.
- Unit + integration tests.

### Out of scope (deferred)

- Vertex AI Vector Search implementation (V3).
- LangGraph orchestration (Weeks 7–8) — the RAG layer must be usable as a plain service <i>before</i> it becomes a graph node.
- Letting retrieved evidence change stop-loss/take-profit/position size (never).
- Real-time streaming ingestion; batch/scheduled refresh is enough.
- UI work beyond what the existing API/Streamlit already exposes.

---

## 3. Prerequisites

- Weeks 1–4 complete: FastAPI app, Postgres, `POST /analyze`, structured data (OHLCV, fundamentals, earnings dates) — see [Execution Plan: Structured Data Ingestion](Execution-Plan-Structured-Data-Ingestion) and [Execution Plan: Evaluation Engine](Execution-Plan-Evaluation-Engine).
- `pgvector` extension available on the Postgres instance (local Docker + target Cloud SQL).
- Access to Vertex AI (Gemini) credentials via Secret Manager / env config.
- Document sources selected (see §4); API keys for news source if needed.

---

## 4. Data sources to index

| Source | Content | Refresh cadence |
| --- | --- | --- |
| SEC EDGAR | 10-K / 10-Q: "Business" and "Risk Factors" sections | On new filing |
| Earnings calls | Summaries/transcripts per quarter | Quarterly |
| Company news | Recent articles for tracked tickers | Daily batch |
| Sector news | AI, defence, nuclear, green energy, quantum, robotics, space | Daily batch |

Every stored chunk must carry: `ticker`, `source_type` (filing\_business / filing\_risk / earnings\_call / company\_news / sector\_news), `source_url`, `published_at`, `ingested_at`, `text`, `embedding`.

---

## 5. Retrieval questions (contract)

The synthesis step must answer, with citations to chunks:

1. What are the main bullish drivers?
2. What are the main bearish risks?
3. What near-term catalysts matter?
4. Does recent news support or weaken the setup?
5. Are there major red flags not visible in price action alone?

---

## 6. Implementation phases (agent task breakdown)

### Phase 1 — Schema & pgvector setup

- [ ] Migration: enable `vector` extension.
- [ ] Table `document_chunks` with columns per §4 + `embedding vector(<dim>)`.
- [ ] SQLModel/SQLAlchemy model + repository layer (create, bulk insert, similarity search).
- [ ] Config: embedding dimension, model name, chunk size, overlap — all env-driven.

- <b>Tests:</b> repository CRUD + a similarity query on seeded vectors.

### Phase 2 — Ingestion pipeline

- [ ] `ingestion/` module with one ingestor per source type (§4), behind a common `Ingestor` protocol.
- [ ] SEC EDGAR ingestor: fetch filing, extract Business + Risk Factors sections.
- [ ] News ingestor(s): company + sector news.
- [ ] Earnings-call ingestor (start with summaries if full transcripts unavailable).
- [ ] Chunking strategy: fixed-size with overlap (default \~800 tokens / 100 overlap), sentence-boundary aware; keep section metadata.
- [ ] Embedding client (Vertex AI) with batching + retry/backoff.
- [ ] Idempotency: re-ingesting the same document updates, never duplicates (unique constraint on source hash).

- <b>Tests:</b> fixture-based parsing tests for each ingestor; chunking edge cases; idempotency test.

### Phase 3 — Retrieval service

- [ ] `retrieval/` module: `retrieve(ticker, question, top_k)` → ranked chunks with scores + metadata.
- [ ] Filters: by `source_type`, date window; fall back to sector-level chunks when ticker-level evidence is thin.
- [ ] Deterministic behavior: same query + same corpus → same results (fixed ordering by score, tie-break by date then id).

- <b>Tests:</b> golden retrieval cases over a seeded mini-corpus (clear hit, no hit, filter behavior).

### Phase 4 — Grounded synthesis (LLM)

- [ ] `synthesis/` module: builds the prompt from retrieved chunks (with source labels), calls Gemini, parses structured output.
- [ ] Output schema (Pydantic): `bullish_drivers[]`, `bearish_risks[]`, `catalysts[]`, `news_alignment` (supports / weakens / neutral), `red_flags[]`, each item with `chunk_ids` citations.
- [ ] Guardrails: refuse to output anything resembling entry/stop/target/size; system prompt states the hard rule; post-validation rejects numeric trade parameters in output.
- [ ] Token/latency logging hooks (feeds Week 9 observability).

- <b>Tests:</b> prompt-construction tests; output-schema validation; guardrail rejection tests; mocked-LLM integration test.

### Phase 5 — API integration

- [ ] `GET /ticker/{symbol}/evidence` → retrieval + synthesis result for that ticker.
- [ ] Extend `POST /analyze` response: new `evidence` block populated from synthesis; decision logic unchanged.
- [ ] Missing/insufficient evidence → `evidence.status: "insufficient_data"` and a confidence downgrade flag for the decision layer (flag only — decision stays deterministic).

- <b>Tests:</b> API contract tests; end-to-end happy path with seeded corpus + mocked LLM.

### Phase 6 — Hardening & docs

- [ ] Refresh job entrypoint (CLI/command) to re-run ingestion on a schedule (Cloud Scheduler target later).
- [ ] README/architecture doc update: dataflow from ingestion → embedding → retrieval → synthesis → API.
- [ ] ADR (short) recording: why pgvector first; why RAG never touches risk math.
- [ ] Golden-case regression fixtures for 2–3 tickers (used by Week 9 evaluation).

---

## 7. Definition of done

- [ ] All phase checkboxes complete; CI green (unit + integration).
- [ ] `POST /analyze` for a seeded ticker returns evidence-backed reasons traceable to stored chunks.
- [ ] A no-trade case returns an evidence-justified explanation.
- [ ] No code path allows synthesis output to influence stop-loss, take-profit, or position size (covered by test + review).
- [ ] Docs updated; ADR merged.

## 8. Explicit non-goals for the agent

- Do not introduce LangGraph yet.
- Do not add Vertex AI Vector Search.
- Do not modify the deterministic rules engine or risk math modules.
- Do not add broker/trading functionality.

## 9. References

- Roadmap: [Forseti](Roadmap) (section <i>Weeks 5–6 — RAG + vector database</i>)
- Style guide: [Coding Agent Clean Code Instructions](Coding-Agent-Clean-Code-Instructions)
- Repo: [https://github.com/ibudasov/forseti](https://github.com/ibudasov/forseti)

---

_Migrated from Notion on 2026-11-12._
