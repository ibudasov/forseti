# ADR 008 — Deterministic rules set the ceiling for every agent

## Status

Accepted. The owner selected **Option A: Fundamental is downgrade-only** in
[LE-2 (#112)](https://github.com/ibudasov/forseti/issues/112).
This resolves the Fundamental promotion question recorded in
[ADR 007](007-technical-analyst-authority-is-downgrade-only.md) and extends the
bounded authority in [ADR 006](006-critic-authority-is-downgrade-only.md)
consistently to every agent.

## Context

The README and trust invariants promised downgrade-only agent decisions, but
the Fundamental Analyst could propose positive score adjustments. In enforced
mode those adjustments could promote a decision, including creating a trade
and recomputing risk numbers. A risk gate on that path did not make the
authority model consistent.

## Decision

Deterministic rules and the risk engine own the baseline. Every agent may
preserve or lower that ceiling, never raise it.

| Stage | Can downgrade | Can promote | Can change risk numbers |
| --- | --- | --- | --- |
| Rules / risk engine | Yes | Yes (deterministic recomputation only) | Yes |
| Fundamental Analyst | Yes (score adjustment `[-2, 0]`) | No | No |
| Technical Analyst | Yes (score adjustment `[-2, 0]`) | No | No |
| Critic / Guardrail | Yes (decision and bounded confidence penalty) | No | No |
| Decision Synthesizer | Yes (decision and confidence) | No | No |

Clearing trade fields when a deterministic policy downgrades to a non-trade
is permitted; inventing, changing, or recomputing numeric risk values or time
stops is not. Technical policy already clears these fields on downgrade.
The Fundamental Analyst does not directly set confidence or decision labels:
its deterministic policy maps the bounded score adjustment to a decision
capped at the incoming baseline.

The finalizer order remains Rules -> Fundamental -> Technical ->
Synthesizer -> Critic. Each transition is bounded by its incoming decision,
not merely by the original rules decision, so a downstream stage cannot undo
an earlier downgrade.

### Fundamental enforcement

- `FundamentalAnalysisRequest.allowed_adjustment_max` is fixed at `0`.
- The analyst may describe positive evidence and return a positive signal,
  but must propose adjustment `0` for it.
- The analyst validator rejects out-of-range outputs. Independently, the
  policy rejects positive adjustments with `promotion_not_allowed`, records
  the raw proposal, and applies adjustment `0`.
- Counterfactual and enforced decisions never outrank their baseline, even
  if checklist scores would otherwise imply a higher decision.
- The workflow applies only downgrade-only transitions. The promotion,
  risk-recompute, and `risk_gate_rejected_promotion` paths are removed.
- Prompt version `fundamental-analyst.v2` and policy version `2.0` distinguish
  this authority model from historical runs. Offline fixtures carry the new
  prompt version, bounds, and recomputed context hashes.

`off` skips the specialist; `shadow` records a bounded counterfactual without
changing the response; `enforced` applies only bounded downgrades. Failures
and rejected model outputs preserve the incoming baseline.

### Typed Fundamental view addendum

In `shadow` and `enforced` modes, the validated assessment already produced
for policy is the single Fundamental interpretation for the run. The ADK
`fundamental_analyst` slot serializes that assessment, including its citations,
to `fundamental_view` without making another model call. `off` retains the
legacy narrative specialist. The typed-view trace event must report zero
tokens; the upstream assessment trace retains usage for the single model call.

## Validation

The trust invariant remains `rank(agentic) <= rank(linear)`, with
`no_trade < watchlist < trade`, and confidence never increases. Fundamental
effect checks also reject positive applied adjustments and promotions in
shadow counterfactuals, even if a later agent masks them with a downgrade.

Offline regression tests replay every golden case across all 27 combinations
of Fundamental, Technical, and Critic modes. They exercise negative, neutral,
and attempted-positive Fundamental adjustments and verify decision ceilings,
confidence limits, risk ownership, and trace effects.

## Rejected alternative

Bounded promotion (Option B) would require a documented exception to the trust
invariant, a separate lift allowance, promotion tracing, and risk recalculation.
It is rejected in favor of one auditable authority rule for all agents.
