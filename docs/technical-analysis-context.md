# Technical Analysis Context

Forseti builds one immutable `TechnicalAnalysisRequest` per ticker/as-of snapshot before any Technical Analyst model call. It extends the TA-1 deterministic baseline (`app/domain/technicals.py`) with a closed set of derived, citable indicator facts, so the analyst reasons from numbers it can point back to instead of four raw columns.

## Schema version

- `schema_version`: `1.0`
- `run_id`: workflow run identifier
- `context_hash`: SHA-256 hash over the canonical JSON payload excluding `run_id`, `context_hash` and `snapshot_at`
- `ticker`, `sector`
- `snapshot_at`: deterministic cutoff timestamp for the request (end of `as_of_date`, UTC)
- `as_of_date`: date used to filter bars, the technical feature snapshot, VIX, and earnings (no look-ahead)
- `deterministic_result`: the TA-1 `DeterministicTechnicalAnalysis` baseline (checklist rules 5-9)
- `facts`: every citable fact, each with a unique `metric_id`
- `series`: the last `SERIES_WINDOW` bars with per-bar rolling indicators, ascending by date
- `coverage`: explicit presence, missingness, staleness, and truncation metadata
- `allowed_adjustment_min`, `allowed_adjustment_max`: bounded analyst adjustment range, fixed at `[-2, 0]` (downgrade-only, see [ADR 007](adr/007-technical-analyst-authority-is-downgrade-only.md))

## Facts and their source

`request.facts` is the union of two groups, concatenated in this order. Every `metric_id` across the whole list is unique.

### 1. TA-1 baseline passthrough

`close`, `sma_50`, `sma_200`, `rsi_14`, `volume_trend` and `vix_close` are **not** recomputed by the technical context builder. They are reused verbatim from `DeterministicTechnicalAnalysis.metrics` (TA-1), under the exact same `metric_id` the baseline already cites. This guarantees every fact cited by the TA-1 baseline is present in the technical context under the same `metric_id` — there is deliberately only one copy of each of these values, so the analyst and the deterministic checklist can never disagree about which number a shared `metric_id` points to.

TA-1 omits a metric entirely (rather than emitting it with a `None` value) when its source row is missing: `sma_50`, `sma_200`, `rsi_14` and `volume_trend` all require a `TechnicalFeature` row dated on or before `as_of_date`; `vix_close` requires a `MacroDaily` row; `close` requires at least one `PriceBar`. See `docs/technical-analysis-contracts.md` for the exact TA-1 `metric_id` date-fallback rule (`TechnicalFeature.as_of_date`, else `PriceBar.bar_date`, else `"unknown"`).

### 2. Derived facts (`app/domain/technical_facts.py`)

Every derived fact below is a pure function of the bar series (plus, for `days_to_earnings`, the next earnings report date). Its `metric_id` is always `"<name>:<as_of_date>"`, where `as_of_date` is the context's own `as_of_date` (the query cutoff), not any underlying row's date. All decimal values are quantised to 4 decimal places with `ROUND_HALF_UP`.

| name | formula | `None` when |
| --- | --- | --- |
| `dist_sma50_pct` | `(close - sma_50) / sma_50`, with `sma_50` recomputed from the bar series via `compute_sma` | fewer than 50 bars |
| `dist_sma200_pct` | `(close - sma_200) / sma_200`, `sma_200` recomputed from bars | fewer than 200 bars |
| `sma50_slope_10d_pct` | `(sma_50_t - sma_50_t-10) / sma_50_t-10`, both SMA50 values recomputed over the series | fewer than 60 bars |
| `sma200_slope_20d_pct` | `(sma_200_t - sma_200_t-20) / sma_200_t-20`, both SMA200 values recomputed over the series | fewer than 220 bars |
| `sma50_above_sma200` | boolean: `sma_50 > sma_200` (encoded as `1`/`0`, `unit="bool"`) | either SMA is unavailable |
| `days_since_sma_cross` | bars since the last sign change of `(sma_50 - sma_200)`, scanning backward over every loaded bar | not enough history to compute both SMAs at the latest bar, or no sign change found anywhere in the loaded history |
| `rsi_14_5d_change` | `rsi_14_t - rsi_14_t-5`, both RSI values recomputed from the series via `compute_rsi` (Wilder RSI-14) | fewer than 5 bars since the comparison point, or RSI undefined at either point |
| `rsi_price_divergence_20d` | boolean (encoded as `1`/`0`): price makes a new 20-bar high (`close_t == max(close window)`) **XOR** RSI makes a new 20-bar high (`rsi_14_t == max(rsi window)`). Exactly one of the two disagreeing is a divergence | fewer than 20 bars, or RSI cannot be computed for every bar in the 20-bar window |
| `atr_14` | average true range over the last 14 bars (`app.services.risk.calculate_atr`, the public name for the former `_calculate_atr`; behaviour unchanged) | fewer than 14 bars |
| `atr_14_pct` | `atr_14 / close` | `atr_14` unavailable, or `close == 0` |
| `swing_low_20` | `min(low)` over the last 20 bars | fewer than 20 bars |
| `swing_high_60` | `max(high)` over the last 60 bars | fewer than 60 bars |
| `dist_swing_low_20_pct` | `(close - swing_low_20) / swing_low_20` | `swing_low_20` unavailable |
| `dist_swing_high_60_pct` | `(close - swing_high_60) / swing_high_60` | `swing_high_60` unavailable |
| `high_52w` | `max(high)` over the last 252 bars, or fewer if unavailable (flagged via `coverage.series_truncated`/`bar_count`) | no bars at all |
| `low_52w` | `min(low)` over the last 252 bars, or fewer if unavailable | no bars at all |
| `dist_high_52w_pct` | `(close - high_52w) / high_52w` | `high_52w` unavailable |
| `up_volume_ratio_20d` | `sum(volume on up days) / sum(volume)` over the last 20 bars, where an "up day" is `close_i > close_{i-1}`; needs 21 bars to classify all 20 days | fewer than 21 bars, or the 20-bar total volume is `0` |
| `return_20d_pct` | `(close_t - close_t-20) / close_t-20` | fewer than 21 bars, or `close_t-20 == 0` |
| `return_60d_pct` | `(close_t - close_t-60) / close_t-60` | fewer than 61 bars, or `close_t-60 == 0` |
| `days_to_earnings` | `(next_earnings_report_date - as_of_date).days`, i.e. **calendar** days (not trading days) from the context's as-of date to the next confirmed earnings report | no known upcoming earnings event |

## Series window

`series` holds the last `SERIES_WINDOW` (`60`) bars, ascending by `bar_date`. Each `IndicatorSeriesPoint`'s `sma_50`, `sma_200` and `rsi_14` are recomputed as of that bar's own date (using every earlier bar in the loaded history, not just the window), so the series shows how each indicator evolved rather than repeating one latest-only value. `coverage.series_truncated` is `true` whenever more bars were loaded than fit in the series window.

## No new database table

Technical context facts are computed on demand from `PriceBar` rows (`app.ingestion.features.compute_sma` / `compute_rsi`, `app.services.risk.calculate_atr`) plus the existing `TechnicalFeature`, `MacroDaily`, and `EarningsEvent` rows. `TechnicalFeature` only ever stores the latest snapshot per date, so nothing here persists a new table — the builder simply reloads a window of bars for every request. `volume_trend` is the one fact sourced directly from `TechnicalFeature` (via the TA-1 passthrough above) rather than recomputed from bars.

## No look-ahead

`build_technical_analysis_request(ticker, run_id, *, as_of=None, engine=None)` only reads rows dated on or before `as_of_date`:

- Bars: `get_latest_bars(..., as_of_date=as_of_date)`
- `TechnicalFeature`: `get_latest_technical_feature(..., as_of_date=as_of_date)`
- `MacroDaily` (VIX): `get_latest_macro_daily(..., as_of_date=as_of_date)`
- Earnings: `get_next_earnings_event(..., on_or_after=as_of_date)`

A future-dated row never influences the computed facts, series, or coverage for an earlier `as_of_date`.

## Deterministic budgets

- Bars loaded per request: `HISTORY_BARS_LOADED = 260`
- Series window: `SERIES_WINDOW = 60`
- Stale price data threshold: `STALE_PRICE_DATA_THRESHOLD_DAYS = 7` (days between `as_of_date` and the latest bar before `coverage.bars_stale` flips `true`)
- Lookbacks: `SMA50_SLOPE_LOOKBACK_DAYS = 10`, `SMA200_SLOPE_LOOKBACK_DAYS = 20`, `RSI_CHANGE_LOOKBACK_DAYS = 5`, `DIVERGENCE_LOOKBACK_DAYS = 20`, `RETURN_SHORT_LOOKBACK_DAYS = 20`, `RETURN_LONG_LOOKBACK_DAYS = 60`, `UP_VOLUME_LOOKBACK_DAYS = 20`, `HIGH_LOW_52W_LOOKBACK_DAYS = 252` (all in `app/domain/technical_facts.py`); `ATR_PERIOD = 14`, `SWING_LOW_LOOKBACK = 20`, `SWING_HIGH_LOOKBACK = 60` (in `app/services/risk.py`)
- Serialized payload budget: a 60-bar window serializes to well under 15 KB of JSON (enforced by a test)

## Missing-data semantics

- Every derived fact has a documented `None` rule above; a missing fact never invents a value — it sets `value=None` and the fact's `name` appears in `coverage.required_facts_missing`.
- A missing `TechnicalFeature` row means `sma_50`, `sma_200`, `rsi_14` and `volume_trend` are entirely absent from `facts` (not present with `value=None`), matching TA-1's own omission rule; this still produces a valid request with the TA-1 baseline's rules reported as `unknown` and a `no_technical_features` warning.
- A missing `MacroDaily`/VIX row means `vix_close` is absent from `facts`, with a `no_vix` warning on the baseline.
- `coverage.bars_stale` is explicit and does not itself invent a negative signal; it only flags that the latest bar is more than `STALE_PRICE_DATA_THRESHOLD_DAYS` days behind `as_of_date`.

## Canonical hashing

`context_hash` is computed with the shared `app.services.context_hash.compute_context_hash` utility (also used by `app/services/critique_context.py`) over sorted, whitespace-stable JSON, excluding:

- `run_id`
- `context_hash`
- `snapshot_at`

Identical database state produces an identical hash regardless of `run_id` or wall-clock time, so replay/debug tooling can diff two runs against the same ticker/as-of snapshot byte-for-byte.

## Developer command

Use the Docker-wrapped helper:

```bash
make technical-context ticker=NVDA as_of=2026-09-08
```

It prints the full JSON request so the same snapshot can be diffed or stored for offline replay.
