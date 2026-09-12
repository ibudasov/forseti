# Fundamental analysis contracts

This document defines Forseti's deterministic fundamental baseline contract. It is an internal, versioned object that records what the rules engine concluded before any future Fundamental Analyst policy logic is applied.

## Schema

- `schema_version`: `1.0`
- `ticker`: analyzed symbol
- `as_of_date`: period end for the normalized fundamentals snapshot, or `null` when no snapshot exists
- `score`: awarded points from deterministic fundamental rules
- `maximum_score`: always `6`
- `rule_results`: explicit results for all four scored rules
- `warnings`: missing-data warnings such as `no_fundamental_snapshot` and `missing_metric:<rule_id>`
- `metrics`: normalized metric references keyed by stable `metric_id`

## Metric IDs and units

Metric IDs use the format `<metric-name>:<period-end>`, for example `revenue_growth:2025-12-31`.

Defined metrics:

- `revenue_growth` — Revenue growth, unit `ratio`
- `fcf` — Free cash flow, unit is the security currency
- `debt_to_equity` — Debt to equity, unit `ratio`
- `eps_trend` — EPS trend, unit `currency_per_share_delta`
- `margins` — Margins, unit `ratio`

## Rule IDs and thresholds

- `revenue_growth` — pass when `revenue_growth > 0.15`, worth 2 points
- `fcf` — pass when `fcf > 0`, worth 2 points
- `debt_to_equity` — pass when `debt_to_equity < 1.0`, worth 1 point
- `eps_trend` — pass when `eps_trend > 0`, worth 1 point

## Null semantics

- Missing snapshot (`as_of_date = null`) makes every rule `unknown`
- Missing metric values make only the affected rules `unknown`
- `unknown` and `failed` rules award zero points
- `passed` rules must cite non-null metric references

## Result semantics

Each rule result has:

- `rule_id`
- `status`: `passed`, `failed`, or `unknown`
- `points_awarded`
- `points_available`
- `metric_ids`
- `explanation`

`score` must equal the sum of `points_awarded` across all rule results.

## Example

For a snapshot dated `2025-12-31`, the deterministic baseline may contain metric references such as `fcf:2025-12-31` and a rule result like:

- `rule_id = "fcf"`
- `status = "passed"`
- `points_awarded = 2`
- `points_available = 2`
- `metric_ids = ["fcf:2025-12-31"]`
- `explanation = "fcf: 21000000.00 > 0"`
