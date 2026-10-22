# Trust criteria

These thresholds start from `docs/critic-evaluation.md` and turn the rollout gate
into numeric checks that later trust sub-issues can measure the same way every
time.

## Minimum evidence set

Before any `shadow -> enforced` recommendation is considered, collect all of:

- frozen offline suite: `12/12` critic golden cases passing
- shadow sample: at least `100` persisted `mode=shadow` runs
- repeatability sample: `10` tickers x `5` identical runs each
- stability window: `14` consecutive days with no unexplained severity spike

## Numeric thresholds

| Criterion | Threshold | How to measure |
| --- | --- | --- |
| Risk-number equality | `100%` exact equality for `entry_range`, `stop_loss`, `take_profit`, `risk_reward`, and `position_size_eur` between deterministic baseline and agentic result | Compare serialized JSON fields for every golden case and every sampled shadow/enforced run; any mismatch fails the gate |
| Decision upgrades | `0` upgrades | Count final decisions whose rank exceeds the deterministic baseline; the accepted count must remain zero |
| Unsupported claims | `0` accepted unsupported claims in frozen fixtures; `<= 5%` false-positive `unsupported_claim` objections in a manually reviewed shadow sample of at least `50` objections | Use fixture outcomes for the hard zero gate, then manual review for field quality |
| Repeat-run decision flip rate | `<= 1` ticker out of `10` may show any final-decision flip across `5` identical runs | For each ticker, compare the 5 final decisions; more than 1 unstable ticker fails |
| Confidence variance | Per-ticker final-confidence standard deviation `<= 0.05` across the `5` identical runs | Compute sample standard deviation on the same repeatability sample |
| Fallback safety | `100%` of forced fallback runs must return either the exact deterministic response or a strict downgrade; `0` 5xx responses; `0` risk-number mutations | Inject provider/schema/timeout failures and compare final output to the deterministic baseline |
| Latency per run | Critic incremental latency `p95 <= 2.0s` over the `100`-run shadow sample | Subtract the no-critic baseline from the full agentic run latency and evaluate the p95 |
| Cost per run | Mean estimated critic cost `<= $0.01/run` and `p95 <= $0.02/run` over the same `100`-run sample | Use persisted token-usage/cost telemetry from the critic step only |

## Hard-stop rules

Any single occurrence below blocks promotion immediately:

- a decision upgrade
- any risk-number mutation
- a fallback path that strengthens the deterministic answer
- a missing or failing golden case
