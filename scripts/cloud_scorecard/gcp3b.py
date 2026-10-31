"""GCP-3b checks: the private housekeeping job, its last run and owner-secret isolation."""
from __future__ import annotations

from typing import Any, Dict, Set

from scripts.cloud_scorecard.environment import Environment
from scripts.cloud_scorecard.model import Check, Cost, Outcome, worst_outcome

GROUP = "GCP-3b"
JOB_NAME = "forseti-housekeeping"
JOB_IDENTITY = "forseti-housekeeping"
JOB_RUNNERS = ("forseti-deployer",)
OWNER_SECRETS = ("forseti-owner-database-url", "forseti-app-db-password")
SECRET_READER_ROLE = "roles/secretmanager.secretAccessor"


def _job_query(env: Environment, *command: str) -> list[str]:
    return ["run", "jobs", *command, JOB_NAME, "--region", env.region, "--project", env.project]


def _job_service_account(job: Dict[str, Any]) -> str:
    return job["spec"]["template"]["spec"]["template"]["spec"].get("serviceAccountName", "")


def check_job_identity(env: Environment) -> Outcome:
    job = env.gcloud(_job_query(env, "describe"))
    if job is None:
        return Outcome.pending(f"Cloud Run job {JOB_NAME} does not exist yet")
    if _job_service_account(job) != env.service_account_email(JOB_IDENTITY):
        return Outcome.failed(f"job {JOB_NAME} does not run as {JOB_IDENTITY}")
    return Outcome.passed(f"job {JOB_NAME} runs as {JOB_IDENTITY}")


def _completed_condition(execution: Dict[str, Any]) -> str:
    conditions = execution.get("status", {}).get("conditions", [])
    return next((condition["status"] for condition in conditions if condition["type"] == "Completed"), "Unknown")


def check_last_execution(env: Environment) -> Outcome:
    query = ["run", "jobs", "executions", "list", "--job", JOB_NAME, "--region", env.region,
             "--project", env.project, "--limit", "1"]
    executions = env.gcloud(query) or []
    if not executions:
        return Outcome.pending(f"job {JOB_NAME} has not run yet")
    completed = _completed_condition(executions[0])
    if completed == "True":
        return Outcome.passed("last housekeeping execution succeeded")
    if completed == "Unknown":
        return Outcome.pending("last housekeeping execution is still running")
    return Outcome.failed("last housekeeping execution failed")


def _secret_readers(env: Environment, secret: str) -> Set[str] | None:
    policy = env.gcloud(["secrets", "get-iam-policy", secret, "--project", env.project])
    if policy is None:
        return None
    return {
        member
        for binding in policy.get("bindings", []) if binding["role"] == SECRET_READER_ROLE
        for member in binding.get("members", [])
    }


def check_owner_secret_readers(env: Environment) -> Outcome:
    expected = {f"serviceAccount:{env.service_account_email(JOB_IDENTITY)}"}
    outcomes = []
    for secret in OWNER_SECRETS:
        readers = _secret_readers(env, secret)
        if readers is None:
            outcomes.append(Outcome.pending(f"secret {secret} does not exist yet"))
        elif readers != expected:
            outcomes.append(Outcome.failed(f"secret {secret} is readable by more than {JOB_IDENTITY}"))
    return worst_outcome(outcomes, f"only {JOB_IDENTITY} can read the owner secrets")


def _may_run_job(env: Environment, member: str) -> bool:
    runners = {f"serviceAccount:{env.service_account_email(name)}" for name in JOB_RUNNERS}
    return member in runners or member.startswith("user:")


def check_job_runners(env: Environment) -> Outcome:
    policy = env.gcloud(_job_query(env, "get-iam-policy"))
    if policy is None:
        return Outcome.pending(f"Cloud Run job {JOB_NAME} does not exist yet")
    members = {member for binding in policy.get("bindings", []) for member in binding.get("members", [])}
    if not all(_may_run_job(env, member) for member in members):
        return Outcome.failed(f"job {JOB_NAME} grants access beyond the deployer and the owner")
    return Outcome.passed("only the deployer and the owner hold job-level grants")


CHECKS = (
    Check("gcp3b.job-identity", GROUP, "Housekeeping job exists and runs as forseti-housekeeping", Cost.FREE,
          check_job_identity),
    Check("gcp3b.last-execution", GROUP, "Last housekeeping execution succeeded", Cost.FREE, check_last_execution),
    Check("gcp3b.owner-secrets", GROUP, "Only forseti-housekeeping reads the owner secrets", Cost.FREE,
          check_owner_secret_readers),
    Check("gcp3b.job-runners", GROUP, "Only forseti-deployer and the owner can run the job", Cost.FREE,
          check_job_runners),
)
