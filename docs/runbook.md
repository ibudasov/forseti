# Runbook

All commands assume project `forseti-505116`, region `europe-west4`. Cloud
writes go through the pipeline unless a section says "owner".

## Bootstrap from zero (owner)

Follow [`docs/gcp-access.md`](gcp-access.md#owner-bootstrap-in-a-codespace):
`make gcloud-login`, `make gh-login`, copy the tfvars example, run
`make tf-bootstrap` twice (second run: no changes), `make cloud-logout`.
Merge to `main` and approve the `gcp-infra` apply, then let Deploy run.

## Deploy and roll back

Merging to `main` builds the image, runs housekeeping (migrations), then
deploys `forseti-api` with no traffic, smoke-tests it and shifts traffic. To
roll back by hand (owner):

```bash
gcloud run revisions list --service forseti-api --region europe-west4
gcloud run services update-traffic forseti-api --region europe-west4 --to-revisions=<rev>=100
```

## Kill switch

Agents are enforced from day one. Normal path: open a PR changing
`agent_modes` (`off`, `shadow` or `enforced` per agent) and approve the
`gcp-infra` apply.

Emergency path (owner credentials; creates a new revision within seconds):

```bash
gcloud run services update forseti-api --region europe-west4 --update-env-vars CRITIC_MODE=shadow
```

Use `FUNDAMENTAL_AGENT_MODE` or `TECHNICAL_AGENT_MODE` for the other agents.
`PIPELINE_MODE=linear` is the full fallback to deterministic-only. Afterwards
open a PR that makes `agent_modes` match, otherwise the drift check fails.

## Approving an infrastructure apply

In the plan comment or run summary check that: only expected resources change;
nothing is destroyed or replaced unexpectedly (Cloud SQL, secrets); no IAM or
identity change appears (those belong to bootstrap); the SHA is the PR head.

## Migration policy

Migrations are Alembic revisions applied by the `forseti-housekeeping` job
before each deployment, so a migration must be compatible with the previous
API revision (expand, then contract in a later release). See
[`docs/database.md`](database.md).

## Rotating secrets (owner)

- Database passwords: set a new password on the Cloud SQL user, add a new
  Secret Manager version for the matching secret, redeploy the API and jobs so
  they read `latest`, then disable the old secret version.
- Alpha Vantage key: add a new version of `forseti-alpha-vantage-api-key`,
  redeploy, disable the old version.

## Re-running or backfilling the refresh

Until the scheduled refresh exists (GCP-5), run ingestion locally against the
Cloud SQL proxy (`make cloudsql-proxy`, then the `make ingest*` targets). Once
it exists, execute the refresh job with `gcloud run jobs execute` and pass the
date range as arguments.

## Restoring a Cloud SQL backup (owner)

```bash
gcloud sql backups list --instance <instance>
gcloud sql instances create <temporary-instance> ...   # restore into a temporary instance first
gcloud sql backups restore <backup-id> --restore-instance <temporary-instance> --backup-instance <instance>
```

Verify the data, then delete the temporary instance. Restoring over the live
instance is a last resort.

## Scaling

The API runs 0–2 instances, 1 vCPU / 1 GiB, concurrency 4, with
`DB_POOL_SIZE=3` and `DB_MAX_OVERFLOW=2`. Change these in
`infra/terraform/main/run_api.tf` through a PR; keep
instances × (pool + overflow) below the Cloud SQL connection limit.

## Tear-down (owner)

Cloud SQL has deletion protection. To remove the Cloud Run service, jobs and
scheduler for the reproducibility drill, use owner credentials:
`terraform destroy -target=google_cloud_run_v2_service.api -target=google_cloud_run_v2_job.housekeeping -target=google_cloud_run_v2_job.trust_check`
in `infra/terraform/main`. The pipeline recreates them with an approved apply.
Destroying the database requires disabling deletion protection in a PR first.

## Revoking pipeline access and the emergency stop (owner)

Remove a grant in `identities.json` (PR, then `make tf-bootstrap`). To stop
every pipeline at once, disable the federation provider:

```bash
gcloud iam workload-identity-pools providers update-oidc github \
  --workload-identity-pool=github --location=global \
  --project=forseti-505116 --disabled
```

## Monthly cost check

Open the billing report for the project, compare each line with the cost table
in [`architecture-gcp.md`](architecture-gcp.md), check the daily LLM cost alert
history and update the table.

## Scorecard authentication

`/verify` and `/verify full` on #121 must be the entire comment. Ops mints the
verifier's Cloud Run ID token with the auth action and does not print the
service URL or the token. A paid run also needs the main-layer apply that
creates the artifacts bucket and grants `run.jobsExecutorWithOverrides` on
`forseti-trust-check`. See [`docs/gcp-access.md`](gcp-access.md).

## Reproducibility drill (owner)

1. From a fresh clone and following only this runbook, run the tear-down
   command above.
2. Approve the pipeline's apply, then let Deploy redeploy.
3. Post `/verify full` on #121; smoke tests and the scorecard must pass and the
   next drift check must be clean.
4. Optional: restore last night's backup into a temporary instance, then delete it.
