# Debugging agent runs

## Decision tree

- Wrong numbers: inspect the deterministic engine, not the agent layer.
- No agent activity: check `entered_agent_layer` in the trace (#22).
- Bad memo: enable `DEBUG_LLM_IO` and read the resolved prompts and events.
- Tool-not-found errors: check the registry tool list and the
  `transfer_to_agent` instructions.

## Reading a trace

The structured trace is described in [agent-trace.md](agent-trace.md). It records
the run ID, event authors, warnings, token usage, and latency without storing
prompts or model output in the database.

## Capturing LLM I/O

Set `DEBUG_LLM_IO=true` and optionally set `DEBUG_LLM_IO_DIR` (default
`/tmp/forseti-llm-io`). Each run gets a directory named for its trace run ID:
`000-run-config.json`, `001-agent-prompts.json`, `002-user-message.json`, one
`003-event-NNN.json` per ADK event, and `999-summary.json`. Files are bounded to
1 MiB. They can contain full prompts and model output; keep capture local and
remove directories when finished (`rm -rf /tmp/forseti-llm-io`).

## ADK development UI

Run `make adk-web`, then open http://127.0.0.1:8010. This invokes
`python -m google.adk.cli web agents --host 0.0.0.0 --port 8010` in Docker and
requires Vertex credentials in `.env`. The UI shows the configured agent
topology and live ADK activity; it does not show the structured API trace or
intercept raw Gemini HTTP traffic.

## Known sharp edges

The `retriever` tool is not registered at runtime because `workflow.py` builds
the registry without an embedding client. Specialists are instructed with
`NO_TOOLS_TEXT`; if a model emits a function call anyway, the run records
`agent_narration_degraded`.
