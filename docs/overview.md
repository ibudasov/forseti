# Pet Project

This page turns the study plan into a concrete implementation roadmap for the trading pet project described in [Trading](https://app.notion.com/p/Trading-3705752d31288089bf35e21f420cc055?pvs=21).

- the goal of the project is to asses stocks according to how it is described in the page [Trading](https://app.notion.com/p/Trading-3705752d31288089bf35e21f420cc055?pvs=21)
- the project should receive as input only a stock ticker abbreviation and return a recommendation on how to make a trade with necessary parameters of StopLoss, TakeProfit, and position size. It may also return a recommendation to avoid the trade when probability of success is low.

## Project goal

Build an **AI-assisted swing-trade analyst** for US equities in target sectors:

- AI
- defence
- nuclear
- green energy
- quantum
- robotics
- space

The system should accept only a ticker abbreviation, gather structured and unstructured evidence, and return one of:

- **Trade**
- **Watchlist**
- **No trade**

If the result is **Trade**, the system should also return:

- entry range
- stop-loss
- take-profit
- position size
- risk/reward
- confidence
- key reasons
- warnings

## Core design principles

<aside>
💡

Use **deterministic code** for calculations and hard rules. Use **LLM + RAG** for explanation, evidence synthesis, and readable trade memos.

</aside>

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

1. **Input layer**
    - accepts only ticker abbreviation input
    - validates and normalizes ticker format
2. **Structured data layer**
    - price history
    - technical indicators
    - fundamentals
    - VIX
    - earnings dates
3. **Rules engine**
    - validates fundamental criteria
    - validates technical criteria
    - applies risk rules
    - produces provisional trade / no-trade output
4. **RAG layer**
    - indexes filings, earnings summaries, and news
    - retrieves relevant evidence for the current ticker
    - summarizes risks, catalysts, and thesis quality
5. **Decision layer**
    - combines rules-engine output with retrieved evidence
    - produces final recommendation JSON
    - produces human-readable explanation
6. **Observability and evaluation**
    - latency
    - token usage
    - retrieval quality
    - consistency checks
    - golden-case regression tests

### Dataflow Diagram

[AI Swing-Trade Analyst — dataflow diagram](Pet%20Project/dataflow.html)

AI Swing-Trade Analyst — dataflow diagram

## Tech stack (Google-focused)

To align with Google ecosystem preferences, the project relies heavily on Google Cloud Platform:

- **Application Framework**: Python, FastAPI, Pydantic
- **Compute**: Google Cloud Run (fully managed serverless containers)
- **Database & Vector Store**: Cloud SQL for PostgreSQL (with `pgvector` enabled) or Vertex AI Vector Search
- **AI / LLMs**: Vertex AI (Gemini models) for thesis synthesis, RAG, and reasoning
- **Storage**: Google Cloud Storage (for raw ingested 10-K filings, earnings transcripts)
- **Secrets**: Google Cloud Secret Manager
- **Observability**: Google Cloud Observability (formerly Stackdriver) with OpenTelemetry
- **Frontend UI**: Streamlit (deployed on Cloud Run)

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
- enforce ticker abbreviation as the only accepted input for v1
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

<aside>
📐

The RAG layer explains the thesis. It does **not** calculate stop-loss, take-profit, or position size.

</aside>

### Exit criteria

- the app retrieves relevant evidence for a ticker
- the app generates a grounded summary
- no-trade recommendations can be justified with evidence

## Weeks 7–8 — Agentic workflow with LangGraph

### Goal

Turn the pipeline into a traceable multi-step AI workflow.

### Graph nodes

1. Input Resolver
2. Structured Data Collector
3. Retriever
4. Fundamental Analyst
5. Technical Analyst
6. Risk Manager
7. Decision Synthesizer
8. Critic / Guardrail

### Patterns to demonstrate

- tool use / ReAct-style loops
- self-critique
- supervisor + specialist flow
- explicit no-trade branch
- confidence downgrade when evidence is weak

### Suggested responsibilities

- **Fundamental Analyst**: growth, cash flow, debt, quality
- **Technical Analyst**: trend, RSI, support/resistance, momentum
- **Risk Manager**: entry, stop, take-profit, size
- **Critic**: finds contradictions and weak evidence

### Exit criteria

- every recommendation has a visible execution trace
- the system can explain why it made or rejected a trade
- failure modes are easier to debug

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

## Suggested module breakdown

### Module 1 — Universe and screening

- sector tagging
- liquidity filter
- growth/quality filters
- candidate selection

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

## Milestones

### MVP

- deterministic trade recommendation engine
- API only
- no RAG yet

### V1

- RAG-backed explanation layer
- LangGraph orchestration
- evaluation dataset

### V2

- GCP deployment
- observability dashboards
- minimal UI

### V3

- Vertex AI Vector Search version
- ADK experiment
- MCP exposure for external tool integration

## Success criteria

- given a ticker, the system returns a stable structured recommendation
- risk math is deterministic and testable
- evidence retrieval improves explanation quality
- the app can justify both **Trade** and **No trade**
- the full system can be demoed end-to-end on GCP

## Recommended execution order

1. deterministic trade engine
2. structured data ingestion
3. RAG explanation layer
4. ADK agentic orchestration
5. evaluation and observability
6. GCP deployment
7. optional UX improvements

## Agentic workflow (Google ADK)

The `agents/` package wires the deterministic services above into a traceable, multi-agent workflow built on [Google ADK](https://google.github.io/adk-docs/) (see `docs/adr/003-adk-vs-langgraph.md`). It is additive: the `PIPELINE_MODE` setting (`linear` | `agentic`, default `linear`) controls whether `POST /analyze` runs the existing linear pipeline or the ADK-orchestrated one; the response schema does not change between modes.

Topology (Trade Analyst Supervisor root agent):

1. Input Resolver — normalizes the ticker (deterministic).
2. Structured Data Collector + Retriever — gather OHLCV/indicators/fundamentals and evidence chunks (deterministic).
3. Fundamental Analyst + Technical Analyst — LLM specialists grounded in the deterministic data.
4. Risk Manager — the only source of entry/stop/target/size/R-R numbers (deterministic).
5. Decision Synthesizer — combines rules-engine output and analyst views into the recommendation memo (LLM).
6. Critic/Guardrail — can only downgrade confidence or the decision label, never upgrade it (LLM + deterministic validators).

## Backlog for later

- broker integration
- paper-trading mode
- portfolio-level risk controls
- alerting workflows
- sector watchlist automation
- historical backtesting UI

## Final positioning

The best framing for this project is:

> an **AI decision-support system for swing-trade research**, not an autonomous trading bot.
> 

That framing better demonstrates:

- Python backend engineering
- RAG
- vector databases
- agentic orchestration
- observability
- cloud deployment
- production-minded system design