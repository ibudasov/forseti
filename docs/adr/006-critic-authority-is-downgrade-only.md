# ADR 006 — Critic authority is downgrade-only

## Status

Accepted.

## Context

The roadmap introduced four specialist prompts: Fundamental Analyst, Technical
Analyst, Decision Synthesizer, and Critic/Guardrail. Only the Fundamental
Analyst had already been integrated into the production path.

That left the Critic with two open questions:

1. how much authority it should have over the deterministic baseline; and
2. how to prove it helps before enabling it in production.

The schema also matters. The recommendation contract has a single confidence
number and downgrade-only decision labels, but no field that can safely encode a
model-driven upgrade target.

## Decision

Use one finalizer chain with bounded authority:

- deterministic rules bind and fail closed
- the model advises and fails open
- the Critic is downgrade-only
- the Critic may request at most one bounded revision of the memo
- the workflow remains Deterministic Pipeline -> Decision Synthesizer ->
  Critic/Guardrail

A confidence penalty is the only allowed confidence mutation. The Critic may ask
for less confidence, not a new target confidence, because an upgrade is
intentionally unrepresentable in the schema.

Shadow mode comes before enforcement. The Critic must first produce structured,
persisted counterfactuals that can be reviewed against the frozen golden cases
and shadow-run evidence.

## Consequences

- The Critic can reject fabricated numbers, stale-data trades, unsupported
  claims, and contradiction-driven optimism without becoming the owner of risk
  math.
- Provider failures or invalid critique payloads preserve the deterministic
  answer and record `assessment_invalid` instead of raising a server error.
- The workflow can only loop once through the Decision Synthesizer, which keeps
  latency and failure modes bounded.
- The service shape now resembles the Fundamental Analyst path: request schema,
  structured result, policy application, evaluation harness, and shadow report.
  That duplication is accepted for now; consolidation is a separate follow-up.

## Rejected alternatives

### Let the Critic rewrite recommendations directly

Rejected because it would let the last model in the chain own risk-sensitive
fields and silently drift from the deterministic baseline.

### Allow unbounded critique-revise loops

Rejected because repeated LLM handoffs make latency, cost, and failure handling
harder while offering no deterministic stop condition.

### Trust the supervisor LLM to decide whether to call the Critic

Rejected because a guardrail that can be skipped by the same model stack it is
supposed to check is not a guardrail.
