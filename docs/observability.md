# Observability

## Signals

- **Cloud Logging** stores structured application logs. Locally, logs remain
  plain text; set `LOG_FORMAT=json` to use the cloud payload format.
- **Cloud Trace** receives request, analysis, ADK agent/model, SQLAlchemy, and
  outbound `requests` spans when `OTEL_TRACES_EXPORTER=gcp`. Tracing setup is a
  no-op when no exporter is selected.
- **Cloud Monitoring** contains the `Forseti` dashboard, log-based analysis
  metrics, and alert policies. The dashboard covers Cloud Run, Cloud SQL, job
  executions, decisions, cost, latency, confidence, agent degradation, and
  downgrades.

Prompt, response, and tool content capture is disabled in the cloud runtime
through ADK's `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false` and
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT` settings.

## From an alert to an AgentRun

1. Open the alert in **Monitoring → Alerting** and follow its linked metric or
   log filter. Search Cloud Logging for the matching `run_id` and ticker.
2. Open the log entry's trace link in **Trace Explorer**. The API request span
   contains `forseti.run_id`, `ticker`, `forseti.pipeline`, agent modes, final
   decision, and degraded-state attributes. The `forseti.analysis` child span
   groups the workflow's agent, model, SQL, and outbound HTTP spans.
3. Use the `run_id` to inspect `GET /runs/{run_id}` or query
   `agent_runs` and `agent_run_steps`. The response includes the OpenTelemetry
   `trace_id`; it is distinct from the `run_id` and links the persisted record
   to Cloud Trace. Older runs can have a null `trace_id`.

The `analysis_completed` structured event is emitted once per completed run.
Its `cost_usd`, cost status, and `pricing_version` come from the trace cost
estimate persisted on the same `agent_runs` row; observability does not price
usage a second time. Agent degradation and downgrade details are included in
that event, with separate per-agent events for the corresponding log-based
metrics.

## Metrics and alerts

Log-based metric names use the `forseti/` prefix. `analysis_count` has decision,
pipeline, and cost-status labels; latency, cost, and confidence are
distributions. Degradation metrics label agent and reason, while downgrade
metrics label the agent. `daily_refresh_status` is defined for the refresh workflow added by GCP-5 and
distinguishes success from failure.

The email alert channel notifies the configured owner for refresh failures or
missing refreshes, API 5xx responses, Cloud SQL disk/connections thresholds,
daily LLM cost over the configured USD threshold, and runs without priced
model usage. The 26-hour absence condition evaluates continuously; GCP-5 must
provide a non-trading-day success heartbeat if refreshes do not run on weekends
to avoid a weekend absence alert.

For detailed persisted step semantics and SQL checks, see
[`agent-trace.md`](agent-trace.md).
