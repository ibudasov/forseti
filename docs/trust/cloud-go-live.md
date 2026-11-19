# Cloud trust check (go-live)

Agents are **enforced** from day one on Cloud Run. ADR 008 limits the blast
radius: agents can only downgrade and never touch risk numbers, so the worst
case is an over-cautious recommendation. This check measures that on cloud
data instead of assuming it.

## How it runs

- Cloud Run job `forseti-trust-check`: same image as the API (`scripts/` is
  included), runs as `forseti-jobs`, production agent modes, Cloud SQL data.
- Entrypoint: `python -m scripts.cloud_trust_check`. It runs the
  `scripts/live_preflight.py` linear-vs-agentic comparison and evaluates the
  trust invariants in `app/trust/invariants.py` for every ticker.
- **Cost gate:** it refuses to start unless `CONFIRM_COST=yes` is passed at
  execution time. The job definition deliberately does not contain it.
- Reports (`<timestamp>.json`, `.md`, `.trust.md`) are written to
  `gs://forseti-505116-artifacts/reports/trust/` through the GCS volume
  mounted at `/gcs`. The bucket is `google_storage_bucket.artifacts`;
  `forseti-jobs` has `storage.objectUser` on it. `reports/` objects expire
  after 180 days. Daily refresh is still GCP-5 and is not part of this job.
- `forseti-verifier` holds `run.jobsExecutorWithOverrides` on the job
  (`identities.json`). `run.invoker` cannot pass `--update-env-vars`, and
  baking `CONFIRM_COST` into the job would weaken the cost gate.
  `/verify full` on #121 executes it as the paid scorecard check
  `exit.cloud-trust`. The owner can also run it by hand:

```bash
gcloud run jobs execute forseti-trust-check --region europe-west4 \
  --update-env-vars CONFIRM_COST=yes --wait
```

Set `TRUST_BASELINE_REPORT` to a local `reports/preflight/*.json` file (GCP-0
baseline) to fill the "Local baseline" column. The command accepts `--tickers`
to narrow the matrix.

## Pass criteria

The job exits non-zero, and the scorecard check fails, when any of these hold:

| Measure | Required |
|---|---|
| Invariant violations | `0` |
| `unparsable_synthesis` runs | `0` |
| Errored (blocked) runs | `0` |
| Unpriced runs | `0` |

The remaining measures are compared with the local baseline and judged
against `docs/trust/criteria.md` (or recorded as documented exceptions):
fallback rate, degraded rate, downgrade rate per agent, p95 latency and cost
per analysis.

## Recorded result

Recorded from `forseti-trust-check-w6vw2` (`exit.cloud-trust`, 5-ticker cloud run
using `gemini-2.5-flash-lite` with enforced `fundamental`, `technical`, and
`critic` modes and typed synthesis).

| Measure | Cloud | Local baseline | Within criteria |
|---|---|---|---|
| Invariant violations (must be 0) | `0` | `n/a` | Yes (`0` violations; ADR 008 safety held across all 5 runs) |
| `unparsable_synthesis` (must be 0) | `0` | `n/a` | Yes (`0` unparsable runs) |
| Fallback rate | `100%` (5/5 `decision_synthesizer`) | `n/a` | Documented exception — `decision_synthesizer` fell back on all 5 runs (2 runs also logged `fundamental_assessment_rejected` / `unknown_metric_citation`); ADR 008 safety held with `0` invariant violations |
| Degraded rate | `100%` (5/5 runs) | `n/a` | Documented exception — `decision_synthesizer` (5/5), `technical_analyst` (5/5), and `fundamental_analyst` (2/5) marked degraded; deterministic fallback preserved `0` violations |
| Downgrade rate: fundamental_analyst | `20%` (1/5) | `n/a` | Yes (downgrade-only authority, `0` upgrades) |
| Downgrade rate: technical_analyst | `0%` (0/5) | `n/a` | Yes (`0` upgrades) |
| Downgrade rate: critic_guardrail | `0%` (0/5) | `n/a` | Yes (`0` upgrades) |
| p95 latency | `26961 ms` | `n/a` | Total-analysis p95 across 5 runs (distinct from `docs/trust/criteria.md` incremental critic `p95 <= 2.0s` over a 100-run shadow sample) |
| Cost per analysis | `$0.0095` (mean; `$0.0477` / 5) | `n/a` | Yes (total-analysis mean `<= $0.01/run` threshold; `0` unpriced runs) |

Evidence: [`/verify full` comment](https://github.com/ibudasov/forseti/issues/121#issuecomment-7018199949) · [Ops Actions run](https://github.com/ibudasov/forseti/actions/runs/44588142674) · execution `forseti-trust-check-w6vw2` · Date: `2026-11-19`
