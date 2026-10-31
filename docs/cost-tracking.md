# Cost tracking

Cost per recommendation is estimated from recorded usage and a versioned price
catalog. Estimates are never silently `0`: a call that cannot be priced is
reported as `unpriced`.

## Billable calls

| Call | Where usage is captured | Priced? |
| --- | --- | --- |
| ADK `LlmAgent` steps (fundamental/technical/critic/synthesizer narration) | ADK event `usage_metadata` → `TraceStep.token_usage` (`adk_events.event_token_usage`) | Yes, per step |
| Typed Fundamental analyst | `FundamentalAnalyst` → `gemini_token_usage` (`app/services/llm_schema.py`), merged into the run total | Yes (as `unattributed_model_calls`) |
| Typed Technical analyst | `TechnicalAnalyst` → `gemini_token_usage`, merged into the run total | Yes (as `unattributed_model_calls`) |
| Critic and its revision loop | `Critic` → `gemini_token_usage`, `additional_token_usage` merged into the run total | Yes (as `unattributed_model_calls`) |
| Decision Synthesizer incl. revision loop | `DecisionSynthesizer` → `gemini_token_usage`, merged into the run total | Yes (as `unattributed_model_calls`) |
| Typed analyst views | No model call; zero-token steps | Cost 0 |
| RAG evidence synthesis (`app/rag/synthesis.py`) | `SynthesisOutput.token_usage` / `model_name` | Usage recorded; not part of an analysis run (`GET /ticker/{symbol}/evidence` only) |
| Query embeddings at analysis time | `VertexAIEmbeddingClient.usage` (`EmbeddingUsage.characters`), delta per run | Yes (`query_embeddings` line) |
| Ingestion embeddings | Same counter; `ingest_ticker_with_cost()` returns `IngestionSummary.embedding_cost` | Yes, per ingestion |

`MockEmbeddingClient` is never billed. Per-agent model overrides do not exist
yet: every agent uses `GEMINI_MODEL` (`trace_cost.model_for_agent`).

## How pricing works

* Catalog: `app/pricing/catalog.json`, validated by `app/pricing/catalog.py`.
  Each entry has `model`, `unit` (`per_million_tokens` or
  `per_million_characters`), `input`, `output`, `cached_input`, an optional
  `long_context` block (`threshold_tokens` plus its own rates), `effective_from`
  and `source_url`. The top-level `version` becomes `pricing_version`.
* `app/services/cost_model.py` prices each `UsageLine` with its own model:
  `prompt - cached` at the input rate, cached tokens at `cached_input`,
  `candidates + thoughts` at the output rate. Embeddings are priced per input
  character.
* `CostEstimate(usd, status, unpriced_models, pricing_version)` where status is
  `priced`, `partially_priced` or `unpriced`. An unknown model logs one
  `pricing_missing:<model>` warning per process.
* `check_pricing_coverage(settings)` runs at API startup and logs an ERROR (not
  fatal) when `GEMINI_MODEL` or `EMBEDDING_MODEL` has no catalog entry.
* `app/services/token_pricing.py` keeps its public functions as wrappers.
* `MODEL_PRICING_USD_PER_MILLION` is an optional env override for experiments
  (`{"model": {"input": 0.1, "output": 0.4}}`). It marks the version with
  `+env_override`.

## Persistence

`agent_runs` stores `cost_usd`, `cost_breakdown` (per step and model: tokens,
usd, plus status and unpriced models) and `pricing_version` (migration
`0014_agent_run_cost`). `POST /analyze?include_trace=true` and
`GET /runs/{run_id}` return `estimated_cost_usd`, `cost_status`,
`cost_breakdown` and `pricing_version`. Runs persisted before the migration
have these fields `null`.

## Updating prices

1. Read the current list prices from the
   [Vertex AI pricing page](https://cloud.google.com/vertex-ai/generative-ai/pricing).
2. Edit `app/pricing/catalog.json`: add or change an entry with a new
   `effective_from` and bump the top-level `version`. Historical runs keep the
   `pricing_version` they were priced with.
3. Run `make test`.

The shipped values were entered without access to the pricing page; the owner
must verify them against the page before relying on them for budgeting.

## Reports

`scripts/trust_e2e.py`, `scripts/live_preflight.py` and
`scripts/eval_fundamental_agent.py` show `unpriced` instead of `0.000000` and
exit non-zero when a report contains unpriced runs; pass `--allow-unpriced` to
override. The technical, critic and synthesizer eval reports do not report cost.

## Reconciling against the billing report

Compare the sum of `cost_usd` for runs on a day with that day's Vertex AI line
item in the Cloud Billing report; document any gap (list vs. negotiated price,
retries, calls not in the trace such as RAG synthesis).

Billing attribution via request labels (`app`, `agent`, `env`) is **not
implemented**: it is optional and requires verifying that the pinned SDK
supports labels on generate-content calls.
