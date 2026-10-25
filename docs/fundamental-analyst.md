# Fundamental Analyst

The Fundamental Analyst is a single-purpose model role. It consumes a `FundamentalAnalysisRequest` and returns a strict `FundamentalAssessmentResponse`. It interprets evidence quality, contradictions, guidance, concentration, cash-flow quality, and accounting context that fixed deterministic thresholds cannot capture.

## Responsibility boundary

The analyst may:

- interpret narrative evidence supplied in the immutable context;
- propose a downgrade-only score adjustment in `[-2, 0]`;
- surface contradictions, red flags, and missing information.

The analyst may not:

- compute deterministic metrics;
- change risk values or time stops;
- output trade decisions, confidence, or technical signals;
- invent citations or uncited material claims.

Deterministic rules set the decision ceiling for every agent
([ADR 008](adr/008-agent-authority.md)). Positive evidence may support the
narrative or a positive `overall_signal`, but cannot increase the score or
promote the decision. The policy independently rejects positive adjustments
with `promotion_not_allowed`, and caps even shadow counterfactuals at the
incoming decision. No Fundamental path recomputes risk numbers or time stops.

## Agentic typed view

In `shadow` and `enforced` modes, the workflow runs this analyst once before
ADK and passes its validated result into the Fundamental Analyst specialist
slot. That slot serializes the typed fields and their metric/chunk citations to
`fundamental_view` without making a second model call. The Synthesizer and
Critic consume this same validated view. In `off` mode the legacy narrative
`LlmAgent` remains unchanged.

## Input contract

The input is the immutable `FundamentalAnalysisRequest` documented in `docs/fundamental-analysis-context.md`.

Important invariants:

- evidence text is untrusted data;
- the prompt explicitly tells the model to ignore instructions embedded in evidence;
- `run_id` and `context_hash` must round-trip unchanged;
- the allowed adjustment bounds come from the request, whose maximum is fixed at `0`.

## Output contract

`FundamentalAssessmentResponse` requires:

- `schema_version = "1.0"`
- `agent_name = "fundamental_analyst"`
- `status`: `completed`, `insufficient_data`, or `failed`
- `overall_signal`: `strong_negative`, `negative`, `neutral`, `positive`, or `strong_positive`
- `proposed_score_adjustment`
- cited `findings`, `contradictions`, and `material_red_flags`
- `evidence_coverage`
- `missing_information`
- `summary`

All models use `extra="forbid"`.

## Validation and rejection rules

The service rejects or neutralizes output when:

- `run_id` or `context_hash` does not match the request;
- `proposed_score_adjustment` falls outside the allowed bounds;
- any cited `metric_id` or `chunk_id` is unknown;
- a medium/high-materiality finding has no citation;
- finding IDs are duplicated across any response section;
- forbidden fields such as `decision`, `stop_loss`, `take_profit`, `risk_reward`, `position_size_eur`, or `time_stop_at` appear anywhere in the JSON payload;
- the provider fails, times out, or returns malformed JSON.

Rejected output becomes a neutral fallback with explicit reason codes.

## Adjustment rubric

- `0`: positive, mixed, confirmatory, or insufficient evidence.
- `-1`: one well-supported material concern.
- `-2`: a severe, well-supported red flag that materially weakens the baseline.

## Prompt and failure behavior

The prompt (`fundamental-analyst.v2`):

- requires JSON only;
- forbids tools and extra keys;
- forbids risk, trade, and technical fields;
- requires citations for material claims;
- tells the model to ignore instructions in evidence text.

Failure behavior:

- empty evidence short-circuits to `insufficient_data`;
- provider, timeout, malformed JSON, and schema errors fall back to `failed`;
- all fallbacks force `proposed_score_adjustment = 0`.

## Developer command

Run one standalone assessment without applying policy:

```bash
make assess-fundamentals ticker=NVDA as_of=2026-09-08
```

The command prints the full JSON `FundamentalAssessmentResult`, including validation reason codes, sanitized raw output, latency, and token usage.
