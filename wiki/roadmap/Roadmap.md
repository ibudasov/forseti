This page turns the study plan into a concrete implementation roadmap for the trading pet project described in [Trading](Trading-Framework).

- the goal of the project is to asses stocks according to how it is described in the page [Trading](Trading-Framework)
- the project should receive as an input the reference to the stock on one of the described trading platforms and as a result I would like to receive a recommendation on how to make an trade with necessary parameters of StopLoss, TakeProfit and the size of the deal. Or maybe a recommendation to not do a trade as it is highly unlikely to win.

What is already done is here: <https://github.com/ibudasov/forseti>

- in the commits of the repository <https://github.com/ibudasov/forseti/commits/main/>
- and in the issues of the project <https://github.com/ibudasov/forseti/issues?q=is%3Aissue%20state%3Aclosed>

Worth mentioning that my GCP project is called “Forseti”

## Project goal

Build an <b>AI-assisted swing-trade analyst</b> for US equities in target sectors:

- AI
- neoclouds
- defence
- nuclear
- green energy
- quantum
- robotics
- space

The system should accept a ticker or a platform reference, gather structured and unstructured evidence, and return one of:

- <b>Trade</b>
- <b>Watchlist</b>
- <b>No trade</b>

If the result is <b>Trade</b>, the system should also return:

- entry range
- stop-loss
- take-profit
- position size
- risk/reward
- confidence
- key reasons
- warnings

## Core design principles

> 💡 Use <b>deterministic code</b> for calculations and hard rules. Use <b>LLM + RAG</b> for explanation, evidence synthesis, and readable trade memos.

### Deterministic components

- position sizing
- stop-loss and take-profit math
- RSI / moving average checks
- hard veto rules
- checklist scoring

### AI-assisted components

- earnings / filing / news summaries
- bullish vs bearish thesis synthesis
- catalyst analysis
- final recommendation explanation

## Scope for v1

### In scope

- US equities only
- long-only swing trades
- holding period: weeks to months
- recommendation engine
- manual trade execution
- API-first implementation

### Out of scope

- direct broker automation
- autonomous order placement
- portfolio optimization
- multi-asset support
- complex frontend in the first version

## Target architecture

1. <b>Input layer</b>
   - accepts ticker or trading-platform reference
   - normalizes to a ticker
2. <b>Structured data layer</b>
   - price history
   - technical indicators
   - fundamentals
   - VIX
   - earnings dates
3. <b>Rules engine</b>
   - validates fundamental criteria
   - validates technical criteria
   - applies risk rules
   - produces provisional trade / no-trade output
4. <b>RAG layer</b>
   - indexes filings, earnings summaries, and news
   - retrieves relevant evidence for the current ticker
   - summarizes risks, catalysts, and thesis quality
5. <b>Decision layer</b>
   - combines rules-engine output with retrieved evidence
   - produces final recommendation JSON
   - produces human-readable explanation
6. <b>Observability and evaluation</b>
   - latency
   - token usage
   - retrieval quality
   - consistency checks
   - golden-case regression tests

### Dataflow Diagram

> 🗺️ **Dataflow diagram:** [view the original HTML source](Dataflow-Diagram) (copy it to a file and open in a browser).

## Tech stack (Google-focused)

To align with Google ecosystem preferences, the project relies heavily on Google Cloud Platform:

- <b>Application Framework</b>: Python, FastAPI, Pydantic
- <b>Compute</b>: Google Cloud Run (fully managed serverless containers) for the API; evaluate <b>Vertex AI Agent Runtime</b> as the managed hosting target for the agent layer (Cloud Run remains a valid fallback)
- <b>Database & Vector Store</b>: Cloud SQL for PostgreSQL (with `pgvector` enabled) or Vertex AI Vector Search
- <b>AI / LLMs</b>: Vertex AI (Gemini models) for thesis synthesis, RAG, and reasoning
- <b>Agent framework</b>: Google <b>ADK (Agent Development Kit)</b> instead of LangChain/LangGraph
- <b>Declarative agents</b>: <b>Managed Agents in the Gemini API</b> (JSON-configured agents, Antigravity-style) for lighter-weight agent definitions
- <b>Enterprise distribution</b>: <b>Gemini Enterprise / Agent Marketplace</b> for agent cataloguing, access control, identity, and governance
- <b>Storage</b>: Google Cloud Storage (for raw ingested 10-K filings, earnings transcripts)
- <b>Secrets</b>: Google Cloud Secret Manager
- <b>Observability</b>: Google Cloud Observability (formerly Stackdriver) with OpenTelemetry
- <b>Frontend UI</b>: Streamlit (deployed on Cloud Run)

## Recommendation schema

```json
{
  "ticker": "NVDA",
  "decision": "trade | watchlist | no_trade",
  "entry_range": [120.0, 124.0],
  "stop_loss": 112.0,
  "take_profit": [135.0, 145.0],
  "risk_reward": 1.8,
  "position_size_eur": 350.0,
  "confidence": 0.71,
  "reasons": [],
  "warnings": []
}
```

## Concrete implementation roadmap

## Week 0 — Scope freeze and design

### Deliverables

- one-page product spec
- input/output schema
- architecture diagram
- backlog of v1 vs later ideas
- success criteria

### Tasks

- define supported sectors and asset universe
- define exact decision states: trade / watchlist / no-trade
- define minimum required inputs
- define the fields of the output contract
- define hard veto rules
- define the initial scoring logic

### Exit criteria

- project scope is frozen
- no brokerage automation planned for v1
- output schema is stable

## Weeks 1–2 — Python + service foundation

### Goal

Build the production skeleton first.

### Stack

- Python
- FastAPI
- Pydantic
- Docker
- Postgres

### Tasks

- create project structure
- implement FastAPI app
- add typed request/response models
- add Docker setup
- create basic Postgres schema
- implement health endpoint
- implement `POST /analyze`
- implement `GET /ticker/{symbol}`

### First functional modules

- ticker normalization
- recommendation schema
- placeholder analyzer
- risk-engine skeleton
- logging and config management

### Exit criteria

- API accepts a ticker and returns a valid structured response
- project runs locally in Docker
- data model is stable enough for later phases

## Weeks 3–4 — Structured data + rules engine

### Goal

Create a usable non-LLM MVP first.

### Tasks

- ingest OHLCV price data
- ingest basic company fundamentals
- ingest VIX
- ingest earnings dates
- create feature tables in Postgres
- compute:
  - RSI
  - 50-day moving average
  - 200-day moving average
  - volume trend
- implement pass/fail checklist logic
- implement stop-loss, take-profit, and position-size calculations

### Initial decision logic

- fundamentals score
- technical score
- macro/sentiment check
- data freshness and completeness check

### Rules to encode from the trading framework

- revenue growth
- free cash flow
- debt/equity
- price vs 50-day MA
- price vs 200-day MA
- RSI state
- VIX context
- risk cap enforcement

### Exit criteria

- ticker analysis works without LLM support
- system can output trade / watchlist / no-trade
- stop-loss and position size are reproducible and testable

## Weeks 5–6 — RAG + vector database

### Goal

Add the highest-priority gap area: retrieval-augmented analysis.

### Data to index

- earnings-call summaries
- 10-K / 10-Q business sections
- 10-K / 10-Q risk sections
- recent company news
- sector news

### Storage strategy

- structured features in Postgres
- embeddings in pgvector first
- optional second implementation with Vertex AI Vector Search later

### Retrieval questions

- what are the main bullish drivers?
- what are the main bearish risks?
- what near-term catalysts matter?
- does recent news support or weaken the setup?
- are there major red flags not visible in price action alone?

### Important rule

> 📐 The RAG layer explains the thesis. It does <b>not</b> calculate stop-loss, take-profit, or position size.

### Exit criteria

- the app retrieves relevant evidence for a ticker
- the app generates a grounded summary
- no-trade recommendations can be justified with evidence

## Weeks 7–8 — Agentic workflow with ADK (Agent Development Kit)

### Goal

Turn the pipeline into a traceable multi-step AI workflow, built on Google's ADK rather than LangChain/LangGraph, so the orchestration stays inside the Google ecosystem.

### Why ADK instead of LangGraph

- native fit with Vertex AI and Gemini models
- first-class support for multi-agent (supervisor + specialist) topologies
- built-in tracing and evaluation hooks that map onto Cloud Observability
- deployable to <b>Vertex AI Agent Runtime</b> as a managed target

### Complementary option to evaluate

- <b>Managed Agents in the Gemini API</b>: JSON-configured agents for the simpler specialist roles, compared against fully coded ADK agents for control, testability, and cost

### Agent / node breakdown

1. Input Resolver
2. Structured Data Collector
3. Retriever
4. Fundamental Analyst
5. Technical Analyst
6. Risk Manager
7. Decision Synthesizer
8. Critic / Guardrail

### Patterns to demonstrate

- tool use / ReAct-style loops (ADK tools wrapping the deterministic rules engine)
- self-critique
- supervisor + specialist flow (ADK root agent with sub-agents)
- explicit no-trade branch
- confidence downgrade when evidence is weak

### Suggested responsibilities

- <b>Fundamental Analyst</b>: growth, cash flow, debt, quality
- <b>Technical Analyst</b>: trend, RSI, support/resistance, momentum
- <b>Risk Manager</b>: entry, stop, take-profit, size
- <b>Critic</b>: finds contradictions and weak evidence

### Exit criteria

- every recommendation has a visible execution trace
- the system can explain why it made or rejected a trade
- failure modes are easier to debug
- the agent runs both locally and on a managed Google runtime

## Enterprise agent distribution — Gemini Enterprise and Agent Marketplace

### Goal

Treat the finished ADK agent as something that would be published inside a company, not just run as a personal script.

### Topics to study

- <b>Gemini Enterprise</b> and the <b>Agent Marketplace</b> (naming may change by the time this is built)
- publishing an ADK agent into an enterprise agent catalogue
- who can discover and invoke the agent (access control)
- what data the agent is allowed to reach (data-scope control)
- <b>agent identity</b>: service accounts, delegated user identity, auditability
- observability and governance of deployed agents

### Key use case to demonstrate

> 🏢 Expose the Forseti ADK agent in an enterprise agent catalogue, with explicit control over who can use it, what data it can access, its identity, and how its behaviour is observed and governed.

### Exit criteria

- documented story for how Forseti would be catalogued and governed in an enterprise
- access-control and agent-identity model written down
- observability/governance requirements mapped to Cloud Observability

## Week 9 — Evaluation and observability

### Goal

Make the project production-shaped instead of demo-shaped.

### Observability

- latency
- token usage
- cost per recommendation
- retrieval hit rate
- failure rate
- confidence score distribution

### Evaluation set

Create a small benchmark of:

- clear good setups
- clear no-trades
- ambiguous cases
- incomplete-data cases

### Questions to test

- did the rules engine behave consistently?
- did the retrieval fetch relevant evidence?
- did the model hallucinate unsupported claims?
- did the final output respect the risk rules?
- did the system choose no-trade when uncertainty was high?

### Exit criteria

- basic dashboards exist
- a golden-case evaluation suite runs automatically
- model and retrieval regressions are measurable

## Week 10 — GCP packaging and deployment

### Goal

Package the project as if it were for a real customer.

### Deployment target

- FastAPI on Cloud Run
- managed Postgres
- scheduled refresh jobs
- Secret Manager for credentials
- object storage for raw documents
- OpenTelemetry or equivalent tracing

### Minimal UI options

- Streamlit
- a very small React frontend
- API-only demo with Swagger and sample requests

### Demo flow

- user enters a ticker
- system analyzes market data and evidence
- system returns structured recommendation
- system shows rationale, warnings, and confidence

### Exit criteria

- public demo environment or stable dev environment on GCP
- clear architecture document
- reproducible deployment
- working end-to-end demo

> 📌 Week 10 deploys Forseti as a typical web app on Cloud Run: API, deterministic rules, agents, guardrails and jobs all in one container. Agent Engine comes in at Week 11. Decisions: [Week 10: GCP Deployment Decisions](Week-10-GCP-Deployment-Decisions)

## Week 11 — Conversational agent on Vertex AI Agent Engine

### Goal

Add a thin conversational ADK agent, hosted on Agent Engine, that uses the deployed Forseti API as a tool. The trusted pipeline (rules, agents, guardrails) stays on Cloud Run; the new agent only turns chat into API calls and explains the typed results.

### Architecture

```text
User chat: "How does NVDA look for a swing trade?"
  → Forseti conversational agent (ADK on Agent Engine)
  → tool: POST /analyze on Cloud Run (service-account auth)
  → typed recommendation + memo
  → agent explains decision, risk block, warnings and confidence in chat
```

### Tasks

- define tools wrapping the Forseti API: `analyze_ticker`, `get_ticker`, and optionally `list_universe`
- generate tool schemas from the OpenAPI contract so the tools can't drift from the API
- write the agent instruction: never invent prices or risk numbers, always quote the API's values verbatim, and say no-trade clearly
- service-to-service auth: the Agent Engine service account gets invoker rights on the private Cloud Run service
- use Agent Engine sessions for multi-turn follow-ups ("why not a trade?", "what would change it?") without re-running the analysis
- tracing: connect Agent Engine traces and Cloud Run OpenTelemetry spans in Cloud Trace (one trace per question)
- eval set: conversational golden cases checking tool-call correctness, numbers quoted verbatim and no hallucinated advice
- Terraform for the Agent Engine deployment, service account and IAM bindings; deploy from CI

### Exit criteria

- the agent answers ticker questions using only data returned by the Forseti API
- numbers in chat match the API response exactly (checked automatically)
- the end-to-end trace is visible in Cloud Trace
- reproducible deployment via Terraform and CI

## Week 12 — Gemini Enterprise

### Goal

Make the Week 11 agent available in Gemini Enterprise, with explicit access control, agent identity and governance. This is the practical delivery of the Enterprise agent distribution section above.

### Tasks

- set up Gemini Enterprise access for the GCP project "Forseti" (check licensing and cost first)
- register the Agent Engine–hosted ADK agent in Gemini Enterprise
- access control: only Igor's identity, or a dedicated Google group, can discover and use the agent
- agent identity: document which service account calls Cloud Run, and whether user identity is delegated or not
- data scope: the agent can reach the Forseti API only, with no direct database or bucket access
- governance and audit: enable audit logs, record who asked what and when, and link it to `AgentRun` records
- write down the governance model and the steps for later marketplace publication (partner onboarding, listing, pricing, support terms; a commercial process, not just a deploy step)

### Exit criteria

- Forseti is usable from Gemini Enterprise by an authorised user only
- access control, identity and data-scope model documented and enforced
- audit trail connects Gemini Enterprise questions to Forseti runs
- marketplace path documented as a future business milestone

## Suggested module breakdown

### Module 1 — Universe and screening

- sector tagging
- liquidity filter
- growth/quality filters
- candidate selection
- include selected publicly traded neocloud stocks in the supported universe

### Module 2 — Technical setup

- RSI
- moving averages
- support/resistance
- volume confirmation

### Module 3 — Fundamentals

- revenue growth
- EPS trend
- free cash flow
- debt/equity
- margins

### Module 4 — News and catalysts

- earnings
- regulation
- contracts
- macro decisions
- sector-specific developments

### Module 5 — Risk engine

- entry logic
- stop-loss logic
- take-profit logic
- risk/reward
- position sizing

### Module 6 — Recommendation engine

- decision label
- explanation
- confidence
- warnings
- missing-data alerts

## Epic — Add Neocloud Stocks to the Supported Universe

### Goal

Make publicly traded neocloud providers eligible for Forseti's existing research and screening workflow, with clear sector classification and no changes to the project's risk-first, human-executed approach.

### Scope

- Add `neocloud` as a supported sector tag and define how neocloud companies that also fit the AI category are classified.
- Identify and validate the initial set of publicly traded US neocloud stocks; record the rationale and authoritative source for each addition.
- Add the approved stocks to the universe seed and ensure ingestion, ticker lookup, screening, and analysis handle them through the existing pipeline.
- Keep existing fundamental, technical, data-quality, and risk checks in force; do not add neocloud-specific trade overrides.

### Tasks

- [ ] Define a repeatable inclusion rule for a company to qualify as a neocloud stock, plus a deterministic rule for sector-tag overlap.
- [ ] Select and verify the initial public ticker list; document its sources and update cadence.
- [ ] Extend the sector-tag model, validation, seed data, and any relevant migrations for `neocloud`.
- [ ] Add offline tests for tagging, universe seeding, and end-to-end analysis of neocloud tickers, including missing-data and no-trade cases.
- [ ] Update the roadmap/repository documentation with the new sector and universe coverage.

### Exit criteria

- Approved neocloud tickers are seeded idempotently and flow through the existing ingestion and analysis pipeline.
- Their sector classification is deterministic, documented, and covered by tests.
- Existing risk rules remain unchanged, and incomplete evidence still produces the normal warning or no-trade behavior.

## Milestones

### MVP

- deterministic trade recommendation engine
- API only
- no RAG yet

### V1

- RAG-backed explanation layer
- ADK orchestration
- evaluation dataset

### V2

- GCP deployment
- observability dashboards
- minimal UI

### V3

- Vertex AI Vector Search version
- Vertex AI Agent Runtime deployment
- Managed Agents (Gemini API) variant of a specialist agent
- Gemini Enterprise / Agent Marketplace packaging and governance model
- MCP exposure for external tool integration

## Success criteria

- given a ticker, the system returns a stable structured recommendation
- risk math is deterministic and testable
- evidence retrieval improves explanation quality
- the app can justify both <b>Trade</b> and <b>No trade</b>
- the full system can be demoed end-to-end on GCP

## Recommended execution order

1. deterministic trade engine
2. structured data ingestion
3. RAG explanation layer
4. ADK orchestration
5. evaluation and observability
6. GCP deployment (Cloud Run web app)
7. conversational agent on Agent Engine with Forseti as a tool
8. Gemini Enterprise (marketplace later)
9. optional UX improvements

## Backlog for later

- broker integration
- paper-trading mode
- portfolio-level risk controls
- alerting workflows
- sector watchlist automation
- historical backtesting UI

## Final positioning

The best framing for this project is:

> an <b>AI decision-support system for swing-trade research</b>, not an autonomous trading bot.

That framing better demonstrates:

- Python backend engineering
- RAG
- vector databases
- agentic orchestration with ADK
- enterprise agent governance
- observability
- cloud deployment
- production-minded system design

- [Coding Agent Clean Code Instructions](Coding-Agent-Clean-Code-Instructions)

- [Execution Plan: GET /ticker/{symbol}](Execution-Plan-GET-ticker-symbol)

- [Execution Plan: Structured Data Ingestion](Execution-Plan-Structured-Data-Ingestion)

- [Execution Plan: Evaluation Engine](Execution-Plan-Evaluation-Engine)

- [Execution Plan: RAG + Vector Database (Weeks 5–6)](Execution-Plan-RAG-and-Vector-Database)

- [Execution Plan: Agentic Workflow with ADK (Weeks 7–8)](Execution-Plan-Agentic-Workflow-with-ADK)

- [Execution Plan: Prove the Agent Workflow Is Trustworthy](Execution-Plan-Prove-the-Agent-Workflow-Is-Trustworthy)

- [Execution Plan: Technical Analyst Hardening](Execution-Plan-Technical-Analyst-Hardening)

- [Execution Plan: Agent-Layer Loose Ends](Execution-Plan-Agent-Layer-Loose-Ends)

- [Week 10: GCP Deployment Decisions](Week-10-GCP-Deployment-Decisions)

---

_Migrated from Notion on 2026-11-12._
