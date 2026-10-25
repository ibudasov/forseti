# ADR 009 — Typed Decision Synthesizer contract

## Status

Accepted.

## Context

The previous synthesizer instruction requested a plain-text memo while the
workflow expected JSON. It also exposed deterministic risk values to model
output, then relied on workflow checks to reject attempts to change them.
Memo claims were not represented as typed, machine-checkable citations.

## Decision

The synthesizer receives a typed `DecisionSynthesisRequest` containing the
deterministic baseline, risk outputs, diagnosis, warnings, analyst views, and
known citation IDs. Its context hash is produced using the shared context-hash
service.

The model returns a typed `DecisionMemoResponse`: a proposed decision,
confidence, thesis, and individually cited key reasons, risks, and conditions
that could change the view. The response has no risk or money fields. The
deterministic template owns rendering of risk levels.

The model is bounded by the incoming decision and confidence. It may only
preserve or downgrade either value, may cite only known metric/chunk/warning
IDs, and may not make unsupported numeric claims. Invalid model responses
degrade to a deterministic memo that preserves the baseline. This stage does
not recompute or change deterministic risk values.

The service uses a protocol-based model port and Gemini adapter with bounded
retries. Shared strict JSON parsing and Gemini schema helpers live in
`app/services/llm_schema.py`. The typed service is intentionally not wired into
the workflow in this change; workflow integration is a separate follow-up.

## Validation

The offline evaluation replays recorded model responses for clean trade,
watchlist, hard blocker, conflicting views, hallucinated number, money-field
leak, and promotion attempt. It requires 100% schema validity after fallback,
zero money fields, zero promotions, and citation precision of at least 0.95.
The golden-cassette baseline for existing synthesizer outputs is 0 unparsable
outputs among 3 recorded outputs.
