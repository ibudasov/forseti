# Critic shadow rollout review

- Issue: #95
- Branch: `copilot/ibudasov-92-parent-session`
- Reviewed on: 2026-10-11 UTC
- Live gate: `make eval-critic-live CONFIRM_COST=yes`
- Shadow batch: `make critic-shadow-batch CONFIRM_COST=yes REPEAT=1`

## Local environment reality check

- `VERTEX_AI_PROJECT` is configured in `.env`, so the live gate now reaches the real `Critic`/Vertex code path.
- This sandbox still lacks usable ADC credentials: `/root/.config/gcloud/application_default_credentials.json` exists as a **directory**, not a readable JSON file.
- The local database still has only the 35 seeded universe rows and zero structured price/fundamental/technical/earnings/RAG records, so linear runs remain `no_trade` with `no_price_data`.

## Command results

| Command | Result |
| --- | --- |
| `make eval-critic` | Passed. Offline suite stayed green with 12/12 frozen cases passing. |
| `make critic-shadow-report` | Passed. Shadow report currently shows `run_count=0`, `objection_rate=0.0`, `decision_change_rate=0.0`, severity counts all `0`, deterministic/model objection counts `0/0`. |
| `make critic-shadow-report SAMPLE=20 OUT=reports/trust/critic-shadow-objections.csv` | Passed. Wrote `reports/trust/critic-shadow-objections.csv` with header only because there are no persisted `mode=shadow` runs yet. |
| `make eval-critic-live CONFIRM_COST=yes` | Failed as expected in this sandbox. Offline cases still passed, then all 3 live cases failed with `provider_error:Your default credentials were not found...` after attempting the real Vertex/Gemini path. |
| `make critic-shadow-batch CONFIRM_COST=yes REPEAT=1` | Failed as expected in this sandbox. Wrote `reports/trust/trust-e2e-shadow-20261011T185223Z.json`; all 7 tickers were `blocked` with `GoogleAuthError: Unable to authenticate your request.` |

## Shadow report snapshot

| Metric | Value |
| --- | --- |
| runs | 0 |
| objection_rate | 0.0 |
| decision_change_rate | 0.0 |
| deterministic_objections | 0 |
| model_objections | 0 |
| severity:high | 0 |
| severity:medium | 0 |
| severity:low | 0 |

## Objection review sample

Export path: `reports/trust/critic-shadow-objections.csv`

| run_id | ticker | objection_code | severity | source | reason | label |
| --- | --- | --- | --- | --- | --- | --- |
| _none yet_ |  |  |  |  |  |  |

False-objection rate: **not yet evaluable** (no collected objections to review).

## Promotion-criteria status

| Criterion | Threshold | Status | Evidence |
| --- | --- | --- | --- |
| Frozen offline suite | 12/12 passing | Pass | `make eval-critic` passed. |
| Live gate exists and makes real model calls | Real Vertex/Gemini path under `CONFIRM_COST=yes`; no exact-wording assertions | Partial | Implemented; command now reaches the live path and fails honestly on missing ADC credentials. |
| Shadow sample size | >=100 shadow runs across tickers/sectors | No-go | `0/100` successful shadow runs collected in this sandbox. |
| Risk-number equality | 100% exact equality | Not yet evaluable | Batch runner now checks Trust 2/6 invariants plus shadow critic persistence, but no shadow run completed here. |
| Decision upgrades | 0 upgrades | Not yet evaluable | Same blocker as above. |
| Revision limit + persistence | revision ran at most once and persisted | Not yet evaluable | New shadow invariant checks are wired, but no completed persisted shadow trace exists yet. |
| Degraded-path stability | 0 degraded-path changes | Partial | Offline `model_failure` golden case still passes; live auth blocker prevents real-model verification. |
| False objections | <=5% in manual review sample | Not yet evaluable | Exported sample is empty; labels cannot start until shadow objections exist. |
| Stable window | 14 consecutive shadow days | Not started | The 14-day clock cannot start until real shadow runs land. |
| Latency | p95 <= 2.0s critic overhead | Not yet evaluable | Live per-case latency printing is implemented, but no successful live/shadow sample exists here. |
| Cost | mean <= $0.01/run, p95 <= $0.02/run | Not yet evaluable | Live per-case token output is implemented, but no successful live/shadow sample exists here. |
| Human sign-off | Required | Not started | No reviewable shadow evidence yet. |

## Manual follow-up once credentials work

1. Re-run `make eval-critic-live CONFIRM_COST=yes` and confirm the 3 live cases pass structurally.
2. Re-run `make critic-shadow-batch CONFIRM_COST=yes REPEAT=<N>` until the persisted shadow sample reaches at least 100 runs.
3. Re-run `make critic-shadow-report SAMPLE=20 OUT=reports/trust/critic-shadow-objections.csv` and label the exported objections as `true_positive`, `false_positive`, or `missed_problem`.
4. Copy the updated shadow metrics and the labelled sample into this document, then re-evaluate the promotion table.
