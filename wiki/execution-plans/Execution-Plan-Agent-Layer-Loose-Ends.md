Epic: [#110 Close agent-layer loose ends](https://github.com/ibudasov/forseti/issues/110)

## Goal

Close the four gaps left in the agent layer after Week 9:

- Make agent authority consistent across docs and code.
- Stop paying twice for fundamental analysis.
- Give the Decision Synthesizer a typed, validated contract.
- Store the true as-of date on every run.

## Roadmap

| Step | Issue | What | Size | Depends on |
| --- | --- | --- | --- | --- |
| 1 | [#111](https://github.com/ibudasov/forseti/issues/111) | LE-1: Persist `as_of_date` on AgentRun; use it in backtests and shadow reports | S | — |
| 2 | [#112](https://github.com/ibudasov/forseti/issues/112) | LE-2: ADR 008 agent authority (decide fundamental promotion) and enforce it | M | Owner decision |
| 3 | [#113](https://github.com/ibudasov/forseti/issues/113) | LE-3: Fundamental typed-view agent (remove duplicate LLM call) | M | LE-2 |
| 4 | [#114](https://github.com/ibudasov/forseti/issues/114) | LE-4: Decision Synthesizer typed contract, service and eval harness | L | LE-2, LE-3 |
| 5 | [#115](https://github.com/ibudasov/forseti/issues/115) | LE-5: Integrate typed synthesizer into workflow, revision loop and API | M | LE-4 |

LE-1 is independent and can run in parallel with LE-2.

## Decision needed

- [ ] Choose the fundamental authority option in #112. The recommendation is <b>Option A: downgrade-only</b>, so every agent follows one rule: deterministic rules set the ceiling.

## Key findings

- The synthesizer's prompt asks for plain text, but the workflow parses its output as JSON. Most real runs probably degrade to `unparsable_synthesis`. Measure this first in LE-4.
- The Fundamental agent can currently promote decisions, which contradicts the README and ADRs 006/007.
- Shadow and enforced fundamental modes make two LLM calls per run (typed service + ADK LlmAgent).
- The backtest uses `created_at` as the as-of date, which is a look-ahead risk on replayed runs.

## Done when

- [ ] All five sub-issues closed
- [ ] `make test`, `make trust-consistency`, `make eval-synthesizer` and `make eval-critic` green
- [ ] README, ADR 008/009 and docs consistent with the code

---

_Migrated from Notion on 2026-11-12._
