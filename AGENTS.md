# Agent Guidelines

Check relevant instructions in the folder `./.github/instructions/*.*`

## Cloud access

- Never run `gcloud` or `terraform apply` against the project, and never ask for credentials.
- To check the cloud, read the scorecard comment on ibudasov/forseti#121. An assistant acting with the owner's GitHub account can post `/verify`.
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
