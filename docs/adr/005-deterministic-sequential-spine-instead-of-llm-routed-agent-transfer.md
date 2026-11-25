# ADR 005 — Deterministic sequential spine instead of LLM-routed agent transfer

## Status

Accepted.

## Context

The first version of the ADK orchestration used an `LlmAgent` root supervisor
with specialist sub-agents and prompt text telling the model when to call
`transfer_to_agent`. That design left execution order to model judgement even
though the workflow is a fixed pipeline: Fundamental Analyst → Technical
Analyst → Decision Synthesizer → Critic/Guardrail.

This showed the failure mode behind the 2666469 hallucination symptom: the
model could invent or misuse transfer behaviour instead of reliably following a
fixed sequence. Prompt edits can reduce that risk, but they cannot remove it.

The roadmap also treats the Critic/Guardrail as a mandatory terminal stage. A
guardrail that can be skipped by the same supervisor model it is supposed to
check is worse than no guardrail, because it creates false confidence that a
review step happened when it may not have.

Google ADK guidance for generate-and-review workflows is to model fixed
multi-step pipelines explicitly in the agent graph rather than hoping prompt
instructions force the model to visit each stage in order.

## Decision

Replace the LLM-routed root supervisor with an ADK `SequentialAgent` named
`trade_analyst_supervisor`.

The sequential spine runs these specialists in a fixed order:

1. `fundamental_analyst`
2. `technical_analyst`
3. `decision_synthesizer`
4. `critic_guardrail`

The root sequential agent carries no model, no instruction, and no tools. It
exists only to encode control flow. Each specialist writes its output into ADK
session state via a stable `output_key`, and downstream specialists read those
state values through instruction templating.

## Consequences

- Specialist execution order is guaranteed by construction rather than by model
  compliance with prompt text.
- `critic_guardrail` always runs last in the agent layer and can no longer be
  skipped by supervisor output.
- Agent names stay unchanged, so trace parsing, skip-reason reporting, replay
  fixtures, and observability code continue to key off the same identifiers.
- The change is limited to orchestration. Deterministic tools, rules, and risk
  math remain unchanged.

## Rejected alternative

Keep the root as an `LlmAgent` and keep patching the supervisor prompt.

Rejected because the problem is structural, not editorial. As long as the root
model decides whether and when to transfer, the ordering contract remains
probabilistic, and the guardrail remains skippable.

## Addendum (#217 / #214–#216): Typed Blackboard Spine & Parallel Specialist Fan-Out

The root `SequentialAgent` (`trade_analyst_supervisor`) now orchestrates typed
ADK `BaseAgent` blackboard nodes (`agents/orchestration/blackboard.py`) that
read and write validated Pydantic artifacts on `session.state`:

1. `specialist_fan_out` (`ParallelAgent`) runs `fundamental_analyst` and
   `technical_analyst` concurrently when both are enabled, followed by
   `specialist_policy_fold` which folds policies strictly sequentially
   (`Rules -> Fundamental -> Technical`) and preserves `SPECIALIST_ORDER` trace
   steps and `context_hash` determinism.
2. `cross_domain_tension` detects specialist conflicts and performs at most one
   bounded, point-in-time-safe `pgvector` evidence lookup.
3. `decision_synthesizer` synthesizes the typed memo from `session.state`.
4. `critic_guardrail` runs as a single-call typed node (zero tokens when
   `CRITIC_MODE=off`, max 1 Critic LLM call when `shadow`/`enforced`, zero
   redundant ADK tokens) with bounded `revise` routing (`MAX_CRITIC_REVISIONS = 1`)
   and a mandatory zero-token post-revision `evaluate_deterministic_objections`
   gate.
