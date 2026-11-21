Goal: turn the Technical Analyst from a free-text ADK agent into a typed, validated, policy-bounded specialist, mirroring the Fundamental Analyst pipeline. Tracked in [Epic #101](https://github.com/ibudasov/forseti/issues/101).

> 📐 Authority: downgrade-only. Adjustment range \[-2, 0\]. The model never computes indicators, only cites deterministic facts by metric\_id. Default mode off.

## Why

- No contract: output is free text and can't be validated or tested
- The Critic never sees a technical view (always "absent")
- It can misquote RSI/SMA values undetected

## Target pipeline

```text
bars + features + VIX + earnings
  → DeterministicTechnicalAnalysis
  → TechnicalAnalysisRequest (hashed, derived facts)
  → TechnicalAnalyst.assess() (Gemini JSON, validation, fallback)
  → apply_technical_policy() (downgrade-only, off|shadow|enforced)
  → TechnicalAgentEffect → Critic + Decision Synthesizer
```

## Roadmap

| Step | Issue | Delivers | Depends on |
| --- | --- | --- | --- |
| TA-1 | [#102](https://github.com/ibudasov/forseti/issues/102) | Deterministic technical baseline contract + ADR 007 | — |
| TA-2 | [#103](https://github.com/ibudasov/forseti/issues/103) | Context builder with \~25 derived, citable facts | TA-1 |
| TA-3 | [#104](https://github.com/ibudasov/forseti/issues/104) | Assessment schema + LLM service, numeric-claim validation | TA-2 |
| TA-4 | [#105](https://github.com/ibudasov/forseti/issues/105) | Policy, TECHNICAL\_AGENT\_MODE, migration, API + trace | TA-3 |
| TA-5 | [#106](https://github.com/ibudasov/forseti/issues/106) | Typed view to Critic/Synthesizer, no duplicate LLM call | TA-4 |
| TA-6 | [#107](https://github.com/ibudasov/forseti/issues/107) | Golden cases, offline/live eval, CI gate | TA-3/TA-4 |
| TA-7 | [#108](https://github.com/ibudasov/forseti/issues/108) | Shadow rollout, backtest, promotion go/no-go | TA-5, TA-6 |

## Open points

- Fundamental policy can currently promote decisions in enforced mode, which conflicts with the README "downgrade-only" rule; documented in TA-1, not changed
- TA-6 live and TA-7 need working GCP ADC and ingested market data (blocker carried over from the trust epic)

---

_Migrated from Notion on 2026-11-12._
