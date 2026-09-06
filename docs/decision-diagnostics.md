# Decision diagnostics

Every deterministic analyzer result carries a structured `diagnosis`. Screening
exposes its compact `debug_reason` by default and the full diagnosis with
`GET /screening?verbose=true`.

## Diagnosis stages

The stage vocabulary is fixed:

- `unknown_security`: `ticker_not_found`
- `data_gate`: `security_inactive`, `no_price_data`,
  `insufficient_price_data` (with non-blocking warnings
  `stale_price_data`, `no_technical_features`, `no_fundamentals`, and
  `no_earnings_data`)
- `hard_veto`: veto rule identifiers such as `earnings_too_close` and
  `rsi_overbought`
- `checklist`: `score_below_watchlist`, `score_below_trade`, or
  `checklist_passed`; the underlying checks are `revenue_growth`, `fcf`,
  `debt_to_equity`, `eps_trend`, `close_vs_sma50`, `close_vs_sma200`,
  `rsi_healthy`, `volume_trend`, and `vix_calm`
- `risk_math`: risk downgrade identifiers

The compact form is always one line and at most 200 characters:
`<stage>/<rule_id>: <detail>`. The full object also includes the checklist
score, its maximum, and normalized missing-data indicators.

## Universe report

`GET /diagnostics/universe` reports coverage for every active security and a
`blocked_by` histogram. The histogram groups each ticker by the diagnosis that
stopped it; traded tickers are counted under `passed`. The bucket total must
equal `universe_size`, which makes the report self-checking.

For example, many `data_gate/insufficient_price_data` entries indicate a data
coverage problem. If coverage is complete but most entries are
`checklist/score_below_watchlist`, the inputs are present and the result is a
signal/checklist outcome instead.
