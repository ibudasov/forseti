# ADR 003 — Google ADK instead of LangChain/LangGraph for the agentic workflow

**Status:** Accepted
**Date:** 2026-08-10

## Context

Weeks 7–8 of the roadmap call for turning the linear pipeline (structured data → rules engine → RAG evidence → recommendation) into a traceable multi-step agentic workflow, with a supervisor agent orchestrating specialist agents (Fundamental Analyst, Technical Analyst, Decision Synthesizer, Critic/Guardrail) and deterministic tool-agents (Input Resolver, Structured Data Collector, Retriever, Risk Manager).

Forseti already targets Vertex AI / Gemini for embeddings and (future) synthesis. Two credible frameworks were considered for the orchestration layer: LangChain/LangGraph and Google's Agent Development Kit (ADK).

## Decision

Use **Google ADK** (`google-adk` package) for the agentic workflow, not LangChain/LangGraph.

Reasons:

1. **Native fit with Vertex AI and Gemini** — ADK's `LlmAgent` is built around the same Gemini model identifiers already configured via `GEMINI_MODEL`/`VERTEX_AI_PROJECT`/`VERTEX_AI_LOCATION`, with no adapter layer needed.
2. **First-class supervisor + specialist topology** — ADK agents natively support `sub_agents` (delegation) and `tools` (deterministic function calls) on the same `LlmAgent`, which maps directly onto the Trade Analyst Supervisor topology in the execution plan.
3. **Built-in tracing/evaluation hooks** — ADK agent runs emit structured events per step, which map onto the OpenTelemetry spans and `agent_runs`/`agent_run_steps` persistence planned for Week 9 observability, without a bespoke tracing shim.
4. **Deployability** — ADK agents are deployable to the managed Vertex AI Agent Runtime or, as a fallback, to Cloud Run alongside the existing FastAPI app, matching Forseti's current deployment target.

## Consequences

- No LangChain/LangGraph dependency is introduced anywhere in the codebase.
- The `agents/` package only orchestrates; it wraps existing deterministic services (`app.services.*`, `app.rag.*`) as ADK `FunctionTool`s and never reimplements business or risk logic (see ADR 002).
- The `PIPELINE_MODE` feature flag (`linear` | `agentic`, default `linear`) lets the existing linear pipeline keep serving `POST /analyze` unchanged while the agentic workflow is built out incrementally.
- A follow-up ADR will record the evaluation of Managed Agents in the Gemini API as an alternative to coded ADK agents; that evaluation is out of scope for this decision.
