# ADR 007 — Technical Analyst authority is downgrade-only

## Status

Accepted.

## Context

The Fundamental Analyst already has a deterministic baseline contract
(`DeterministicFundamentalAnalysis`, `docs/fundamental-analysis-contracts.md`)
and a policy that can apply its structured assessment to the pipeline
response. The Critic/Guardrail followed the same shape (ADR 006): deterministic
rules bind and fail closed, the model advises and fails open, and the model's
authority over the response is bounded.

The Technical Analyst is the next specialist in the roadmap. Today the
technical side of the checklist (rules 5-9: `close_vs_sma50`,
`close_vs_sma200`, `rsi_healthy`, `volume_trend`, `vix_calm`) only returns
`ChecklistResult` for rules that passed; failed and unknown rules are silently
dropped, and there is no stable `metric_id` a model-based specialist could
cite when discussing the evidence. Before any Technical Analyst prompt is
wired into the workflow, the deterministic baseline it will reason over must
be an explicit, versioned contract, and its authority must be decided in
advance, the same way it was for the Critic.

## Decision

Introduce `DeterministicTechnicalAnalysis` (`app/domain/technicals.py`) as a
pure, versioned contract that the checklist rules 5-9 delegate to, with zero
behaviour change to scores, decisions, or `detail` strings (TA-1, this issue).
A future Technical Analyst agent will consume this contract the same way the
Fundamental Analyst consumes `DeterministicFundamentalAnalysis`, and its
authority is fixed now, before that agent exists:

- deterministic technical rules and hard vetoes remain authoritative; the
  Technical Analyst never computes indicators, re-decides vetoes, or recomputes
  the checklist score
- the Technical Analyst is **downgrade-only**: it may apply a confidence
  penalty bounded to `[-2, 0]`, the same bound and mechanism already used for
  the Critic's confidence penalty, and may move the decision one step down the
  `trade -> watchlist -> no_trade` ladder, never up
- the Technical Analyst never emits trade, risk, or confidence *target*
  fields — only a bounded penalty and a decision floor, mirroring the Critic's
  schema constraint that an upgrade is intentionally unrepresentable
- `TechnicalVetoState` in the new contract is an informational mirror of the
  analyzer's existing veto identifiers (`rsi_overbought`, `vix_panic_regime`,
  `earnings_too_close`, `deep_below_sma200`); the Technical Analyst can read
  these but never re-triggers or suppresses a veto
- the agent ships behind a three-way `TECHNICAL_MODE` setting: `off` (default),
  `shadow` (structured, persisted counterfactuals only, no effect on the
  response), and `enforced` (bounded downgrade applied), following the same
  rollout discipline as `CRITIC_MODE`
- the workflow position is unchanged by this decision: Deterministic Pipeline
  -> Fundamental Analyst -> Technical Analyst -> Decision Synthesizer ->
  Critic/Guardrail, each specialist bounded in the same direction

This ADR covers authority only. Wiring `TECHNICAL_MODE`, the Technical Analyst
prompt, and its policy function is out of scope for TA-1 and lands in a later
issue.

## Consequences

- The Technical Analyst can flag RSI divergences, volume anomalies, or
  trend contradictions and penalize confidence or cap the decision, without
  becoming able to invent a buy signal the deterministic engine did not award.
- `app/domain/technicals.py` gives any future prompt stable `metric_id`s
  (`<metric_name>:<as_of_date>`) to cite, the same way the Fundamental Analyst
  cites `<metric>:<period-end>`.
- Because the contract is introduced with zero behaviour change (TA-1), this
  ADR can be accepted and merged without touching the agent workflow, keeping
  the sequential epic plan (contract first, agent wiring later) intact.
- A later issue must still decide whether `TechnicalVetoState` should be
  populated from `app/services/vetoes.py` at call time, since `analyze_technicals`
  does not have access to `next_earnings` and does not recompute vetoes itself;
  until then `veto_states` is populated only when the caller supplies it, and is
  empty from `evaluate_checklist`.

## Resolved owner question: Fundamental authority

At acceptance, the Fundamental policy could promote decisions in enforced
mode, contrary to the README's downgrade-only guarantee. The owner selected
Option A in LE-2: [ADR 008](008-agent-authority.md) makes Fundamental
downgrade-only too, fixes its maximum adjustment at `0`, and removes the
promotion/risk-recompute path. There is no exception to the shared ceiling.

## Rejected alternatives

### Let the Technical Analyst recompute indicators or vetoes

Rejected for the same reason as the Critic: a specialist that can silently
diverge from the deterministic indicator math would make the baseline
unauditable and would duplicate logic that already lives in
`app/ingestion/features.py` and `app/services/vetoes.py`.

### Give the Technical Analyst the same promotion power observed in the Fundamental Analyst's enforced mode

Rejected because it would compound the exact inconsistency flagged above
instead of resolving it, and because every other bounded authority in this
codebase (Critic, and now Technical Analyst) is downgrade-only by design.

### Skip the contract and have the Technical Analyst read raw ORM rows

Rejected because the Fundamental Analyst precedent shows a pure, versioned
contract is what keeps the specialist testable without a database and gives
the model stable `metric_id`s to cite instead of free-floating numbers.

## Addendum (TA-5): the narrative ADK step is removed in typed mode

When `TECHNICAL_AGENT_MODE != off`, the ADK spine's `technical_analyst` slot
is no longer a narrative `LlmAgent` making its own, second interpretation of
the indicators. It is filled by the deterministic
`agents/orchestration/typed_view_agent.py::TypedTechnicalViewAgent`, which
renders the already-validated `TechnicalAssessmentResult` from this ADR's
service into the `technical_view` session-state key and makes no model or
tool call.

This closes a gap introduced between TA-4 and TA-5: before this addendum, a
run with `TECHNICAL_AGENT_MODE` enabled would compute the typed assessment
*and* still run the narrative LLM agent, meaning the Decision Synthesizer and
Critic could see two independent, potentially disagreeing technical
interpretations per run, and only one of them (the narrative one) actually
reached them. TA-5 makes the validated, downgrade-only assessment the single
source of technical interpretation downstream, consistent with the authority
limits this ADR already establishes. The narrative `LlmAgent` path is kept,
unchanged, for `TECHNICAL_AGENT_MODE=off` (the default) until a future issue
promotes typed mode to the only mode.
