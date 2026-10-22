# Trust 2/6 end-to-end review

- Issue: #94
- Matrix frozen on: 2026-10-11 UTC
- Branch: `copilot/ibudasov-92-parent-session`
- Live runner: `make trust-e2e CONFIRM_COST=yes`

## Ticker matrix

| Ticker | Sector | Intended review bucket | Why it is in the matrix |
| --- | --- | --- | --- |
| `NVDA` | AI | clear trade | Strong large-cap AI benchmark for a likely positive path. |
| `LMT` | defence | watchlist | Mature defence name reserved for an ambiguous/manual-review path. |
| `OKLO` | nuclear | hard veto | Volatile nuclear name chosen to probe RSI / near-earnings veto handling. |
| `FSLR` | green energy | clear no_trade | Green-energy representative reserved for a deterministic no-trade comparison. |
| `IONQ` | quantum | incomplete data | Quantum name selected to probe sparse-coverage handling. |
| `SERV` | robotics | hard veto | Momentum-heavy robotics name chosen as a second veto candidate. |
| `RKLB` | space | clear trade | Space benchmark intended to provide a second positive trace. |

## Local environment reality check

- `make migrate` succeeded, `seed_universe()` inserted the missing universe rows, and the local DB now lists all 35 target-universe tickers as active.
- The local Postgres volume still had **zero** structured data rows for prices, fundamentals, technical features, earnings, and RAG chunks at the time this issue was worked.
- `~/.config/gcloud/application_default_credentials.json` exists as a **directory**, not a readable ADC JSON file, so live Vertex/Gemini authentication is not available in this sandbox.

## Summary table

| Step | Result | Observed output |
| --- | --- | --- |
| Freeze ticker matrix (`scripts/trust_tickers.json`) | Done | 7 cross-sector tickers committed with intended review buckets and notes. |
| Build reusable invariant checks (`app/trust/invariants.py`) | Done | Pure checks cover specialist order, risk-number equality, downgrade-only behavior, and warning propagation. |
| Add unit coverage (`tests/test_trust_invariants.py`) | Done | Synthetic cases cover pass/fail paths, including the explicit `critic_mode_off` skip allowance. |
| Add manual live runner (`scripts/trust_e2e.py`, `make trust-e2e`) | Done | Runner writes JSON reports to gitignored `reports/trust/` and exits non-zero on violations or runtime blockers. |
| Attempt live run | Blocked by environment | `make trust-e2e CONFIRM_COST=yes` wrote `reports/trust/trust-e2e-20261011T183114Z.json`; every ticker reached the linear pipeline (`decision=no_trade`, warning `no_price_data`) and every agentic call failed with `GoogleAuthError: Unable to authenticate your request.` |
| Manual trace review | Blocked by environment | No real agentic traces were produced in this sandbox, so there were no anomalies to log yet. |

## Manual review notes

1. The issue text says to keep `CRITIC_MODE=off`, but the acceptance criteria also say no specialist step may be skipped. In the current implementation, `critic_guardrail` is recorded as a trace step with status `skipped` and reason `critic_mode_off` when the critic is off.
2. To keep Trust 2/6 aligned with the execution plan, the reusable invariant checker treats `critic_guardrail` as **required in sequence** but allows the specific skip reason `critic_mode_off`. Any other skipped specialist still fails the run.
3. Because no live run completed here, there are no golden-case candidates or anomaly issues yet. The next session with working data + Vertex auth should run the matrix, inspect the generated report, and open follow-up issues for any surprising traces.

## Remaining manual validation once credentials work

1. Run `make trust-e2e CONFIRM_COST=yes`.
2. Open the newest `reports/trust/trust-e2e-*.json`.
3. For each ticker, inspect the persisted trace ordering plus the agentic-vs-linear invariant results.
4. If any anomaly appears, open a dedicated issue and link it from #94 before closing the trust review.
