# Debugging agent runs

## Decision tree

- Wrong numbers: inspect the deterministic engine, not the agent layer.
- Wrong deterministic `/screening` or universe-coverage result: start with
  [decision-diagnostics.md](decision-diagnostics.md).
- No agent activity: check `entered_agent_layer` in the trace before blaming the
  model layer.
- Bad memo: enable `DEBUG_LLM_IO` and read the resolved prompts and raw ADK
  events for that `run_id`.
- Tool-not-found errors: check the registry tool list and the
  `transfer_to_agent` instructions before changing prompts.

## Per-request pipeline override

Keep `PIPELINE_MODE=linear` by default, then opt into one-off agent runs locally
with:

```bash
ALLOW_PIPELINE_OVERRIDE=true
```

Restart the stack after changing `.env`. When the flag is off, any
`POST /analyze?pipeline=...` request fails with `403` and
`{"detail":"pipeline_override_disabled"}` instead of silently falling back to
the configured mode.

Use the local helper target to compare deterministic and agentic runs for the
same ticker:

```bash
make analyze ticker=NVDA mode=linear
make analyze ticker=NVDA mode=agentic
```

Every explicit override appends `pipeline_override:<mode>` to `warnings`. The
agentic path can spend LLM quota, so use `mode=agentic` deliberately and only
for single-ticker debugging.

## How to read a trace

The structured trace is described in [agent-trace.md](agent-trace.md). It records
the run ID, event authors, warnings, token usage, and latency without storing
prompts or model output in the database. Start there first: if
`entered_agent_layer` is false, the agent never actually ran and LLM I/O capture
will not explain the failure.

## How to capture LLM I/O

Set `DEBUG_LLM_IO=true` and optionally set `DEBUG_LLM_IO_DIR` (default
`/tmp/forseti-llm-io`). Each run gets a directory named for its trace `run_id`.
Files may contain full prompts and model output, so keep this disabled outside
local debugging and clean up when done:

```bash
rm -rf /tmp/forseti-llm-io
```

Each directory contains:

- `000-run-config.json`: ticker, model, temperature, timeout, retries,
  pipeline mode, optional git SHA, and start time.
- `001-agent-prompts.json`: the resolved instruction text for each agent,
  including `HARD_RULES_TEXT`, tool names, and sub-agent names.
- `002-user-message.json`: the exact `Runner.run(... new_message=...)` text.
- `003-event-NNN.json`: one raw ADK event per file, with normalized author,
  text, function calls, function responses, token usage, and the raw ADK event
  payload.
- `999-summary.json`: event count, total token usage, final decision, warnings,
  and wall-clock timing. Failed runs still write a summary.

Each file is bounded to 1 MiB and truncated with `"truncated": true` when
necessary.

## Replay a failed run

Once `DEBUG_LLM_IO=true` has captured a run, replay it offline with the saved
event files instead of calling Gemini again:

```bash
make replay RUN_ID=<run_id_from_trace>
```

The replay target mounts `DEBUG_LLM_IO_DIR` (default `/tmp/forseti-llm-io`) into
the app container, reads `000-run-config.json`, `002-user-message.json`, the
`003-event-*.json` cassette files, and the expected deterministic snapshot from
`999-summary.json`, then reruns the workflow through the existing
`runner_factory` seam.

- exit code `0`: deterministic fields matched the recorded run
- exit code `1`: replay diverged and prints a field-by-field diff

If replay fails with `CassetteNotFoundError`, double-check the `run_id` and the
capture directory before debugging the workflow itself.

## How to run the ADK dev UI

Run `make adk-web`, then open http://127.0.0.1:8010.

Verified CLI contract (`python -m google.adk.cli web --help` inside the app
container): `web` expects an `AGENTS_DIR` whose subdirectories each contain an
ADK-discoverable `agent.py`, `__init__.py`, or `root_agent.yaml`. Forseti uses
`agents/adk_app/agent.py`, so the Make target runs:

```bash
python -m google.adk.cli web agents --host 0.0.0.0 --port 8010
```

This is a local-development-only UI. It requires the Vertex credentials from
`.env`, shows the real `trade_analyst_supervisor` topology plus live transfers
and tool calls, and does **not** show the structured API trace or intercept raw
Gemini HTTP traffic.

## Known sharp edges

The `retriever` tool is not registered at runtime because `workflow.py` builds
the registry without an embedding client. Specialists are instructed with
`NO_TOOLS_TEXT`; if a model emits a function call anyway, the run records
`agent_narration_degraded`.
