# Agent trace semantics

`POST /analyze?include_trace=true` and `GET /runs/{run_id}` return an `AnalysisTrace` that records what actually happened during the run.
When Cloud Trace export is enabled, `AnalysisTrace.trace_id` is the OpenTelemetry
trace identifier that links the persisted run to its request span. It is nullable
for runs created without an active tracing provider.

## Step statuses

`TraceStep.status` is one of:

- `completed` — step/event was observed and succeeded
- `degraded` — step/event was observed but produced a degraded result
- `failed` — step failed and run crashed or could not continue
- `skipped` — step did not happen

For `failed` and `skipped`, `TraceStep.output.reason` is always present.

## Reason codes

- `retriever_tool_unavailable`
- `agent_narration_degraded`
- `unparsable_synthesis`
- `guardrail_rejected`
- `adk_runner_exception`
- `fundamental_analyst_never_reached`
- `technical_analyst_never_reached`
- `decision_synthesizer_never_reached`
- `critic_never_reached`
- `critic_mode_off`

## Trace-level observability fields

- `entered_agent_layer` — `true` only when at least one ADK event was observed
- `adk_event_count` — number of observed ADK events
- `observed_agents` — ADK event authors in first-seen order
- `fundamental_agent_effect` — persisted summary of the fundamental analyst guardrail outcome
- `technical_agent_effect` — persisted summary of the technical analyst guardrail outcome; always
  downgrade-only relative to the post-fundamental decision it was computed against (see
  [`docs/critic-guardrail.md`](critic-guardrail.md) for the full composition order)
- `critic_effect` — persisted summary of the critic guardrail outcome, including objections, decisions, and revisions
- `synthesizer_effect` — typed Decision Synthesizer outcome (contract, prompt version, fallback used, proposed vs
  final decision, validation failures, memo hash); omitted in legacy mode. In typed mode the
  `decision_synthesizer` step has `output.source = "typed_synthesis"` and zero tokens

## Output truncation

Any string nested in `TraceStep.output` is truncated to 20,000 chars and suffixed with `…[truncated]`.

## `fundamental_analyst` step

When `FUNDAMENTAL_AGENT_MODE` is `off`, the `fundamental_analyst` ADK slot is
the legacy narrative `LlmAgent` (or a no-op blackboard node when the unified
typed blackboard spine is active).

When the mode is `shadow` or `enforced`, the Fundamental Analyst executes as a
typed ADK `BaseAgent` blackboard node (`BlackboardFundamentalAgent`, or
`TypedAnalystViewAgent` in legacy view mode) that runs one validated assessment
and writes typed artifacts (`fundamental_request`, `fundamental_assessment`,
`fundamental_view`) to `session.state`:

- When both `FUNDAMENTAL_AGENT_MODE` and `TECHNICAL_AGENT_MODE` are enabled,
  the ADK spine fans out `fundamental_analyst` and `technical_analyst`
  concurrently inside `specialist_fan_out` (`ParallelAgent`), then folds
  policies sequentially in `specialist_policy_fold`
  (`Rules -> Fundamental -> Technical`) so `fundamental_agent_effect`,
  `technical_agent_effect`, `context_hash`, and `SPECIALIST_ORDER` trace steps
  remain deterministic regardless of completion order.
- `output.source == "typed_assessment"` and the remaining fields mirror the
  validated result, including cited `metric_ids` and `chunk_ids`.
- The typed-view event has `token_usage == {}`. The assessment trace records
  the single Fundamental model call and its usage.
- Downstream nodes (`cross_domain_tension`, `decision_synthesizer`, and
  `critic_guardrail`) read this validated view from `session.state`.

## `technical_analyst` step

When `TECHNICAL_AGENT_MODE` is `off`, the `technical_analyst` step is a
narrative ADK `LlmAgent` turn in legacy mode (or a no-op blackboard node in
blackboard mode).

When `TECHNICAL_AGENT_MODE` is `shadow` or `enforced`, the step is produced by
the typed ADK `BaseAgent` node (`BlackboardTechnicalAgent` or
`TypedAnalystViewAgent`) instead of a duplicate model call:

- When both specialists are enabled, `technical_analyst` builds its context and
  runs its assessment concurrently with `fundamental_analyst` under
  `specialist_fan_out`, while policy folding remains strictly sequential after
  the join.
- `output.source == "typed_assessment"`, and the remaining `output` fields
  (`status`, `overall_signal`, `trend_state`, `momentum_state`,
  `proposed_score_adjustment`, `findings`, `summary`) mirror the validated
  `TechnicalAssessmentResult` from TA-4.
- `token_usage` on the typed-view event is always `{}` — no duplicate model call
  is made.
- `SPECIALIST_ORDER` and `observed_agents` remain deterministic; the agent
  emits its structured view to `technical_view` on `session.state`.

See [`docs/technical-analyst.md`](technical-analyst.md) for how this typed
view also reaches `cross_domain_tension`, the Decision Synthesizer, and the
Critic.

## `cross_domain_tension` step

When both Fundamental and Technical assessments complete and conflict (opposite
polarity, `signal_conflict`, or a high-materiality technical `risk_flag`
against a positive fundamental view), the `cross_domain_tension` blackboard
node emits a typed `CrossDomainTension` artifact on `session.state` and runs at
most one point-in-time-safe (`published_at <= snapshot_at`) `pgvector` evidence
lookup, deduplicated against `FundamentalAnalysisRequest.evidence_chunks`. Both
the tension object and supplemental chunks flow into `decision_synthesizer` and
`critic_guardrail` without altering ADR 002 risk math or ADR 008 ceilings.

## `critic_guardrail` step

Agentic runs always include a `critic_guardrail` step after `decision_synthesizer`.
The ADK slot is backed by a typed `BaseAgent` (`BlackboardCriticAgent` /
`TypedCriticViewAgent`) rather than a duplicate narrative `LlmAgent`:

- `status=completed` — critique ran and passed validation
- `status=degraded` — critique ran but the validation or fallback path degraded
- `status=skipped` — critique was intentionally bypassed (`CRITIC_MODE=off`
  records `reason="critic_mode_off"` with zero LLM calls and `token_usage={}`)

In `shadow` or `enforced` mode, at most one `Critic().review()` LLM call
executes per run, and the ADK view step carries zero redundant ADK tokens
(`adk_token_usage == {}`), enforced by `check_critic_step_zero_redundant_tokens`
in `app/trust/invariants.py`.

For schema validation failures, the trace `detail` includes sanitized field paths
and validation error types (for example, `verdict:literal_error`). It does not
include the model's raw response.

Known skip reasons:

- `critic_mode_off`
- `critic_never_reached`

The step `output` is a compact summary of the critic result:

- `verdict`
- `objection_counts_by_severity`
- `baseline_decision`
- `final_decision`
- `accepted`
- `reason_codes`

If the critic requests and receives one bounded revision (`MAX_CRITIC_REVISIONS = 1`),
structured `revision_objection_ids` / `revision_targets` are routed to the
Decision Synthesizer, the `decision_synthesizer` step keeps the original
sequence position and exposes `retries=1`, and the revised memo is re-verified
via zero-token `evaluate_deterministic_objections` before finalizing (no second
Critic LLM call).

## SQL quick check

```sql
SELECT sequence, agent_name, status, tool_calls, output->>'reason' AS reason
FROM agent_run_steps
WHERE run_id = '<run_id>'
ORDER BY sequence;

SELECT entered_agent_layer, adk_event_count, observed_agents
FROM agent_runs
WHERE run_id = '<run_id>';
```
