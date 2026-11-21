Decisions for the Week 10 GCP deployment. Implementation epic: [#121 Week 10 – Deploy Forseti to GCP](https://github.com/ibudasov/forseti/issues/121)

## Decisions

| # | Topic | Decision |
| --- | --- | --- |
| 1 | Audience | Private, Igor only. No public URL; Cloud Run IAM invoker + `gcloud run services proxy` |
| 2 | Database | <b>Cloud SQL for PostgreSQL 15</b>, `db-f1-micro`, zonal, pgvector (about \$9–12/month) |
| 3 | Data refresh | Mon–Fri 18:30 New York time (after the US close), Cloud Scheduler → Cloud Run Job |
| 4 | Agent modes in prod | <b>Enforced</b> from day one (all agents are downgrade-only per ADR 008) |
| 5 | Infrastructure | Terraform wherever possible; CI/CD via GitHub Actions with keyless Workload Identity Federation |
| 6 | Runtime | Week 10: one container on Cloud Run. Week 11: conversational agent on Agent Engine. Week 12: Gemini Enterprise |
| 7 | Region | `europe-west4`, matching the Vertex AI config |
| 8 | Database access | The app and data jobs connect as `forseti_app` (read/write data only, no DDL). Extensions, migrations and roles run only in the private Cloud Run Job `forseti-housekeeping`, as `forseti_owner` |
| 9 | DB connections | One shared pool per process (pool 3 + overflow 2), at most 20 of the \~25 connections on `db-f1-micro` |
| 10 | `.env` | Stays committed as it is (no real secrets in it) |
| 11 | Agent access | Agents never hold GCP credentials or keys. They change things only through PRs; GitHub Actions does the cloud work with short-lived identities, each usable by one workflow ([#124](https://github.com/ibudasov/forseti/issues/124)) |
| 12 | Terraform apply | CI plans every PR. After merge, CI applies once you approve the `gcp-infra` deployment (one click). Identities, IAM roles, APIs, GitHub settings and the budget live in `bootstrap/`, which only you apply (`make tf-bootstrap`). All access is defined in `identities.json` and documented in the generated `docs/gcp-access.md` ([#124](https://github.com/ibudasov/forseti/issues/124)) |
| 13 | Progress tracking | A scorecard comment on [#121](https://github.com/ibudasov/forseti/issues/121), refreshed nightly, after each deploy and on `/verify` ([#133](https://github.com/ibudasov/forseti/issues/133)). Validation boxes are ticked only with evidence |

Considered and not chosen: Neon (outside GCP), self-managed Postgres on a VM (you'd have to handle backups and patching yourself).

## Roadmap

| Step | Issue | Milestone | Size | Depends on |
| --- | --- | --- | --- | --- |
| PRE-1 | [#130](https://github.com/ibudasov/forseti/issues/130) | Dependency hygiene: remove stray packages, `pyproject.toml` as the single source, hashed locks | S | — |
| PRE-2 | [#131](https://github.com/ibudasov/forseti/issues/131) | Accurate cost tracking: pricing catalog, per-run cost on `agent_runs` | M | — |
| GCP-0 | [#122](https://github.com/ibudasov/forseti/issues/122) | Live pre-flight: first real end-to-end run locally, agents enforced | S | — (soft: PRE-2 for cost) |
| GCP-1 | [#123](https://github.com/ibudasov/forseti/issues/123) | Cloud-ready container and runtime config, incl. connection reuse | M | PRE-1 |
| GCP-2 | [#124](https://github.com/ibudasov/forseti/issues/124) | Terraform foundation: two layers, identities as code, keyless CI, plan on PRs and apply after one-click approval, budget | L | — |
| GCP-2b | [#133](https://github.com/ibudasov/forseti/issues/133) | Status scorecard and `/verify` commands: evidence for agents and the exit criteria | M | GCP-2 |
| GCP-3 | [#125](https://github.com/ibudasov/forseti/issues/125) | Cloud SQL + Secret Manager | M | GCP-2 |
| GCP-3b | [#132](https://github.com/ibudasov/forseti/issues/132) | Private "Forseti Housekeeping" Cloud Run Job: extensions, migrations, DB roles | M | GCP-1, 2, 3 |
| GCP-4 | [#126](https://github.com/ibudasov/forseti/issues/126) | Cloud Run API service and deploy pipeline (runs housekeeping first) | L | GCP-1, 2, 3, 3b |
| GCP-5 | [#127](https://github.com/ibudasov/forseti/issues/127) | Daily data refresh: Cloud Run Jobs, Scheduler, Cloud Storage | M | GCP-4 |
| GCP-6 | [#128](https://github.com/ibudasov/forseti/issues/128) | Observability: Cloud Trace, structured logs, dashboard, alerts | M | GCP-4, PRE-2 |
| GCP-7 | [#129](https://github.com/ibudasov/forseti/issues/129) | Go-live: cloud trust check, demo, architecture doc, runbook | M | all |

PRE-1, PRE-2, GCP-0 and GCP-2 can start now. GCP-1 follows PRE-1, GCP-3 follows GCP-2, and GCP-3b needs both before GCP-4. GCP-2b follows GCP-2; each later step adds its checks to it.

## Done when

- [ ] Stable private environment on GCP with agents enforced
- [ ] Architecture doc and runbook
- [ ] Reproducible deployment (Terraform + CD, no drift)
- [ ] Working end-to-end demo
- [ ] Cloud trust check passes

---

_Migrated from Notion on 2026-11-12._
