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

## Trace-level observability fields

- `entered_agent_layer` — `true` only when at least one ADK event was observed
- `adk_event_count` — number of observed ADK events
- `observed_agents` — ADK event authors in first-seen order

## Output truncation

Any string nested in `TraceStep.output` is truncated to 2000 chars and suffixed with `…[truncated]`.

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
