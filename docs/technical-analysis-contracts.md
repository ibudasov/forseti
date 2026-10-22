# Technical analysis contracts

This document defines Forseti's deterministic technical baseline contract. It
is an internal, versioned object that records what checklist rules 5-9
concluded before any future Technical Analyst policy logic is applied. It
mirrors `docs/fundamental-analysis-contracts.md`; see that document for the
equivalent fundamental-side contract.

## Schema

- `schema_version`: `1.0`
- `ticker`: analyzed symbol
- `as_of_date`: the technical feature date (`TechnicalFeature.as_of_date`),
  falling back to the latest price bar's date when there is no technical
  feature row, or `null` when neither is available
- `score`: awarded points from the five deterministic technical rules
- `maximum_score`: always `5`
- `rule_results`: explicit results for all five scored rules, always present
  and always in a fixed order
- `veto_states`: an informational mirror of the analyzer's hard vetoes (see
  below); never re-decides a veto
- `metrics`: normalized metric references keyed by stable `metric_id`
- `warnings`: missing-data warnings such as `no_technical_features`, `no_vix`,
  and `missing_metric:<rule_id>`

## Metric IDs and units

Metric IDs use the format `<metric_name>:<as_of_date>`, for example
`rsi_14:2026-10-21`. When the underlying date cannot be determined (no price
bar and no technical feature row at all), the literal `unknown` is used
instead of a date, for example `vix_close:unknown`.

Defined metrics:

- `close` — latest close price, unit `price`, dated by the price bar's date
- `sma_50` — 50-period simple moving average, unit `price`, dated by the
  technical feature's `as_of_date`
- `sma_200` — 200-period simple moving average, unit `price`, dated by the
  technical feature's `as_of_date`
- `rsi_14` — Wilder RSI-14, unit `rsi_points`, dated by the technical
  feature's `as_of_date`
- `volume_trend` — 20/20 volume trend ratio, unit `ratio`, dated by the
  technical feature's `as_of_date`
- `vix_close` — VIX index close, unit `index_points`, dated by the overall
  `as_of_date` (VIX has no row of its own in this contract)

## Rule IDs and thresholds

- `close_vs_sma50` — pass when `close > sma_50`, worth 1 point
- `close_vs_sma200` — pass when `close > sma_200`, worth 1 point
- `rsi_healthy` — pass when `45 <= rsi_14 <= 65` (`RSI_HEALTHY_MIN` /
  `RSI_HEALTHY_MAX`), worth 1 point
- `volume_trend` — pass when `volume_trend > 1.0`, worth 1 point
- `vix_calm` — pass when `vix_close < 25` (`VIX_CALM_MAX`), worth 1 point

These are checklist rules only. They are distinct from the hard vetoes
applied earlier in the pipeline (`app/services/vetoes.py`): `rsi_overbought`
(RSI > 70), `vix_panic_regime` (VIX > 30), `earnings_too_close` (within 7
days), and `deep_below_sma200` (close more than 10% below SMA-200). Vetoes
can block a trade outright regardless of checklist score; they are not
re-implemented in `app/domain/technicals.py`. `TechnicalVetoState` exists so a
caller that has already computed vetoes can attach them to the contract for
reference, but `evaluate_checklist` does not have the extra veto inputs
(notably the next earnings event) available at the call site today, so
`veto_states` is empty when produced through the checklist. A later issue can
decide whether to thread the full veto inputs through.

## Null semantics

- Missing `TechnicalFeature` row makes every feature-derived rule (`rsi_healthy`,
  `volume_trend`, and both `close_vs_sma*` rules, which also need the SMA half
  of the comparison) `unknown`
- Missing latest price bar makes both `close_vs_sma*` rules `unknown`,
  independent of whether a `TechnicalFeature` row exists
- Missing VIX close makes `vix_calm` `unknown`; it does not affect the other
  four rules
- A single missing value inside an otherwise-present row (for example
  `sma_50 IS NULL`) makes only the rule(s) that need that value `unknown`
- `unknown` and `failed` rules always award zero points
- `passed` rules must cite metric references whose `value` is not null

## Result semantics

Each rule result has:

- `rule_id`
- `status`: `passed`, `failed`, or `unknown`
- `points_awarded`
- `points_available`
- `metric_ids`
- `explanation`

`score` must equal the sum of `points_awarded` across all rule results. The
`explanation` string for a `passed` rule is identical, character for
character, to the `detail` string `evaluate_checklist` has always produced
for that rule; this was a parity requirement for TA-1 (no behaviour change).

## Example

For a ticker whose technical feature row is dated `2026-10-21` with
`rsi_14 = 52.30`, the deterministic baseline contains a metric reference
`rsi_14:2026-10-21` and a rule result:

- `rule_id = "rsi_healthy"`
- `status = "passed"`
- `points_awarded = 1`
- `points_available = 1`
- `metric_ids = ["rsi_14:2026-10-21"]`
- `explanation = "rsi_14: 52.30 in [45, 65]"`
