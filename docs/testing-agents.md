# Testing agents

Agent behavior is tested in five layers:

| Layer | Catches |
| --- | --- |
| Tool adapters | Wiring, schemas, and deterministic tool results |
| Single agent | Prompt boundaries and one-agent behavior with a stubbed model |
| Trajectories | Transfer order, tool calls, failures, and guardrails |
| Golden cases | Regressions against recorded, representative runs |
| Live evaluation | Model quality and behavior against changing services |

Trajectory tests stay offline by replacing the ADK runner with a script:

```python
script = [
    Turn("trade_analyst_supervisor", ("structured_data_collector",)),
    Turn("decision_synthesizer", ("calculate_risk",)),
    Turn("critic_guardrail"),
]
workflow = AgenticAnalysisWorkflow(
    config,
    engine=engine,
    runner_factory=scripted_runner(script),
)
response = workflow.analyze("NVDA")
assert_steps(response.trace, [
    ("trade_analyst_supervisor", "completed"),
    ("decision_synthesizer", "completed"),
    ("critic_guardrail", "completed"),
])
```

Assert trajectory and deterministic numbers, not model prose. The scripted runner
does not make network calls or require credentials.
