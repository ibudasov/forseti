# GCP access and Terraform operations

Coding agents change code through PRs. They never receive personal cloud
credentials or service-account keys. Offline PR checks validate configuration;
the owner bootstraps identities, merges reviewed workloads, then approves the
`gcp-infra` apply. Cloud access uses short-lived GitHub OIDC credentials.

## Layers and state

`infra/terraform/bootstrap` owns APIs, identities, federation, GitHub settings,
the budget and the state bucket. **Only the owner applies this layer.**
`infra/terraform/main` owns workloads, starting with Artifact Registry.
CI can apply workloads but cannot change project IAM, create identities or
keys, enable APIs or administer billing.

The versioned `forseti-505116-tfstate` bucket in `europe-west4` has uniform
access, public access prevention, destruction protection and an authoritative
IAM policy without default project-viewer bindings. The owner has bucket
administration. CI reads/writes only objects under `main/`; only the owner
can read `bootstrap/` state. The GCS backend also needs bucket metadata and
object listing to discover workspaces: CI can see object **names**, not the
contents of bootstrap state. The apply identity's project storage-admin grant
explicitly excludes this bucket and all its objects.

State contains sensitive values despite Terraform's `sensitive` display
redaction. Never commit state, saved plans, credentials, real tfvars or
provider debug logs, and never upload plans as workflow artifacts.

## Identities and grants

`infra/terraform/bootstrap/identities.json` is the source of truth consumed by
both layers, this generated documentation, and future live IAM verification
(#133). Run `make iam-doc` after editing it; CI rejects stale tables.

<!-- BEGIN GENERATED IAM -->
| Identity | Purpose | Project roles | May act as | Workflow |
|---|---|---|---|---|
| `forseti-api` | Cloud Run API | aiplatform.user, cloudsql.client, cloudtrace.agent, logging.logWriter, monitoring.metricWriter | None | Runtime only |
| `forseti-jobs` | Cloud Run data jobs | aiplatform.user, cloudsql.client, cloudtrace.agent, logging.logWriter, monitoring.metricWriter | None | Runtime only |
| `forseti-housekeeping` | Database housekeeping; exclusive reader of owner DB secrets (GCP-3b) | cloudsql.client, logging.logWriter | None | Runtime only |
| `forseti-scheduler` | Cloud Scheduler; job invocation added in GCP-5 | None | None | Runtime only |
| `forseti-tf-plan` | Read-only infrastructure plans and drift checks | viewer, iam.securityReviewer | None | infra.yml on main; no environment |
| `forseti-tf-apply` | Approved infrastructure applies; no identity or billing administration | run.admin, cloudsql.admin, secretmanager.admin, artifactregistry.admin, cloudscheduler.admin, monitoring.editor, logging.configWriter, storage.admin (exclude_state_bucket) | forseti-api, forseti-jobs, forseti-housekeeping, forseti-scheduler | infra.yml on main; gcp-infra |
| `forseti-deployer` | Deploy application images from main | run.developer, artifactregistry.writer | forseti-api, forseti-jobs, forseti-housekeeping | deploy.yml on main; gcp-dev |
| `forseti-verifier` | Read-only live operational verification; no secret values or table data | run.viewer, cloudsql.viewer, secretmanager.viewer, iam.securityReviewer, iam.workloadIdentityPoolViewer, logging.viewer, monitoring.viewer, cloudtrace.user, cloudscheduler.viewer, artifactregistry.reader | None | ops.yml on main; no environment |

| State identity | Role | Object prefix |
|---|---|---|
| owner | storage.admin | All |
| CI state identities | storage.legacyBucketReader | Bucket metadata/object names only |
| forseti-tf-plan | storage.objectViewer | `main/` |
| forseti-tf-apply | storage.objectAdmin | `main/` |

| Resource kind | Resource | Member | Role |
|---|---|---|---|
| secret | forseti-database-url | forseti-api | secretmanager.secretAccessor |
| secret | forseti-database-url | forseti-jobs | secretmanager.secretAccessor |
| secret | forseti-alpha-vantage-api-key | forseti-api | secretmanager.secretAccessor |
| secret | forseti-alpha-vantage-api-key | forseti-jobs | secretmanager.secretAccessor |
| secret | forseti-owner-database-url | forseti-housekeeping | secretmanager.secretAccessor |
| secret | forseti-app-db-password | forseti-housekeeping | secretmanager.secretAccessor |
| service | forseti-api | owner | run.invoker |
| service | forseti-api | forseti-deployer | run.invoker |
| service | forseti-api | forseti-verifier | run.invoker |
<!-- END GENERATED IAM -->

Resource grants are initially empty because the later secrets, services,
jobs and buckets do not exist yet. To add one, declare the resource in
`main/`, register its Terraform ID in the corresponding map in `access.tf`,
and add `{kind, resource, member, role}` to `resource_grants`. Supported kinds
are `secret`, `service`, `job` and `bucket`; unknown resources fail planning.
Members are service-account IDs or `owner`, resolved from the sensitive
`owner_email`. Do not add authoritative resource IAM policies to `main/`.

To add an identity, edit the manifest, regenerate docs, review the PR, and
have the owner run bootstrap again. Project roles, conditional roles, act-as
targets and workflow impersonation all originate in that manifest.
Only `exclude_state_bucket` is supported for conditional project grants.
Remove grants through the same review/bootstrap process to revoke access.

Database roles and owner-secret isolation: see
[roles and privileges](database.md#roles-and-privileges). Only `forseti-housekeeping`
reads the owner secrets, and only `forseti-deployer` and the owner can run the job.

## Federation and GitHub settings

Pool/provider: `github/github`. The provider admits only immutable repository
ID `1323914036` and owner ID `3657892`. Each service-account binding uses a
composite `attribute.pipeline`, calculated from the exact workflow path
**at `refs/heads/main`**, ref, subject (including environment) and event.
Subjects use GitHub's immutable `repo:OWNER@OWNER_ID/REPO@REPO_ID` prefix.
Other branches, workflows, environments and PR events map to `denied`.
There are no account keys. Workflow/ref/environment claims are also mapped
individually for inspection.

`gcp-infra` requires the owner's review and accepts only `main`.
`gcp-dev` accepts only `main` without an extra reviewer: merging is its gate.
The owner may approve their own infrastructure deployment; otherwise a
single-owner project would deadlock.

Bootstrap configures these repository variables:
`GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_TF_PLAN_SA`,
`GCP_TF_APPLY_SA`, `GCP_DEPLOYER_SA`, `GCP_VERIFIER_SA`,
`GCP_ARTIFACT_REPO`, `TF_STATE_BUCKET`.
It also configures the masked repository secret `OWNER_EMAIL`, passed to
workload plans/applies as `TF_VAR_owner_email`.

Before approving any Copilot workflow run, inspect changes to
`.github/workflows/` and keep GitHub's default approval requirement for
Copilot-authored PRs. Approving a workflow is a separate decision from
approving its eventual infrastructure apply.

Emergency stop (owner only):

```bash
gcloud iam workload-identity-pools providers update-oidc github \
  --workload-identity-pool=github --location=global \
  --project=forseti-505116 --disabled
```

This blocks new federated credentials; already issued access tokens can
remain valid until expiry. Re-enable deliberately with `--no-disabled`.

## Owner bootstrap in a Codespace

1. Create a Codespace on `main` after #145, or update an existing Codespace
   and run **Codespaces: Rebuild Container**. Check out the infrastructure
   PR with `gh pr checkout NUMBER`. Verify `gcloud --version`,
   `docker --version` and `docker compose version`.
2. Run `make gcloud-login`, then `make gh-login`. Use the owner's account
   with project and billing-budget administration. The Google login writes
   both CLI and application-default credentials (ADC). Never let an agent
   execute commands while those credentials are present.
3. Copy and edit the ignored owner input file:
   `cp infra/terraform/bootstrap/terraform.tfvars.example infra/terraform/bootstrap/terraform.tfvars`.
   Set your email and billing account ID (`gcloud billing accounts list`).
   The EUR budget assumes the billing account uses EUR; verify this first.
   The budget is an alert, **not a hard spending cap**. Its email channel
   may need confirmation; verify receipt/verification in Cloud Monitoring.
4. Run `make tf-bootstrap` and review/confirm the interactive apply.
   Preflight checks ADC validity, the owner's GitHub identity, repository
   administration and access to environments, secrets and variables before
   Terraform starts. The first run uses local state, then migrates it into
   the bucket under `bootstrap/` and removes the local state and backup
   **only after successful migration**. Interrupted runs keep recovery data;
   rerun the same command. A fresh checkout detects the existing bucket
   and reconnects to remote state. Do not delete the bucket or backend file
   as a way to resolve initialization errors.
   The command also initializes the empty `main/` state as owner without
   applying workloads. GCS creates/locks state on first access even for a
   later `-lock=false` plan; doing this now keeps the planner strictly
   read-only from its very first cloud plan.
5. Run `make tf-bootstrap` again: expect **No changes**. Verify the budget
   and email channel, GitHub environments, variables and secret.
6. Run `make cloud-logout`. If you supplied `TF_GITHUB_TOKEN`, also
   `unset TF_GITHUB_TOKEN` in your shell. Logout cannot unset a parent-shell
   environment variable. Verify `gcloud auth list` has no accounts and the
   ADC file is gone before allowing an agent into the Codespace.
7. Re-run offline PR checks, review and merge. The initial workflow must
   first reach `main` before GitHub offers its manual-dispatch controls.
   Approve the pending `gcp-infra` apply to create Artifact Registry.

The Codespace's built-in `GITHUB_TOKEN`/`GH_TOKEN` cannot administer these
settings. `make gh-login` removes both from the login subprocess and disables
interactive setup prompts, so Git credential helpers are not reconfigured.
The web device-code flow still prints sign-in instructions. Bootstrap likewise ignores both variables, using
`TF_GITHUB_TOKEN` if supplied or the owner's stored `gh auth token`.
Prefer the web login with classic `repo` scope. Alternatively use a short-lived
fine-grained token limited to this repository with **Administration, Secrets
and Variables: read/write**, and Metadata: read. GitHub does not expose a
complete fine-grained token permission introspection API: non-mutating
preflight verifies identity/admin and endpoint read access, but the owner
must verify the token's **write** permissions when creating it.
Tokens go only in subprocess environment, never command-line arguments,
Terraform variables, state or committed files.

Other local commands use the pinned Terraform Docker image:
`make tf-fmt`, `make tf-validate dir=bootstrap`, `make tf-validate dir=main`,
`make tf-init dir=main`, and `make tf-plan dir=main`.
Only owner commands mount `~/.config/gcloud`; offline `make infra-check`
does not mount credentials. There is deliberately no workload apply target.
Terraform/provider/TFLint images and provider packages must be downloaded
before checks can run; "offline" means no live cloud calls or cloud credentials,
not an air-gapped dependency installation.

## Infrastructure workflow

PRs run formatting, backend-disabled initialization, validation, TFLint,
policy checks, IAM-doc freshness and offline tooling tests. They do not
authenticate to GCP, plan against live resources, or post cloud plan comments.
This intentionally replaces #124's original automatic PR-plan requirement
per the owner's decision in that issue.

After bootstrap and after the workflow exists on `main`, use
**Actions → Infrastructure → Run workflow**, select branch `main`, operation
`plan`, and optionally enter a same-repository PR number. Without a PR number
it plans `main`; with one it checks out the PR's exact head commit and updates
one plan comment. Forks are rejected. This is deliberate execution of PR
code with a read-only cloud identity: review it before dispatching.
Re-dispatch after every change; a comment is evidence for its recorded SHA,
not necessarily the current PR head.

Pushes changing workloads on `main`, or a manual `apply`, wait for approval
in `gcp-infra`, then plan and apply **the same saved plan**. Delete/replacement
actions are rejected unless a manual apply explicitly sets `allow_destroy`.
Apply concurrency is `terraform-main`, with cancellation disabled.
Saved plans and error captures remain runner-local and are removed on exit.
No plan artifacts are uploaded. The nightly drift check fails on any
non-empty plan; inspect its run summary and open a corrective PR rather
than changing the console by hand.

## Scorecard and ops commands

Agents hold no cloud credentials, so `.github/workflows/ops.yml` checks the
cloud for them. It authenticates as `forseti-verifier` (read-only; no secret
values or table data) and edits **one** comment on issue #121, found by the
hidden marker `<!-- forseti-scorecard -->`. The same Markdown goes to the job
summary. Everything is redacted first: no URLs, emails, project numbers or
long identifiers are printed or posted, because the repository is public.

| Trigger | What it does | Who | Cost |
|---|---|---|---|
| nightly schedule, after the Deploy workflow, `workflow_dispatch` | free checks | automatic / owner | none |
| `/verify` comment on #121 | free checks | repository owner only | none |
| `/verify full` comment on #121 | free and `paid` checks (demo and trust, #129) | repository owner only | a few cents per run |

Comments must match exactly; other authors and other issues are ignored (the
job is skipped). The runner reacts 👀 on the command, then 🚀 on success or
😕 on failure. Statuses: ✅ pass, ❌ exists but is wrong, ⏳ resource not
created yet (milestone not started), ➖ skipped (paid check on `/verify`).

Checks live in `scripts/cloud_scorecard/` (not `scripts/scorecard/`, which is
the product scorecard in `scripts/scorecard.py`). Each has an id, group, cost
flag and a function returning a status plus a short detail. Unit tests replay
recorded `gcloud` JSON from `tests/fixtures/cloud_scorecard/`. A new cloud
resource ships its checks, its group's `later_groups.py` placeholder is
replaced, and its grants are added to `identities.json` (the verifier needs
`run.invoker` on resources it calls).

Agents read the scorecard comment instead of running `gcloud`. An assistant
acting with the owner's GitHub account can post `/verify`. Tick a Validation
box only with a link to evidence: a CI run, a PR plan comment or the scorecard.

Changing the workflow events in `identities.json` requires the owner to run
`make tf-bootstrap` so the federation condition admits them.

## Live acceptance checklist (owner only)

Offline checks cannot establish any of the following. Record actual observed
outputs in the PR/issue after running them, without publishing private data:

- Second bootstrap reports no changes.
- Environment reviewer/branch rules, variables and masked email secret exist.
- Manual PR plan creates/updates a comment for the expected SHA.
- Merge waits for approval; approval creates the registry; drift is green.
- Dispatching privileged workflows from a test branch cannot impersonate
  apply/deployer/verifier; the planner cannot write a disposable resource.
- No user-managed account keys exist; live IAM matches the manifest (#133).
- EUR budget and verified owner notification channel are present.
- No state, real tfvars, tokens, private emails or billing IDs appear in git,
  workflow summaries or artifacts.
