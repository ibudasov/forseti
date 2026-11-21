Goal: show with evidence that the full ADK workflow (Input Resolver → Structured Data Collector → Retriever → Fundamental Analyst → Technical Analyst → Risk Manager → Decision Synthesizer → Critic) produces recommendations that are grounded, consistent, risk-compliant and safely conservative before moving on to deployment.

> 🎯 Definition of done: a repeatable evaluation run on golden cases plus real traces shows no risk-rule violations, no unsupported claims in memos, correct no-trade behaviour under uncertainty, and a Critic that has run in shadow mode with reviewed objections.

Tracked in GitHub: [Epic #92](https://github.com/ibudasov/forseti/issues/92) → [#93](https://github.com/ibudasov/forseti/issues/93) Baseline · [#94](https://github.com/ibudasov/forseti/issues/94) E2E runs · [#95](https://github.com/ibudasov/forseti/issues/95) Critic shadow · [#96](https://github.com/ibudasov/forseti/issues/96) Grounding · [#97](https://github.com/ibudasov/forseti/issues/97) Robustness · [#98](https://github.com/ibudasov/forseti/issues/98) CI + trust report

## Phase 1 — Baseline and tracker cleanup

- [ ] Run the full test suite and the offline Critic evaluation on current `main`; record pass/fail as the baseline
- [ ] Reconcile Critic issues #82–#90 with the merged code: close what passes acceptance, comment on gaps for the rest
- [ ] Update the checkboxes in Execution Plan: Agentic Workflow with ADK (Weeks 7–8) so it reflects reality (e.g. #20 is closed)
- [ ] Write down the trust criteria as measurable checks (risk compliance, grounding, consistency, no-trade under uncertainty, latency/cost budget)

<b>Exit:</b> one baseline report and an accurate issue tracker.

## Phase 2 — End-to-end runs on real tickers

- [ ] Pick 5–8 tickers across target sectors covering: clear trade, clear no-trade, ambiguous, incomplete data
- [ ] Run a full agentic analysis for each and inspect the persisted trace step by step
- [ ] Verify every step ran in the expected `SequentialAgent` order and no step was silently skipped
- [ ] Confirm stop-loss, take-profit and position size in the final output equal the deterministic Risk Manager output exactly
- [ ] Note every surprising result as a candidate golden case

<b>Exit:</b> traces reviewed for all selected tickers; findings logged as issues.

## Phase 3 — Critic shadow rollout

- [ ] Enable the Critic in shadow mode (recommendation unchanged, objections recorded)
- [ ] Run shadow mode over the Phase 2 tickers and the golden cases
- [ ] Review each objection: true positive, false positive, or missed problem
- [ ] Verify the downgrade-only policy: Critic can never upgrade a decision or raise confidence
- [ ] Verify the single bounded revision terminates and is persisted in the trace
- [ ] Run the Critic at least once against live Gemini — `make eval-critic-live` only replays frozen fixtures
- [ ] Decide go / no-go for enabling the Critic by default, with written rationale

<b>Exit:</b> objection review table and an explicit enable decision.

## Phase 4 — Grounding and hallucination checks

- [ ] For each Decision Synthesizer memo, check that every factual claim maps to retrieved evidence or deterministic data
- [ ] Check the Fundamental Analyst citations point to real retrieved chunks
- [ ] Test weak / empty retrieval: confidence must drop and warnings must appear
- [ ] Test contradictory evidence: Critic or synthesizer must surface the contradiction

<b>Exit:</b> zero unsupported claims in the reviewed set, or issues filed for each one.

## Phase 5 — Consistency and robustness

- [ ] Run the same ticker several times with identical inputs; compare decision, confidence and reasons
- [ ] Define an acceptable variance threshold for confidence and decision flips
- [ ] Inject failures (data provider down, stale data, model timeout, malformed LLM output) and confirm safe fallback to no-trade / neutral
- [ ] Confirm data-freshness and completeness gates block recommendations on stale or missing data

<b>Exit:</b> repeat-run variance within threshold; every injected failure ends in a safe output.

## Phase 6 — Lock it in as regression

- [ ] Add new golden cases from Phases 2–5 to the evaluation suite
- [ ] Add trajectory assertions for step order and tool usage
- [ ] Run the evaluation suite automatically in CI and fail on risk-rule violations
- [ ] Track latency, token usage and cost per recommendation per run
- [ ] Write a short trust report: what was tested, results, known limitations

<b>Exit:</b> CI-enforced evaluation suite and a trust report.

## Follow-up candidates (after this plan)

- Technical Analyst hardening with a typed contract like the Fundamental Analyst
- Week 10 GCP deployment readiness check

---

_Migrated from Notion on 2026-11-12._
