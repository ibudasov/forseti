# Agent trace semantics

`POST /analyze?include_trace=true` and `GET /runs/{run_id}` return an `AnalysisTrace` that records what actually happened during the run.

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

## Output truncation

Any string nested in `TraceStep.output` is truncated to 20,000 chars and suffixed with `…[truncated]`.

## `fundamental_analyst` step

When `FUNDAMENTAL_AGENT_MODE` is `off`, the `fundamental_analyst` ADK slot is
the legacy narrative `LlmAgent`.

When the mode is `shadow` or `enforced`, the workflow calls the Fundamental
Analyst once before ADK to produce the validated assessment used by policy.
The ADK slot is then a deterministic typed view (`TypedAnalystViewAgent`),
not another model call:

- `output.source == "typed_assessment"` and the remaining fields mirror the
  validated result, including cited `metric_ids` and `chunk_ids`.
- The typed-view event has `token_usage == {}`. The earlier assessment trace
  records the one Fundamental model call and its usage.
- The Synthesizer and Critic read this validated view from `fundamental_view`.

## `technical_analyst` step

When `TECHNICAL_AGENT_MODE` is `off`, the `technical_analyst` step is a
narrative ADK `LlmAgent` turn like any other specialist: `output` is the raw
`{"text": ...}` fallback and `token_usage` reflects the model call.

When `TECHNICAL_AGENT_MODE` is `shadow` or `enforced`, the step is produced by
the deterministic `TypedAnalystViewAgent`
(`agents/orchestration/typed_view_agent.py`) instead of a second model call:

- `output.source == "typed_assessment"`, and the remaining `output` fields
  (`status`, `overall_signal`, `trend_state`, `momentum_state`,
  `proposed_score_adjustment`, `findings`, `summary`) mirror the validated
  `TechnicalAssessmentResult` from TA-4.
- `token_usage` is always `{}` — no model call is made.
- `SPECIALIST_ORDER` and `observed_agents` are unchanged; the agent still
  emits exactly one event authored `technical_analyst`.

See [`docs/technical-analyst.md`](technical-analyst.md) for how this typed
view also reaches the Decision Synthesizer and the Critic.

## `critic_guardrail` step

Agentic runs always include a `critic_guardrail` step after `decision_synthesizer`.

- `status=completed` — critique ran and passed validation
- `status=degraded` — critique ran but the validation or fallback path degraded
- `status=skipped` — critique was intentionally bypassed; `skip_reason` and `output.reason` explain why

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

If the critic requests and receives one bounded revision, the `decision_synthesizer` step keeps the original sequence position and exposes `retries=1`.

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
