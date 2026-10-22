# Trust report

- Scope: issue #98 for epic #92; lock offline trust regressions into CI, report runtime telemetry, and summarize current evidence.
- Verified snapshot SHA: `f9c762888a045e48b74935ab71221774d7773e80`
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

## Technical Analyst shadow status (TA-7, issue #108)

| Criterion | Threshold | Current status |
| --- | --- | --- |
| Offline golden suite | All TA-6 cases pass in CI | **Met** — `make eval-technical-agent` passes, 0 failures |
| 4 trust invariants implemented and unit-tested offline | `technical_never_upgrades`, `technical_risk_fields_untouched_in_shadow`, `technical_numeric_claims_grounded`, `technical_step_zero_tokens_in_typed_mode` | **Met** — all 4 implemented in `app/trust/invariants.py`, wired into `scripts/trust_e2e.py`'s per-run report, 19/19 offline unit tests passing in `tests/test_trust_invariants.py` |
| Usefulness backtest tooling | Pure, offline, replays persisted effects against stored bars, no model calls | **Met** — `scripts/technical_backtest.py`, 9/9 offline unit tests passing on synthetic bars in `tests/test_technical_backtest.py` |
| Shadow sample size | >=100 shadow runs across >=5 sectors | Blocked here; `0/100` runs collected in this sandbox (no GCP ADC, no ingested market data) |
| Invariant violations on real data | 0 upgrades, 0 changed risk numbers, 0 ungrounded numeric claims | Not yet evaluable without a real shadow sample |
| Manual review of sampled downgrades | >=80% judged justified | Not yet evaluable; `reports/trust/technical-shadow-sample.csv` export tooling exists but has no rows to label here |
| Usefulness (forward-return comparison) | Downgraded-run mean forward 20-day return below neutral-run mean | Not yet evaluable; `make technical-backtest` runs cleanly but both cohorts have `sample_size=0` here |
| Stable window | 14 consecutive days | Not started |
| Human sign-off | Required | Not started |

This mirrors the exact environment blocker already documented for the
Fundamental Analyst and Critic above: this sandbox has no usable GCP
Application Default Credentials and no ingested structured market data, so
`make technical-shadow-batch CONFIRM_COST=yes` cannot collect real shadow
runs here. See `docs/trust/technical-shadow.md` for the full evidence table,
the exact commands required to finish TA-7 once ADC and ingested data are
available, and the reasoning behind leaving every data-dependent criterion
explicitly `pending` rather than fabricated. The `TECHNICAL_AGENT_MODE` code
default remains `off`.

## Cost and latency

- Persisted traces already keep per-step `latency_ms` plus per-step `prompt_token_count` / `candidates_token_count` / `total_token_count`.
- `scripts/trust_e2e.py` now logs per-run `total_latency_ms`, aggregate token usage, and `estimated_cost_usd`, and summarizes runtime as p50/p95 latency plus mean/p95/total estimated cost across completed agentic runs.
- Pricing is centralized in `Settings.MODEL_PRICING_USD_PER_MILLION` instead of being hard-coded inline.
- In this sandbox, live trust runs are still blocked before model completion, so there is **no trustworthy live p50/p95 or mean-cost sample yet**.

## Known limitations

- Issue #95 (Trust 3/6) is only **partially verified** here: live critic gate/shadow collection still fails with missing GCP Application Default Credentials, so the shadow sample and promotion thresholds are not yet proven.
- Issue #97 (Trust 5/6) is only **partially verified** here: repeat-run variance measurement still fails for the same ADC blocker, so no real flip-rate or confidence-variance numbers are claimed from this environment.
- Issue #108 (TA-7) is only **partially verified** here: the 4 new technical-agent trust invariants and the offline usefulness-backtest module are implemented and unit-tested, but live shadow-run collection fails for the same ADC/ingestion blocker, so no real shadow sample, labelled-review summary, or go/no-go decision is claimed from this environment. See `docs/trust/technical-shadow.md`.
- The local database still lacks populated structured market/fundamental/technical/earnings/RAG data beyond the seeded universe rows, which keeps live linear runs in the `no_price_data` path.
- This report therefore does **not** claim epic #92 is complete.

## Recommended next steps

1. Re-run Trust 3/6 shadow collection and Trust 5/6 repeatability checks in an environment with working GCP ADC and populated market data before treating epic #92 as fully proven.
2. Expand the sanitized workflow-fixture set whenever future manual trust reviews uncover a new specialist-order or tool-usage regression.
3. Keep the Critic in `shadow` mode until the promotion criteria in `docs/trust/criteria.md` are met and a human signs off.
4. Complete the remaining roadmap items called out in prior trust docs, including the Technical Analyst typed-contract hardening and the planned Week 10 deployment verification work.
5. Run `make technical-shadow-batch CONFIRM_COST=yes REPEAT=<N>` in an environment with GCP ADC and ingested market data to collect >=100 Technical Analyst shadow runs, then follow the step-by-step list in `docs/trust/technical-shadow.md` to finish TA-7 and record a real go/no-go.
