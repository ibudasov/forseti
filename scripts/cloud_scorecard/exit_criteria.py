"""Week 10 exit criteria: stable private API, cloud trust, demo, reproducibility and documentation."""
from __future__ import annotations

import time
from typing import Any, Mapping

from scripts import demo_check
from scripts.cloud_scorecard import gcp2, gcp4, gcp6
from scripts.cloud_scorecard.environment import Environment
from scripts.cloud_scorecard.model import Check, Cost, Outcome, worst_outcome

GROUP = "Exit criteria"
TRUST_JOB = "forseti-trust-check"
# ID tokens minted for /verify full last 1 hour. This poll runs before the demo
# check reuses that token, so 1800s stays inside the token lifetime.
TRUST_JOB_TIMEOUT_SECONDS = 1800
TRUST_POLL_INTERVAL_SECONDS = 20
GOOGLE_MANAGED_ACCOUNT = r"@(gcp-sa-[a-z0-9-]+\.iam|cloudservices|developer)\.gserviceaccount\.com$"
MUTATING_METHOD = r"(?i)(create|insert|update|patch|delete|setIamPolicy|undelete|replace)"
IGNORED_CONNECT_METHOD = "cloudsql.instances.connect"
MUTATING_ROLE_MARKERS = ("admin", "developer", "editor", "writer")
DOC_COVERAGE_JOB = "Documentation coverage"
NIGHTLY_RUNS_PATH = (
    "repos/{repository}/actions/workflows/infra.yml/runs?event=schedule&branch=main&status=completed&per_page=1"
)


def check_stable_private_agents(env: Environment) -> Outcome:
    return worst_outcome(
        [
            gcp4.check_runtime_configuration(env),
            gcp4.check_private_api_access(env),
            gcp6.check_no_recent_api_5xx(env),
        ],
        "private API serves enforced agents with typed synthesis and no 5xx in 7 days",
    )


def _job_query(env: Environment, *command: str) -> list[str]:
    return ["run", "jobs", *command, "--region", env.region, "--project", env.project]


def _completed_condition(execution: dict[str, Any]) -> str:
    conditions = execution.get("status", {}).get("conditions", [])
    return next((condition["status"] for condition in conditions if condition["type"] == "Completed"), "Unknown")


def _await_execution(env: Environment, execution_name: str) -> str:
    deadline = time.monotonic() + TRUST_JOB_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        execution = env.gcloud(_job_query(env, "executions", "describe", execution_name)) or {}
        completed = _completed_condition(execution)
        if completed != "Unknown":
            return completed
        time.sleep(TRUST_POLL_INTERVAL_SECONDS)
    return "Unknown"


def check_cloud_trust(env: Environment) -> Outcome:
    if env.gcloud(_job_query(env, "describe", TRUST_JOB)) is None:
        return Outcome.pending(f"Cloud Run job {TRUST_JOB} does not exist yet")
    execution = env.gcloud(_job_query(env, "execute", TRUST_JOB, "--update-env-vars", "CONFIRM_COST=yes"))
    execution_name = execution["metadata"]["name"]
    completed = _await_execution(env, execution_name)
    if completed == "True":
        return Outcome.passed(f"0 invariant violations and 0 unparsable_synthesis; comparison in {execution_name} logs")
    if completed == "Unknown":
        return Outcome.failed(f"{execution_name} did not finish within {TRUST_JOB_TIMEOUT_SECONDS} seconds")
    return Outcome.failed(f"{execution_name} reported trust violations or errors; see its logs")


def check_demo(env: Environment) -> Outcome:
    service = gcp4._service(env)
    if service is None:
        return Outcome.pending(f"Cloud Run service {gcp4.SERVICE_NAME} does not exist yet")
    service_url = gcp4._service_url(service).rstrip("/")
    if not service_url:
        return Outcome.pending(f"Cloud Run service {gcp4.SERVICE_NAME} has no URL yet")
    call = demo_check.authenticated_api_call(service_url, gcp4._identity_token(service_url))
    try:
        decisions = demo_check.run_demo(call, demo_check.DEFAULT_TICKERS)
    except demo_check.DemoCheckError as error:
        return Outcome.failed(str(error))
    summary = ", ".join(f"{ticker}={decision}" for ticker, decision in decisions.items())
    return Outcome.passed(f"every run persisted with a trace ({summary})")


def _role_can_mutate(role: str) -> bool:
    normalized = role.lower()
    return any(marker in normalized for marker in MUTATING_ROLE_MARKERS)


def _resource_mutators(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """Pipeline identities allowed to change resources, from identities.json.

    Runtime identities have no workflow and are never mutators, so a new
    runtime account is treated the same way without a hardcoded exception.
    Read-only workflow identities (planner, verifier) are not mutators either.
    """
    mutators = []
    for name, account in manifest["service_accounts"].items():
        if account.get("workflow") is None:
            continue
        roles = account.get("project_roles", [])
        if any(_role_can_mutate(role) for role in roles):
            mutators.append(name)
    return tuple(sorted(mutators))


def _unexpected_writers_filter(manifest: Mapping[str, Any]) -> str:
    principal = "protoPayload.authenticationInfo.principalEmail"
    clauses = [
        'logName:"cloudaudit.googleapis.com%2Factivity"',
        f'protoPayload.methodName=~"{MUTATING_METHOD}"',
        f'NOT protoPayload.methodName="{IGNORED_CONNECT_METHOD}"',
    ]
    mutators = _resource_mutators(manifest)
    if mutators:
        pipeline = "|".join(mutators)
        clauses.append(f'NOT {principal}=~"^({pipeline})@"')
    clauses.append(f'NOT {principal}=~"{GOOGLE_MANAGED_ACCOUNT}"')
    return " AND ".join(clauses)


def check_reproducible_deployment(env: Environment) -> Outcome:
    drift = gcp2.check_nightly_drift(env)
    entries = env.gcloud([
        "logging", "read", _unexpected_writers_filter(env.manifest),
        "--freshness=7d", "--limit=1", "--project", env.project,
    ]) or []
    if entries:
        return worst_outcome(
            [drift, Outcome.failed("admin-activity audit logs show a change from outside the pipeline identities")], ""
        )
    return worst_outcome([drift], "drift check is green and only pipeline identities changed resources in 7 days")


def check_doc_coverage(env: Environment) -> Outcome:
    runs = (env.github([NIGHTLY_RUNS_PATH.format(repository=env.repository)]) or {}).get("workflow_runs", [])
    if not runs:
        return Outcome.pending("no nightly infrastructure run has completed yet")
    jobs = (env.github([f"repos/{env.repository}/actions/runs/{runs[0]['id']}/jobs"]) or {}).get("jobs", [])
    coverage = next((job for job in jobs if job["name"] == DOC_COVERAGE_JOB), None)
    if coverage is None:
        return Outcome.pending("the latest nightly run has no documentation coverage job yet")
    if coverage["conclusion"] != "success":
        return Outcome.failed(f"documentation coverage concluded {coverage['conclusion']}")
    return Outcome.passed("docs/architecture-gcp.md mentions every deployed resource")


CHECKS = (
    Check("exit.stable-private-agents", GROUP,
          "Private API is stable with enforced agents and typed synthesis", Cost.FREE, check_stable_private_agents),
    Check("exit.cloud-trust", GROUP, "Cloud trust check: zero invariant violations and zero unparsable synthesis",
          Cost.PAID, check_cloud_trust),
    Check("exit.demo", GROUP, "Demo API steps pass for three tickers with persisted runs and traces",
          Cost.PAID, check_demo),
    Check("exit.reproducible", GROUP, "Drift is clean and only pipeline identities changed resources",
          Cost.FREE, check_reproducible_deployment),
    Check("exit.doc-coverage", GROUP, "Architecture doc covers every deployed resource", Cost.FREE, check_doc_coverage),
)
