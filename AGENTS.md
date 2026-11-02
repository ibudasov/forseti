# Agent Guidelines

Check relevant instructions in the folder `./.github/instructions/*.*`

## Cloud access

- Read-only `gcloud` diagnostics are allowed through an existing owner-authorized session: list/describe resources and read relevant Cloud Logging entries. Prefer a least-privilege read-only identity. Never change cloud resources, IAM, secrets, or authentication configuration, run `terraform apply`, or ask for credentials.
- Start cloud checks with the scorecard comment on ibudasov/forseti#121; use read-only diagnostics when it lacks the necessary detail. An assistant acting with the owner's GitHub account can post `/verify`.
- Never print tokens, read secret values or application table data, or publish raw private logs. Redact credentials, personal data, and private infrastructure identifiers from diagnostic summaries and PRs.
- Tick a Validation box only with a link to evidence: a CI run, a PR plan comment or the scorecard.
- Every new cloud resource comes with its scorecard checks (`scripts/cloud_scorecard/`) and its `identities.json` entries.
- Details: `docs/gcp-access.md`.

## Environment Configuration

Docker Compose is the first-class runtime setup. Every environment variable
defined in `.env` or `.env.example` must be represented in the relevant
Compose service environment and be available inside the container. Use
Compose `env_file` / `--env-from-file` to load the file instead of duplicating
the variable-name list in YAML or Make targets. Keep test-only overrides
explicit in the relevant Makefile test target.
