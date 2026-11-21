Execution plan for the <b>Weeks 7–8 — Agentic workflow with ADK</b> scope of [Forseti](Roadmap). Written to be handed to a coding agent as the single source of truth for implementation.

All code must follow [Coding Agent Clean Code Instructions](Coding-Agent-Clean-Code-Instructions).

GitHub issue: [ibudasov/forseti#17](https://github.com/ibudasov/forseti/issues/17) (foundation, closed by PR #18)

Follow-up issue for the remaining work: [ibudasov/forseti#20](https://github.com/ibudasov/forseti/issues/20) — runnable supervisor workflow, critic loop, tracing, plus validation gates (every `make` target runs cleanly, full test coverage).

---

## 1. Goal

Turn the existing linear pipeline (structured data → rules engine → RAG evidence → recommendation) into a <b>traceable multi-step agentic workflow</b> built on Google's <b>ADK (Agent Development Kit)</b>, not LangChain/LangGraph.

After this phase, every recommendation must carry a visible execution trace explaining which agent did what, which tools were called, and why the final decision was made.

### Why ADK

- Native fit with Vertex AI and Gemini models.
- First-class support for supervisor + specialist multi-agent topologies.
- Built-in tracing/evaluation hooks that map onto Cloud Observability.
- Deployable to Vertex AI Agent Runtime (managed) or Cloud Run (fallback).

### Hard product rules (non-negotiable)

> 📐 All money math stays deterministic. Agents may only <b>read</b> entry range, stop-loss, take-profit, position size, and risk/reward produced by the existing risk engine. No LLM may compute or modify them.

1. All money math stays deterministic (see above).
2. The existing deterministic rules engine and RAG service are not rewritten — they are wrapped as ADK tools.
3. The public API contract does not break: `POST /analyze` keeps its current response schema; the agentic trace is additive.
4. The decision label (`trade` / `watchlist` / `no_trade`) may never be upgraded by the LLM beyond what the deterministic rules allow. Agents may only downgrade confidence or downgrade the decision, never upgrade.

---

## 2. Scope

### In scope

- ADK dependency, project wiring, and configuration.
- Tool adapters wrapping existing deterministic services (data collector, rules engine, risk engine, retrieval, synthesis).
- Eight agents/nodes (see §4), orchestrated by an ADK root agent.
- Execution trace captured, persisted, and exposed via the API.
- Self-critique (Critic/Guardrail) step with explicit no-trade branch and confidence downgrade.
- Feature flag to run the pipeline in legacy (linear) or agentic mode.
- Local run + tests; Cloud Run deployability preserved.

### Out of scope (deferred)

- Vertex AI Agent Runtime deployment automation (V3; keep the code deployable and document the path).
- Managed Agents in the Gemini API variant (evaluate and write an ADR; do not implement).
- Gemini Enterprise / Agent Marketplace packaging.
- Vertex AI Vector Search migration.
- Any broker/trading execution functionality.
- New data sources or new indicators.

---

## 3. Prerequisites

- Weeks 1–4 complete: FastAPI app, Postgres, `POST /analyze`, structured data ingestion, deterministic rules + risk engine — see [Execution Plan: Structured Data Ingestion](Execution-Plan-Structured-Data-Ingestion) and [Execution Plan: Evaluation Engine](Execution-Plan-Evaluation-Engine).
- Weeks 5–6 complete: pgvector retrieval and grounded synthesis usable as plain services — see [Execution Plan: RAG + Vector Database (Weeks 5–6)](Execution-Plan-RAG-and-Vector-Database).
- Vertex AI (Gemini) credentials available via env / Secret Manager.
- `google-adk` installable and pinned in dependencies.

---

## 4. Agent topology

Root agent: <b>Trade Analyst Supervisor</b> (ADK root agent) orchestrating specialists as sub-agents/tools.

| Agent | Type | Responsibility | LLM? |
| --- | --- | --- | --- |
| 1. Input Resolver | deterministic tool-agent | Normalize ticker or platform reference into a validated Ticker | No |
| 2. Structured Data Collector | deterministic tool-agent | OHLCV, indicators, fundamentals, VIX, earnings dates; freshness/completeness | No |
| 3. Retriever | tool-agent | Retrieval service for the five evidence questions | No |
| 4. Fundamental Analyst | LLM specialist | Growth, cash flow, debt, quality — grounded in evidence | Yes |
| 5. Technical Analyst | LLM specialist | Trend, RSI, support/resistance, momentum | Yes |
| 6. Risk Manager | deterministic tool-agent | Existing risk engine: entry, stop, target, size, R/R | No |
| 7. Decision Synthesizer | LLM specialist | Final recommendation payload + human-readable memo | Yes |
| 8. Critic / Guardrail | LLM + deterministic validators | Contradictions, weak evidence, violated rules; can force no\_trade | Yes |

### Control flow

1. Input Resolver — fail fast on invalid input.
2. Structured Data Collector and Retriever run (parallel where ADK allows).
3. Fundamental Analyst and Technical Analyst run on their respective inputs.
4. Risk Manager runs the deterministic risk engine.
5. Decision Synthesizer produces a draft recommendation.
6. Critic reviews the draft; on rejection, one bounded revision loop (max 1 retry), then final output.
7. Incomplete data or insufficient evidence → explicit no-trade / watchlist branch with reasons.

---

## 5. Implementation phases (agent task breakdown)

### Phase 1 — ADK foundation

- [ ] Add and pin `google-adk`; document required env vars (project, location, model id).
- [ ] Create `agents/` package: `agents/tools/`, `agents/specialists/`, `agents/orchestration/`.
- [ ] Config object for model names, temperature, timeouts, max retries, and the `PIPELINE_MODE` flag (`linear` \| `agentic`, default `linear`).
- [ ] No domain/business logic in the ADK layer; it only orchestrates.

- <b>Tests:</b> config loading, flag switching, agent registry construction without network calls.

### Phase 2 — Tool adapters over deterministic services

- [ ] Adapters wrapping: ticker normalization, structured data collection, indicator computation, rules engine evaluation, risk engine calculation, retrieval, synthesis.
- [ ] Typed Pydantic input/output schema and a model-usable docstring per tool.
- [ ] Tools stay thin: no business rules, no math, no I/O beyond delegation.
- [ ] Explicit dependency injection; no global singletons.

- <b>Tests:</b> delegation with mocked services, schema validation, error mapping.

### Phase 3 — Specialist agents

- [ ] Implement agents 1–3 and 6 as deterministic tool-agents (no LLM calls).
- [ ] Implement Fundamental Analyst, Technical Analyst, Decision Synthesizer as ADK LLM agents with typed structured output.
- [ ] Every LLM prompt states the hard rules from §1 and requires citations to chunk ids / metric names.
- [ ] Post-validation rejects LLM output containing trade numbers absent from Risk Manager output.

- <b>Tests:</b> prompt construction, structured-output parsing, guardrail rejection of fabricated numbers.

### Phase 4 — Critic / guardrail and no-trade branch

- [ ] Critic checks: fundamental vs technical contradictions, unsupported claims, violated hard veto rules, stale/incomplete data.
- [ ] Deterministic validators run alongside the LLM critic; rules take precedence over the model.
- [ ] Bounded revision: max one Decision Synthesizer retry with critic feedback, then finalize.
- [ ] Deterministic confidence downgrade policy (weak evidence, thin retrieval, stale data).
- [ ] Decision may only be downgraded, never upgraded.

- <b>Tests:</b> contradiction, unsupported claim, stale data, forced no-trade, retry bound.

### Phase 5 — Tracing and API integration

- [ ] Capture per-run trace: run id, ticker, ordered steps, agent name, tool calls (redacted where large), latency, token usage, retries, final decision.
- [ ] Persist traces in Postgres (`agent_runs`, `agent_run_steps`) with migration.
- [ ] `POST /analyze` accepts optional `include_trace=true`; additive `trace` block, default shape unchanged.
- [ ] `GET /runs/{run_id}` returns the full trace for a previous run.
- [ ] Emit OpenTelemetry spans per agent step for Week 9 observability.

- <b>Tests:</b> trace persistence, API contract tests, end-to-end agentic run with mocked LLM.

### Phase 6 — Hardening, docs, ADRs

- [ ] Timeouts, retries with backoff, graceful degradation: an LLM specialist failure falls back to the deterministic result with a warning, never a 500.
- [ ] Cost/latency budget per run enforced and logged.
- [ ] README + architecture doc updated with agent topology and the linear-vs-agentic flag.
- [ ] ADR: why ADK instead of LangGraph.
- [ ] ADR: coded ADK agents vs Managed Agents in the Gemini API (evaluation only).
- [ ] Golden-case fixtures: 3 tickers (clear trade, clear no-trade, ambiguous) for the Week 9 evaluation suite.

---

## 6. Definition of done

- [ ] All phase checkboxes complete; CI green (lint, types, unit, integration).
- [ ] `PIPELINE_MODE=agentic` produces identical deterministic risk numbers to `linear` for the golden cases.
- [ ] Every recommendation has a retrievable execution trace showing agents, tools, and reasons.
- [ ] The system explains in prose why it made or rejected a trade, with citations.
- [ ] No code path lets an LLM compute or modify stop-loss, take-profit, position size, or upgrade a decision.
- [ ] An LLM/tool failure degrades gracefully to a deterministic answer with a warning.
- [ ] Runs locally in Docker and remains deployable to Cloud Run.
- [ ] Docs and both ADRs merged.

## 7. Explicit non-goals for the agent

- Do not introduce LangChain or LangGraph.
- Do not modify the deterministic rules engine, risk math, or the RAG ingestion/retrieval implementation.
- Do not migrate to Vertex AI Vector Search.
- Do not implement Gemini Enterprise / Agent Marketplace packaging.
- Do not add broker or order-execution functionality.
- Do not change existing `POST /analyze` response fields (additive changes only).

## 8. References

- Roadmap: [Forseti](Roadmap) (section <i>Weeks 7–8 — Agentic workflow with ADK</i>)
- Previous phase: [Execution Plan: RAG + Vector Database (Weeks 5–6)](Execution-Plan-RAG-and-Vector-Database)
- Style guide: [Coding Agent Clean Code Instructions](Coding-Agent-Clean-Code-Instructions)
- Repo: [https://github.com/ibudasov/forseti](https://github.com/ibudasov/forseti)

---

_Migrated from Notion on 2026-11-12._
