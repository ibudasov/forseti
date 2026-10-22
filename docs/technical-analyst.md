# Technical Analyst

The Technical Analyst is a single-purpose model role. It consumes a `TechnicalAnalysisRequest` and returns a strict `TechnicalAssessmentResponse`. It interprets trend, momentum, overextension, support/resistance, volume confirmation, volatility, and event-risk context that fixed deterministic thresholds cannot capture.

## Responsibility boundary

The analyst may:

- interpret derived technical facts supplied in the immutable context;
- propose a bounded, downgrade-only score adjustment in `[allowed_adjustment_min, 0]`;
- surface contradictions, risk flags, and missing information.

The analyst may not:

- compute new indicator values (it only cites facts by `metric_id`);
- propose a positive adjustment;
- change risk values, entries, exits, or time stops;
- output trade decisions, confidence, or support/resistance levels not already a cited fact;
- invent citations or uncited numeric claims.

## Input contract

The input is the immutable `TechnicalAnalysisRequest` documented in `docs/technical-analysis-context.md`.

Important invariants:

- `run_id` and `context_hash` must round-trip unchanged;
- the allowed adjustment bounds come from the request and always include `0` as the maximum;
- facts are derived deterministically upstream (TA-2); the analyst never recomputes them.

## Output contract

`TechnicalAssessmentResponse` requires:

- `schema_version = "1.0"`
- `agent_name = "technical_analyst"`
- `status`: `completed`, `insufficient_data`, or `failed`
- `overall_signal`: `strong_negative`, `negative`, `neutral`, `positive`, or `strong_positive`
- `trend_state`: `uptrend`, `downtrend`, `range`, `transition`, or `unknown`
- `momentum_state`: `strengthening`, `stable`, `weakening`, `overextended`, or `unknown`
- `proposed_score_adjustment` (always `<= 0`)
- cited `findings`, `contradictions`, and `risk_flags`
- `missing_information`
- `summary`

All models use `extra="forbid"`.

## Validation and rejection rules

The service rejects or neutralizes output when:

- `run_id` or `context_hash` does not match the request;
- `proposed_score_adjustment` falls outside the allowed bounds;
- any cited `metric_id` is unknown (not in `request.facts` or `request.deterministic_result.metrics`);
- a medium/high-materiality finding has no citation;
- a number written in a claim does not equal the value of one of that finding's cited facts (checked as either
  a fraction or a ×100 percentage for `pct`-unit facts, within tolerance);
- a negative adjustment has no supporting `risk_flags` or medium/high-materiality negative finding;
- finding IDs are duplicated across any response section;
- forbidden fields such as `decision`, `stop_loss`, `take_profit`, `risk_reward`, `position_size_eur`,
  `time_stop_at`, `proposed_decision`, `entry`, `target`, `support_level`, or `resistance_level` appear anywhere
  in the JSON payload;
- the provider fails, times out, or returns malformed JSON.

Rejected output becomes a neutral fallback with explicit reason codes.

## Adjustment rubric

- `0`: the evidence is confirmatory or mixed, or deterministic rules already passed (that alone is never a reason
  for any adjustment).
- `-1`: one well-supported technical risk the deterministic rules miss.
- `-2`: a severe, well-supported red flag (for example a breakdown below `swing_low_20` combined with a
  weakening trend).

Positive adjustments are rejected unconditionally: `allowed_adjustment_max` is always `0`.

## Prompt and failure behavior

The prompt:

- requires JSON only;
- forbids tools and extra keys;
- forbids decision, risk, and position-sizing fields;
- requires every cited number to equal a cited fact's value;
- states that passing deterministic rules is never, by itself, a reason for an adjustment.

Failure behavior:

- missing required facts (`coverage.required_facts_missing` non-empty) or stale bars (`coverage.bars_stale`)
  short-circuit to `insufficient_data` with no model call;
- provider, timeout, malformed JSON, and schema errors fall back to `failed`;
- all fallbacks force `overall_signal = neutral`, `trend_state = momentum_state = unknown`, and
  `proposed_score_adjustment = 0`.

## Developer command

Run one standalone assessment without applying policy:

```bash
make assess-technicals ticker=NVDA as_of=2026-09-08
```

The command prints the full JSON `TechnicalAssessmentResult`, including validation reason codes, sanitized raw
output, latency, and token usage.
