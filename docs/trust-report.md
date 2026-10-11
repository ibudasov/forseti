# Trust report

- Scope: issue #98 for epic #92; lock offline trust regressions into CI, report runtime telemetry, and summarize current evidence.
- Commit SHA: `78668c572e42306fcafccf59efaba06bab459a3e`
- Branch: `copilot/ibudasov-92-parent-session`

## Invariant results

| Area | Status | Evidence |
| --- | --- | --- |
| Trust 2/6 invariant checker | Pass offline; live matrix blocked | `app/trust/invariants.py`, `tests/test_trust_invariants.py`, `docs/trust/e2e-review.md` |
| Offline trajectory guard | Pass offline | Sanitized replay fixture `tests/fixtures/trust/clear_trade_trajectory.json` plus `tests/agents/test_recorded_trajectory.py` enforce `SPECIALIST_ORDER`, allowed tool calls, and no `transfer_to_agent` in the sanitized specialist trace |
| CI regression gate | Pass offline | `.github/workflows/test.yml` now runs both `make test` and `make eval-critic` |

## Grounding results

- Trust 4/6 found **no unsupported claims** in the persisted offline fixtures available in this sandbox.
- Supporting evidence: `docs/trust/grounding.md`, `app/trust/memo_checks.py`, `tests/test_memo_checks.py`.
- Caveat: no successful live Trust 2/6 memo was available here because Vertex/Gemini authentication is still blocked.

## Consistency and robustness results

| Area | Status | Evidence |
| --- | --- | --- |
| Failure-injection suite | Pass offline | `tests/trust/test_failure_injection.py` covers provider/schema/timeout degradation paths and keeps deterministic risk numbers intact |
| Repeat-run consistency tooling | Implemented, not verified live here | `scripts/trust_e2e.py --repeat`, `make trust-consistency`, `docs/trust/robustness.md` |
| New frozen regression from trust review | Added | `tests/fixtures/critic/no_price_data_watchlist_cap.json` freezes the `no_price_data` hard-blocker cap observed in `docs/trust/e2e-review.md` |

## Critic shadow status vs. promotion criteria

| Criterion | Threshold | Current status |
| --- | --- | --- |
| Frozen offline suite | 12/12+ cases passing | Passing offline; CI now re-runs `make eval-critic` |
| Shadow sample size | >=100 successful shadow runs | Blocked here; `0/100` successful runs collected in this sandbox |
| Decision upgrades | 0 | Passing in offline fixtures/failure injection; live shadow evidence still missing |
| Risk-number equality | 100% | Passing in offline fixtures/failure injection; live shadow evidence still missing |
| False-objection review | <=5% | Not yet evaluable without shadow sample |
| Stable window | 14 consecutive days | Not started |
| Human sign-off | Required | Not started |

## Cost and latency

- Persisted traces already keep per-step `latency_ms` plus per-step `prompt_token_count` / `candidates_token_count` / `total_token_count`.
- `scripts/trust_e2e.py` now logs per-run `total_latency_ms`, aggregate token usage, and `estimated_cost_usd`, and summarizes runtime as p50/p95 latency plus mean/p95/total estimated cost across completed agentic runs.
- Pricing is centralized in `Settings.MODEL_PRICING_USD_PER_MILLION` instead of being hard-coded inline.
- In this sandbox, live trust runs are still blocked before model completion, so there is **no trustworthy live p50/p95 or mean-cost sample yet**.

## Known limitations

- Issue #95 (Trust 3/6) is only **partially verified** here: live critic gate/shadow collection still fails with missing GCP Application Default Credentials, so the shadow sample and promotion thresholds are not yet proven.
- Issue #97 (Trust 5/6) is only **partially verified** here: repeat-run variance measurement still fails for the same ADC blocker, so no real flip-rate or confidence-variance numbers are claimed from this environment.
- The local database still lacks populated structured market/fundamental/technical/earnings/RAG data beyond the seeded universe rows, which keeps live linear runs in the `no_price_data` path.
- This report therefore does **not** claim epic #92 is complete.

## Recommended next steps

1. Re-run Trust 3/6 shadow collection and Trust 5/6 repeatability checks in an environment with working GCP ADC and populated market data before treating epic #92 as fully proven.
2. Expand the sanitized workflow-fixture set whenever future manual trust reviews uncover a new specialist-order or tool-usage regression.
3. Keep the Critic in `shadow` mode until the promotion criteria in `docs/trust/criteria.md` are met and a human signs off.
4. Complete the remaining roadmap items called out in prior trust docs, including the Technical Analyst typed-contract hardening and the planned Week 10 deployment verification work.
