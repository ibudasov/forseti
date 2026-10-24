# Technical Analyst shadow rollout review

- Issue: #108 (TA-7), part of epic #101
- Branch: `copilot/agent-orchestration-session-101`
- Reviewed on: 2026-10-22 UTC
- Live gate: `make eval-technical-agent-live CONFIRM_COST=yes`
- Shadow batch: `make technical-shadow-batch CONFIRM_COST=yes REPEAT=1`
- Offline usefulness backtest: `make technical-backtest`

## Local environment reality check

This sandbox cannot collect real shadow-run evidence:

- No usable GCP Application Default Credentials exist (`VERTEX_AI_PROJECT`/
  `VERTEX_AI_LOCATION` are set in `.env`, but there is no
  `~/.config/gcloud/application_default_credentials.json` and no
  `GOOGLE_APPLICATION_CREDENTIALS`). This is the same pre-existing blocker
  documented for the Fundamental Analyst and Critic shadow rollouts in
  `docs/trust-report.md` and `docs/trust/critic-shadow.md`, carried over
  from issue #92.
- The local database has only the seeded universe rows and zero ingested
  structured price/fundamental/technical/earnings/RAG records, so any
  shadow run attempted here would fail on missing market data before it
  could reach a real model call.

Given this, TA-7 was executed strictly as an **offline-only** issue in this
session, per the explicit instruction accompanying this task: implement and
unit-test every invariant/backtest/report piece that does not require a
live model call or ingested data, and leave every result that needs real
shadow runs explicitly marked `PENDING`, rather than fabricated.

## What was implemented and verified offline in this session

| Item | Status | Evidence |
| --- | --- | --- |
| `technical_never_upgrades` invariant | implemented, unit-tested | `check_technical_agent_effect_rank` in `app/trust/invariants.py`; tests in `tests/test_trust_invariants.py` |
| `technical_risk_fields_untouched_in_shadow` invariant | implemented, unit-tested | `check_technical_risk_fields_untouched_in_shadow` in `app/trust/invariants.py` |
| `technical_numeric_claims_grounded` invariant | implemented, unit-tested | `check_technical_numeric_claims_grounded` in `app/trust/invariants.py`, reusing the TA-3 `validate_technical_assessment` numeric-citation check |
| `technical_step_zero_tokens_in_typed_mode` invariant | implemented, unit-tested | `check_technical_step_zero_tokens_in_typed_mode` in `app/trust/invariants.py` |
| Invariants wired into `trust_e2e` report | done | `scripts/trust_e2e.py` calls all 4 checks per run (3 via `evaluate_agentic_invariants`, the numeric-claims check via the workflow's `last_technical_assessment`/`last_technical_request` hook) |
| `technical-shadow-batch` Make target | done | `Makefile`, mirrors `critic-shadow-batch`: `CONFIRM_COST=yes` gated, sets `TECHNICAL_AGENT_MODE=shadow`, `FUNDAMENTAL_AGENT_MODE`/`CRITIC_MODE` configurable, default `off` |
| `scripts/technical_backtest.py` (usefulness backtest) | done | Pure/offline: replays persisted `technical_agent_effect` rows against stored bars only, no model/network calls; unit-tested on synthetic bar series in `tests/test_technical_backtest.py` |
| Matrix archetype documentation | done | `scripts/trust_tickers.json` now tags each existing ticker with a `technical_archetype` and documents which archetypes (`recent_death_cross`, `pre_earnings`) still need `make ingest` |

## Command results (this sandbox, offline-reachable commands only)

| Command | Result |
| --- | --- |
| `make eval-technical-agent` | Passed. All fixtures in `tests/fixtures/technical/` pass; 0 failures, 0 network calls (socket guard enforced in `tests/test_technical_golden.py`). |
| `make test` | Passed: 1203 passed, 6 skipped. |
| `make lint` | Passed: 0 violations. |
| `make typecheck` | Passed: no issues found in 89 source files. |
| `make scorecard` | Passed, no regression vs. `docs/scorecard-baseline.json`. |
| `make technical-shadow-report` | Ran successfully against the local (migrated, empty) database: `run_count=0`, all distributions empty, because zero `mode=shadow` runs have ever been persisted here. |
| `make technical-shadow-report SAMPLE=20 OUT=reports/trust/technical-shadow-sample.csv` | Ran successfully; wrote a header-only CSV (no rows) because there are no persisted shadow runs. |
| `make technical-backtest` | Ran successfully against the empty database: `downgrade.sample_size=0`, `neutral.sample_size=0`, `mean_forward_return=null` for both cohorts, `excluded_insufficient_bars_count=0`. |
| `make technical-shadow-batch CONFIRM_COST=yes REPEAT=1` | **Not run.** Per the task's explicit environment constraint, this would attempt a real Vertex/Gemini call and fail on missing ADC, as already demonstrated for the Critic in `docs/trust/critic-shadow.md`. Not attempted here to avoid wasting time on a confirmed-unavailable path. |
| `make eval-technical-agent-live CONFIRM_COST=yes` | **Not run**, same reason as above. |

## Shadow report snapshot

| Metric | Value |
| --- | --- |
| runs | 0 |
| adjustment_distribution | `{-2: 0, -1: 0, 0: 0}` |
| decision_change_rate (counterfactual) | 0.0 |
| rejection_reason_counts | `{}` |
| p50 / p95 / max latency_ms | 0.0 / 0.0 / 0.0 |
| token_usage | all `0.0` |

**PENDING — requires live shadow-run collection with GCP ADC + `make ingest`.**

## Labelled sample summary

Export path: `reports/trust/technical-shadow-sample.csv` (header only, no rows).

| run_id | ticker | applied_adjustment | final_decision | decision_changed | trend_state | momentum_state | reason_codes | label |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| _none yet_ | | | | | | | | |

Manual justification rate: **PENDING — requires live shadow-run collection
with GCP ADC + `make ingest`.**

## Usefulness backtest table

`make technical-backtest` output against this sandbox's empty database:

| Cohort | Sample size | Mean forward 20-day return | Median forward 20-day return |
| --- | --- | --- | --- |
| `technical_would_downgrade` | 0 | n/a | n/a |
| `technical_neutral` | 0 | n/a | n/a |

Excluded for insufficient trailing bars: 0.

**PENDING — requires live shadow-run collection with GCP ADC + `make
ingest`** (persisted shadow runs with real `as_of_date`/ticker data, plus
ingested daily price bars covering at least 20 trading days after each
`as_of_date`).

The backtest uses the persisted `AgentRun.as_of_date` as its forward-return
anchor. Legacy rows are backfilled from `created_at.date()` and marked with
`as_of_date_source=created_at_backfill`; report output includes the number of
such rows used. If a row has no as-of date, the consumer warns and falls back
to `created_at.date()`.

## Promotion-criteria status

| # | Criterion | Threshold | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Offline golden suite | All TA-6 cases pass in CI | **met** | `make eval-technical-agent` passes, 0 failures. |
| 2 | Shadow sample size | >= 100 shadow runs across >= 5 sectors | pending | `0/100` runs collected in this sandbox. |
| 3 | Invariant violations | 0 upgrades, 0 changed risk numbers, 0 ungrounded numeric claims | pending | All 4 invariants implemented and unit-tested offline (19/19 passing in `tests/test_trust_invariants.py`); not yet exercised against real shadow data. |
| 4 | Assessment rejection rate | <= 10%, no single reason code above 5% | pending | No persisted shadow runs to measure. |
| 5 | Counterfactual downgrade rate | between 5% and 40% | pending | No persisted shadow runs to measure. |
| 6 | Manual review | >= 80% of sampled downgrades judged justified | pending | Sample export is header-only; nothing to label yet. |
| 7 | Usefulness | mean forward 20-day return of downgraded runs below neutral runs, reported with sample size, no significance claim | pending | `scripts/technical_backtest.py` implemented and unit-tested on synthetic bars; both cohorts have `sample_size=0` here. |
| 8 | Latency / cost | p95 added latency <= 8s, mean added cost per run reported | pending | No persisted shadow runs to measure. |
| 9 | Human sign-off | required | pending | Blocked on all of the above. |

## Manual follow-up once an environment with GCP ADC and ingested data exists

1. Run `make ingest` (and `make ingest-earnings` if pre-earnings archetype
   coverage is desired) against the real providers to populate daily bars,
   fundamentals, and earnings calendar data for the frozen ticker matrix in
   `scripts/trust_tickers.json`. Add tickers for the `recent_death_cross`
   and `pre_earnings` archetypes if the existing seven do not naturally
   exhibit them after ingestion (see `selection_basis.technical_archetype_coverage`
   in that file).
2. Run `make technical-shadow-batch CONFIRM_COST=yes REPEAT=<N>` repeatedly
   (matrix x repeat, across several days) until at least 100 shadow runs
   are persisted, keeping `FUNDAMENTAL_AGENT_MODE=off` and `CRITIC_MODE=off`
   (the Make target's defaults) to isolate the technical effect.
3. Run `make technical-shadow-report` and copy the real metrics into the
   "Shadow report snapshot" table above.
4. Run `make technical-shadow-report SAMPLE=20 OUT=reports/trust/technical-shadow-sample.csv`,
   have the owner label each row `justified` or `unjustified`, and copy the
   labelled summary into the "Labelled sample summary" table above.
5. Run `make technical-backtest` against the now-populated database and
   copy the real cohort table into the "Usefulness backtest table" above.
   Do not tune `PROMPT_VERSION`, the technical facts module, or the policy
   thresholds using this same backtest data without disclosing that in this
   document — the backtest is descriptive evidence for promotion, not a
   tuning signal, unless explicitly reported as such.
6. Re-evaluate every `pending` row in the promotion-criteria table above
   against the real numbers, mark each `met`/`not met`, and record the
   go/no-go decision below.
7. If it's **go**: open a separate, small PR that changes `.env.example` or
   the deployment default for `TECHNICAL_AGENT_MODE`. The code default in
   this repository must stay `off` until the owner explicitly approves that
   follow-up PR.
8. If any criterion fails: file one follow-up issue per failing criterion
   (for example, bumping `PROMPT_VERSION` to tune the assessment prompt, or
   adjusting `app/domain/technical_facts.py`), rather than silently
   reinterpreting the threshold.

## Go/no-go

**Not decided — PENDING live shadow-run collection.** No go/no-go
recommendation is recorded in this document because no real shadow data has
been collected in this sandbox. Fabricating a recommendation without real
evidence would violate the explicit constraint of this task.
