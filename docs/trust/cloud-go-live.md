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
  mounted at `/gcs` (the bucket arrives with GCP-5).
- `forseti-verifier` holds `run.invoker` on the job (`identities.json`).
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

Fill this in from the `exit.cloud-trust` execution and link the `/verify full`
run. The values below are intentionally empty until the owner has run it.

| Measure | Cloud | Local baseline | Within criteria |
|---|---|---|---|
| Invariant violations (must be 0) | | | |
| `unparsable_synthesis` (must be 0) | | | |
| Fallback rate | | | |
| Degraded rate | | | |
| Downgrade rate: fundamental_analyst | | | |
| Downgrade rate: technical_analyst | | | |
| Downgrade rate: critic_guardrail | | | |
| p95 latency | | | |
| Cost per analysis | | | |

Evidence: _link to the `/verify full` run_ · Date: _YYYY-MM-DD_
