Execution plan for a coding agent to implement the deterministic evaluation engine in [ibudasov/forseti](https://github.com/ibudasov/forseti). This is <b>guide 2 of 3</b> for the <b>Weeks 3–4 — Structured data + rules engine</b> phase of the roadmap. It covers the two things guide 1 (<i>Execution Plan: Structured Data Ingestion</i>) explicitly deferred: (a) computing `technical_feature` rows from ingested price bars, and (b) the rules engine that turns stored data into the `trade / watchlist / no_trade` recommendation behind `POST /analyze`. No LLM anywhere in this guide — the RAG/explanation layer arrives in Weeks 5–6. Every step below is written to be executed mechanically, in order, without design decisions left to the agent.

> 🎯 Mission: replace the placeholder analyzer with a deterministic evaluation engine — data gate → hard vetoes → checklist scoring → decision thresholds → risk math → confidence — that reads only Postgres, persists every recommendation via `save_recommendation`, and is fully unit-testable with golden cases. Plus the feature-computation step that fills `technical_feature` at the end of each ingestion run.

## 1. Verified current state of the repository

Facts verified against `main` on 2026-08-06, plus the expected delivered outputs of the <i>Execution Plan: Structured Data Ingestion</i> (guide 1) and the <i>Execution Plan: GET /ticker/\{symbol\}</i>. Verify before building; if an expected piece is missing or has drifted, stop and report instead of improvising.

| File | What exists today |
| --- | --- |
| `app/main.py` | FastAPI app; `POST /analyze` wired via dependency `get_analysis_engine()` returning `get_engine()` |
| `app/schemas/analyze.py` | `AnalyzeRequest` (validates via `validate_and_normalize_ticker`), `AnalyzeResponse` placeholder — floats at the API boundary; `trace_id` / `engine_version` conventions exist here |
| `app/services/analyzer.py` | `validate_and_normalize_ticker`; `ENGINE_VERSION = "v1.placeholder.0"`; placeholder engine to be replaced by this guide |
| `app/services/ticker_profile.py` | From the ticker-endpoint plan: `STALE_PRICE_DATA_THRESHOLD_DAYS = 7`, `build_ticker_profile`, and the warning vocabulary — explicitly designed for reuse by this engine |
| `app/db/models.py` | SQLModel tables: `Security`, `PriceBar`, `Fundamental`, `EarningsEvent`, `MacroDaily`, `TechnicalFeature`, `Recommendation` |
| `app/db/repository.py` | Existing: `get_security`, `get_latest_bars(ticker, n, engine=None, as_of_date=None)` (desc order), `save_recommendation`; from guide 1: `list_active_securities`, `upsert_price_bars`, `upsert_macro_daily`, `upsert_fundamental`, `upsert_earnings_events`; from the ticker-endpoint plan: `count_price_bars`, `get_latest_technical_feature`, `get_latest_fundamental`, `get_next_earnings_event` |
| `app/ingestion/` | From guide 1: `universe.py`, `prices.py`, `vix.py`, `fundamentals.py`, `earnings.py`, `run.py` with `--source` choices `all/prices/vix/fundamentals/earnings` |
| `app/settings.py` | `DATABASE_URL`, `ALPHA_VANTAGE_API_KEY`, `EDGAR_USER_AGENT`, `INGEST_PRICE_PERIOD` |
| `tests/` | `db_engine` fixture (testcontainers Postgres), `db_client` TestClient fixture, ingestion tests with no network access |
| `Makefile` | `make test`, `make ingest` |
| `.github/instructions/codestyle.instructions.md` | Clean Code + Object Calisthenics — <b>binding for all code produced here</b> |

Model details relevant to this guide (exact):

- `TechnicalFeature`: PK (`security_id`, `as_of_date`); nullable `rsi_14`, `sma_50`, `sma_200`, `volume_trend`
- `Recommendation`: read `app/db/models.py` first and map the engine output to its exact fields. If a field needed by the §2.4 contract is missing, <b>stop and report — no migration in this guide</b>

## 2. Evaluation contract (exact specification)

### 2.1 Feature computation (`app/ingestion/features.py`, new module)

- New pipeline step `features`, appended to the `run.py` fixed order: universe seed → prices → vix → fundamentals → earnings → <b>features</b>. The `--source` choices gain `features`; `all` includes it. This is the only change to guide-1 code.
- Input per active security: latest 250 bars via `get_latest_bars` (desc order) → reverse to ascending before computing.
- Compute (pure functions on lists of bars; floats internally; quantize to 4 decimal places on write, consistent with guide-1 conventions):
  - `rsi_14` — Wilder's RSI, period 14; needs ≥ 15 bars, else `None`
  - `sma_50` / `sma_200` — simple means of closes; need ≥ 50 / ≥ 200 bars, else `None`
  - `volume_trend` — `mean(volume last 20) / mean(volume prior 20)`; `None` if \< 40 bars or the prior mean is 0
- `as_of_date` = latest bar date; upsert one row per (`security_id`, `as_of_date`) — re-running must update, never duplicate.
- New repository function `upsert_technical_feature` in `app/db/repository.py`, same style as the guide-1 upserts.

### 2.2 Decision pipeline (the engine)

Strict order; every step is a pure function; all reads are Postgres-only.

<b>Step 0 — Data gate.</b> Build the freshness/completeness picture, reusing the ticker endpoint's vocabulary and importing `STALE_PRICE_DATA_THRESHOLD_DAYS` from `app.services.ticker_profile` (do not redefine it). Warnings come from exactly this set: `no_price_data`, `stale_price_data`, `no_technical_features`, `no_fundamentals`, `no_earnings_data`, `security_inactive`, `insufficient_price_data` (fewer than `SMA_LONG` bars stored).

Gate short-circuits, evaluated before vetoes:

- `no_price_data` → `no_trade` immediately (nothing to evaluate)
- `security_inactive` → `no_trade` with that warning
- `stale_price_data` or `no_fundamentals` present → decision is <b>capped at </b><b>`watchlist`</b> even if scoring would say `trade`

<b>Step 1 — Hard vetoes.</b> Any one stops evaluation; first hit in this order wins; the veto text goes into `reasons`. Null feature values never trigger a veto (fail open).

| veto | condition | decision |
| --- | --- | --- |
| `rsi_overbought` | `rsi_14 > 70` | `no_trade` |
| `vix_panic_regime` | latest VIX close `> 30` | `no_trade` |
| `earnings_too_close` | next earnings within 7 days (inclusive) | `watchlist` |
| `deep_below_sma200` | `close < sma_200 × 0.90` | `no_trade` |

<b>Step 2 — Checklist scoring (max 11 points).</b> Null inputs score 0.

| check | pass condition | points |
| --- | --- | --- |
| `revenue_growth` | `> 0.15` YoY | +2 |
| `fcf` | `> 0` | +2 |
| `debt_to_equity` | `< 1.0` | +1 |
| `eps_trend` | `> 0` | +1 |
| `close_vs_sma50` | `close > sma_50` | +1 |
| `close_vs_sma200` | `close > sma_200` | +1 |
| `rsi_healthy` | `45 ≤ rsi_14 ≤ 65` | +1 |
| `volume_trend` | `> 1.0` | +1 |
| `vix_calm` | VIX `< 25` | +1 |

<b>Step 3 — Decision thresholds.</b> `score ≥ 8` → `trade`; `5–7` → `watchlist`; `≤ 4` → `no_trade`. Then apply the Step-0 caps.

<b>Step 4 — Risk math (only when decision is </b><b>`trade`</b><b>).</b> All money/price outputs quantized to 2 decimal places at the boundary.

- `entry_reference` = latest close
- `entry_range` = `[close × 0.98, close × 1.02]`
- `atr_14` = simple mean of true range over the last 14 bars (simple mean, not Wilder's — frozen)
- `stop_loss` = `min(lowest low of last 20 bars, close − 2 × atr_14)`
- `risk_per_share` = `entry_range.high − stop_loss`; if `≤ 0` → `no_trade` with warning `invalid_risk_geometry`
- `take_profit` = `[highest high of last 60 bars, close + 2.0 × (close − stop_loss)]` — TP1 is anchored to the 60-day swing high (resistance), <b>not</b> a risk multiple, so the risk/reward gate below is a real test rather than a tautology
- `risk_reward` = `(take_profit[0] − close) / (close − stop_loss)`; if `< 1.5` → downgrade to `watchlist` with reason `risk_reward_below_min`
- `risk_budget_eur` = `ACCOUNT_CAPITAL_EUR × RISK_PER_TRADE_PCT`; `shares` = `floor(risk_budget_eur / risk_per_share)`; if `shares == 0` → downgrade to `watchlist` with reason `position_size_zero`
- `position_size_eur` = `shares × entry_range.high`

<b>Step 5 — Confidence.</b> `round2(clamp(score / 11 − 0.05 × len(warnings), 0.0, 1.0))`.

<b>Step 6 — Reasons and warnings.</b> `reasons` = templated strings from every fired rule, formatted `"<rule_id>: <detail>"` (e.g. `"revenue_growth: 0.62 YoY > 0.15 min"`, `"close_vs_sma50: 122.00 > 118.30"`), plus the veto or downgrade reason when one fired. `warnings` = the machine-readable snake\_case strings from Step 0.

### 2.3 Frozen constants

> 📐 Explicit decisions (do not reconsider): every rule threshold is a <b>module-level constant</b>, not a setting — same rule as the ticker-endpoint plan (no speculative configuration). Only the two account parameters are settings, because they describe the account, not the strategy. Week 9 evaluation tunes the constants via code changes.

| constant | value |
| --- | --- |
| `RSI_PERIOD` | 14 |
| `RSI_OVERBOUGHT_VETO` | 70 |
| `RSI_HEALTHY_MIN` / `RSI_HEALTHY_MAX` | 45 / 65 |
| `SMA_SHORT` / `SMA_LONG` | 50 / 200 |
| `SMA200_DEEP_VETO_PCT` | 0.10 |
| `VOLUME_WINDOW` | 20 |
| `VIX_CALM_MAX` | 25 |
| `VIX_PANIC_VETO` | 30 |
| `EARNINGS_VETO_DAYS` | 7 |
| `REVENUE_GROWTH_MIN` | 0.15 |
| `DEBT_TO_EQUITY_MAX` | 1.0 |
| `EPS_TREND_MIN` | 0 |
| `SCORE_TRADE_MIN` / `SCORE_WATCHLIST_MIN` | 8 / 5 |
| `MAX_SCORE` | 11 |
| `ATR_PERIOD` / `ATR_STOP_MULTIPLE` | 14 / 2.0 |
| `SWING_LOW_LOOKBACK` / `SWING_HIGH_LOOKBACK` | 20 / 60 |
| `ENTRY_BUFFER_PCT` | 0.02 |
| `TP2_RISK_MULTIPLE` | 2.0 |
| `RISK_REWARD_MIN` | 1.5 |
| `CONFIDENCE_WARNING_PENALTY` | 0.05 |
| `ENGINE_VERSION` | `"v1.rules.0"` |

Settings (env-tunable, with defaults): `ACCOUNT_CAPITAL_EUR: float = 10000`, `RISK_PER_TRADE_PCT: float = 0.01`.

### 2.4 API contract (`POST /analyze`)

Extend the placeholder `AnalyzeResponse` to the roadmap's recommendation schema, keeping the existing `trace_id` / `engine_version` conventions:

```json
{
  "ticker": "NVDA",
  "decision": "trade",
  "entry_range": [119.56, 124.44],
  "stop_loss": 114.0,
  "take_profit": [134.0, 138.0],
  "risk_reward": 1.5,
  "position_size_eur": 1119.96,
  "confidence": 0.77,
  "reasons": ["revenue_growth: 0.62 YoY > 0.15 min"],
  "warnings": [],
  "engine_version": "v1.rules.0",
  "trace_id": "..."
}
```

- `decision` is exactly one of `"trade" | "watchlist" | "no_trade"`
- For `watchlist` / `no_trade`: `entry_range`, `stop_loss`, `take_profit`, `risk_reward`, `position_size_eur` are `null`; `reasons` and `warnings` are always present
- Invalid symbol → 422 via the existing `validate_and_normalize_ticker`; valid but unknown symbol → 404 `{"detail": "ticker_not_found: XYZ"}` (consistent with the ticker endpoint)
- Every completed analysis is persisted via `save_recommendation`, mapping to the existing `Recommendation` fields exactly; on any mismatch → stop and report

## 3. Architecture (aligned with the repo codestyle)

Thin route → orchestrating service → pure domain modules → repository I/O. Codestyle §16 (wrap domain primitives) applies directly here.

- `app/services/analyzer.py` — orchestrator `analyze(symbol, engine=None, today=None)`: assemble snapshot → gate → vetoes → score → decision → risk math → persist. `today` is injectable for deterministic tests. Replaces the placeholder internals; `validate_and_normalize_ticker` is untouched.
- `app/services/vetoes.py` — pure predicates, evaluated in §2.2 order, returning the first veto or `None`.
- `app/services/checklist.py` — pure scoring rules; each returns `(rule_id, points, reason)`.
- `app/services/risk.py` — pure math plus frozen dataclasses `RiskConfig(capital_eur, risk_per_trade_pct)` and `TradeLevels(entry_range, stop_loss, take_profit, risk_reward, shares, position_size_eur)`.
- `app/ingestion/features.py` — feature computation and its registration in `run.py`.
- `app/db/repository.py` — add `get_latest_macro_daily(engine=None)` and `upsert_technical_feature(...)`; reuse everything else.
- No network, no LLM, no `yfinance`/`httpx` imports anywhere under `app/services/`.

## 4. Execution steps

### Phase 0 — Baseline

- [ ] `git checkout main && git pull` — create branch `feat/evaluation-engine`
- [ ] Read `AGENTS.md` and `.github/instructions/codestyle.instructions.md`; they are binding
- [ ] Run `make test` — must be green before any change; if red, stop and report
- [ ] Verify the §1 expectations (ticker-endpoint service and its repository functions, guide-1 ingestion package and upserts); read the `Recommendation` model fields; stop and report on any drift

### Phase 1 — Settings

- [ ] `app/settings.py`: add `ACCOUNT_CAPITAL_EUR: float = 10000` and `RISK_PER_TRADE_PCT: float = 0.01` — nothing else becomes a setting
- [ ] `make test` still green

### Phase 2 — Repository additions (`app/db/repository.py`)

- [ ] `get_latest_macro_daily(engine=None) -> Optional[MacroDaily]` — latest row by date
- [ ] `upsert_technical_feature(feature, engine=None)` — ON CONFLICT (`security_id`, `as_of_date`) DO UPDATE
- [ ] Reuse `count_price_bars`, `get_latest_technical_feature`, `get_latest_fundamental`, `get_next_earnings_event`, `get_latest_bars`, `save_recommendation` — do not duplicate
- [ ] `tests/test_db.py`: tests for both new functions, including upsert-twice idempotency

### Phase 3 — Feature computation (`app/ingestion/features.py`)

- [ ] Pure functions `compute_rsi(closes)`, `compute_sma(closes, window)`, `compute_volume_trend(volumes)` with the None-rules from §2.1
- [ ] `compute_technical_features(engine=None)` — loops active securities, fetches bars, computes, upserts with 4-decimal quantization; per-ticker error isolation exactly like guide 1
- [ ] Register in `run.py`: append `features` to the fixed order and to the `--source` choices; `all` includes it
- [ ] Tests: hand-computed RSI/SMA/volume-trend cases; short-history cases produce `None`; pipeline step is idempotent

### Phase 4 — Vetoes (`app/services/vetoes.py`)

- [ ] Four pure predicates per §2.2, evaluated in the listed order, first hit wins
- [ ] Tests: each veto triggers on a boundary-crossing value; each fails open on `None`; boundary values exactly at the threshold do NOT trigger (strict `>`)

### Phase 5 — Checklist (`app/services/checklist.py`)

- [ ] Nine rules per §2.2 with the frozen constants; each returns `(rule_id, points, reason)`
- [ ] Tests: each rule passes/fails on crafted inputs; null inputs score 0; maximum total is 11

### Phase 6 — Risk math (`app/services/risk.py`)

- [ ] `RiskConfig` / `TradeLevels` frozen dataclasses; ATR (simple mean of true range), swing low/high, sizing, both downgrades
- [ ] Golden test with these exact frozen numbers: crafted bars with `close = 122.00`, `swing_low_20 = 115.00`, `atr_14 = 4.00`, `swing_high_60 = 134.00` → `stop_loss = 114.00`; `entry_range = [119.56, 124.44]`; `risk_per_share = 10.44`; budget `10000 × 0.01 = 100` → `shares = 9`; `position_size_eur = 1119.96`; `take_profit = [134.00, 138.00]`; `risk_reward = 1.5` → passes the gate
- [ ] Tests for `invalid_risk_geometry`, `risk_reward_below_min` (swing high at 130.00 → `risk_reward = 1.0` → downgrade), `position_size_zero`

### Phase 7 — Orchestrator and API (`app/services/analyzer.py`, `app/schemas/analyze.py`)

- [ ] `analyze(symbol, engine=None, today=None)` implementing the six steps in order; gate warnings assembled with the exact vocabulary; confidence per §2.2
- [ ] Persist via `save_recommendation` on every completed analysis; bump `ENGINE_VERSION` to `"v1.rules.0"`
- [ ] Evolve `AnalyzeResponse` to the §2.4 contract (keep `trace_id` / `engine_version` conventions; floats at the boundary; null risk block for non-trade decisions); 404 for unknown valid tickers

### Phase 8 — Integration tests (`tests/test_integration.py`, existing fixtures and conventions)

- [ ] Full-trade golden case: seed NVDA + 250 crafted bars + features + fundamentals + VIX row + future earnings → `POST /analyze` → assert every contract field including the exact frozen numbers from Phase 6
- [ ] One test per veto, one per gate cap (`stale_price_data`, `no_fundamentals` → capped at `watchlist`; `no_price_data`, `security_inactive` → `no_trade`)
- [ ] Determinism: two identical calls return identical payloads (except `trace_id`)
- [ ] 404 and 422 paths; null fields for non-trade decisions

### Phase 9 — Documentation and delivery

- [ ] `README.md`: new `## Evaluation engine` section — the six-step pipeline, the constants table, the worked example, and a note that `make ingest` now also computes features
- [ ] `make test` — 100% green including all pre-existing tests; match CI exactly
- [ ] Manual smoke run: `make ingest`, then `curl -s -X POST http://127.0.0.1:8000/analyze -H 'Content-Type: application/json' -d '{"ticker": "NVDA"}'`
- [ ] Codestyle self-check against the review checklist; small logical commits in the repo's message style; PR titled e.g. `feat: add deterministic evaluation engine`

## 5. Acceptance criteria (definition of done)

- [ ] `POST /analyze` returns the frozen §2.4 contract; `decision` ∈ `trade | watchlist | no_trade`
- [ ] All four vetoes, both gate caps, and both risk downgrades behave exactly as §2.2 specifies
- [ ] Risk math matches the worked example to the cent
- [ ] Identical seeded data → identical decision payload (except `trace_id`) — determinism is provable
- [ ] The `features` step fills `technical_feature` idempotently and runs as part of `make ingest`
- [ ] Every analysis is persisted via `save_recommendation`; missing data never fabricates values; the warnings vocabulary matches §2.2 exactly
- [ ] No LLM, no network under `app/services/`; all tests run with no network access; `make test` 100% green
- [ ] No behavior changes to `GET /ticker/{symbol}` or to the guide-1 ingestion sources

## 6. Non-goals and guardrails

- No LLM, RAG, or explanation prose (Weeks 5–6); no new data sources; no scheduling (Week 10); no broker integration of any kind
- The only change to ingestion code is appending the `features` step to `run.py` — fetch/parse/upsert behavior from guide 1 is untouched
- `GET /ticker/{symbol}` behavior is unchanged; import `STALE_PRICE_DATA_THRESHOLD_DAYS`, do not move or duplicate it
- No migrations: `Recommendation` and `TechnicalFeature` are used as they exist; on mismatch, stop and report
- No new settings for rule thresholds — they are frozen module constants
- Do not refactor unrelated code (Boy Scout Rule applies only to lines you must touch); if any §1 fact is stale, stop and report

## 7. Why this shape (context for reviewers)

The strategy's edge is discipline: identical inputs must produce identical verdicts, which is what makes the Week 9 golden-case evaluation suite possible — the engine is regression-tested like any pure function. Vetoes, scoring, and risk math are pure modules so they are unit-testable without DB or HTTP (codestyle §32), and the orchestrator is the only place where I/O and ordering meet. Reusing the ticker endpoint's freshness vocabulary makes `GET /ticker/{symbol}` a human-readable window into exactly the gate the engine applies. TP1 is anchored to the 60-day swing high so the risk/reward gate measures market structure instead of being satisfied by construction. Risk parameters are the only settings because they describe the account, not the strategy.

---

_Migrated from Notion on 2026-11-12._
