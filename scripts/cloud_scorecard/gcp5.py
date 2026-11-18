"""GCP-5 checks: daily refresh job, Cloud Scheduler, scheduled provenance, freshness, and artifacts bucket."""
from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta, timezone
from importlib import import_module
from typing import Any
from zoneinfo import ZoneInfo

from scripts.cloud_scorecard.environment import Environment
from scripts.cloud_scorecard.model import Check, Cost, Outcome

GROUP = "GCP-5"
JOB_NAME = "forseti-daily-refresh"
SCHEDULER_NAME = "forseti-daily-refresh"
EXPECTED_SCHEDULE = "30 18 * * 1-5"
EXPECTED_TIME_ZONE = "America/New_York"
EXPECTED_JOB_SA = "forseti-jobs"
EXPECTED_SCHEDULER_SA = "forseti-scheduler"
EXPECTED_ENV = {
    "RAW_DOCUMENT_DIR": "/mnt/artifacts/raw",
    "REPORT_DIR": "/mnt/artifacts/reports",
    "INGEST_REPORT_PATH": "/mnt/artifacts/reports/ingest/coverage-latest.json",
    "EARNINGS_FALLBACK_ENABLED": "true",
}
FRESHNESS_MAX_AGE = timedelta(hours=26)
RUN_GRACE_PERIOD = timedelta(minutes=30)
ET_ZONE = ZoneInfo("America/New_York")
_XNYS_CALENDAR: Any = None


def _xnys_calendar() -> Any:
    global _XNYS_CALENDAR
    if _XNYS_CALENDAR is None:
        xcals = import_module("exchange_calendars")
        _XNYS_CALENDAR = xcals.get_calendar("XNYS")
    return _XNYS_CALENDAR


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _completed_condition(execution: dict[str, Any]) -> str:
    conditions = execution.get("status", {}).get("conditions", [])
    return next((c.get("status", "Unknown") for c in conditions if c.get("type") == "Completed"), "Unknown")


def _execution_name(execution: dict[str, Any]) -> str:
    return str(execution.get("metadata", {}).get("name") or execution.get("name") or "").rsplit("/", 1)[-1]


def _execution_timestamp(execution: dict[str, Any]) -> datetime | None:
    status = execution.get("status", {})
    metadata = execution.get("metadata", {})
    return (
        _parse_timestamp(status.get("completionTime"))
        or _parse_timestamp(status.get("startTime"))
        or _parse_timestamp(metadata.get("creationTimestamp"))
        or _parse_timestamp(execution.get("createTime"))
    )


def check_scheduler(env: Environment) -> Outcome:
    scheduler = env.gcloud([
        "scheduler", "jobs", "describe", SCHEDULER_NAME,
        "--location", env.region, "--project", env.project,
    ])
    if scheduler is None:
        return Outcome.pending(f"Cloud Scheduler job {SCHEDULER_NAME} does not exist yet")

    problems: list[str] = []
    if scheduler.get("state") != "ENABLED":
        problems.append(f"state={scheduler.get('state')}")
    if scheduler.get("schedule") != EXPECTED_SCHEDULE:
        problems.append(f"schedule={scheduler.get('schedule')}")
    if scheduler.get("timeZone") != EXPECTED_TIME_ZONE:
        problems.append(f"timeZone={scheduler.get('timeZone')}")

    retry_count = scheduler.get("retryConfig", {}).get("retryCount")
    if retry_count != 1:
        problems.append(f"retryCount={retry_count}")

    http_target = scheduler.get("httpTarget", {})
    expected_suffix = f"/projects/{env.project}/locations/{env.region}/jobs/{JOB_NAME}:run"
    if not str(http_target.get("uri", "")).endswith(expected_suffix):
        problems.append("unexpected target URI")

    oauth_email = http_target.get("oauthToken", {}).get("serviceAccountEmail", "")
    if oauth_email != env.service_account_email(EXPECTED_SCHEDULER_SA):
        problems.append("unexpected scheduler service account")

    if problems:
        return Outcome.failed(f"Cloud Scheduler {SCHEDULER_NAME} misconfigured: {', '.join(problems)}")
    return Outcome.passed(f"Cloud Scheduler {SCHEDULER_NAME} is enabled with {EXPECTED_SCHEDULE} {EXPECTED_TIME_ZONE}")


def _job_template_spec(job: dict[str, Any]) -> dict[str, Any]:
    return (
        job.get("spec", {}).get("template", {}).get("spec", {}).get("template", {}).get("spec")
        or job.get("template", {}).get("template", {})
        or {}
    )


def check_job(env: Environment) -> Outcome:
    job = env.gcloud([
        "run", "jobs", "describe", JOB_NAME,
        "--region", env.region, "--project", env.project,
    ])
    if job is None:
        return Outcome.pending(f"Cloud Run job {JOB_NAME} does not exist yet")

    spec = _job_template_spec(job)
    service_account = spec.get("serviceAccountName") or spec.get("serviceAccount") or ""
    containers = spec.get("containers", [])
    env_map = {
        item["name"]: str(item.get("value", ""))
        for item in (containers[0].get("env", []) if containers else [])
        if "name" in item
    }
    mounts = {
        mount.get("mountPath"): mount.get("name")
        for mount in (containers[0].get("volumeMounts", []) if containers else [])
    }
    volumes = {vol.get("name"): vol for vol in spec.get("volumes", []) if "name" in vol}

    problems: list[str] = []
    if service_account != env.service_account_email(EXPECTED_JOB_SA):
        problems.append("unexpected service account")
    for key, expected_val in EXPECTED_ENV.items():
        if env_map.get(key) != expected_val:
            problems.append(f"{key}!={expected_val}")

    artifacts_vol_name = mounts.get("/mnt/artifacts")
    if not artifacts_vol_name:
        problems.append("missing /mnt/artifacts mount")
    else:
        bucket_name = (
            volumes.get(artifacts_vol_name, {}).get("gcs", {}).get("bucket")
            or volumes.get(artifacts_vol_name, {}).get("csi", {}).get("volumeAttributes", {}).get("bucketName")
        )
        if bucket_name and bucket_name != f"{env.project}-artifacts":
            problems.append("wrong artifacts bucket mount")

    if "/cloudsql" not in mounts:
        problems.append("missing /cloudsql mount")

    if problems:
        return Outcome.failed(f"Cloud Run job {JOB_NAME} misconfigured: {', '.join(problems)}")
    return Outcome.passed(f"Cloud Run job {JOB_NAME} configured with {EXPECTED_JOB_SA} and required mounts")


def _scheduler_triggered_names_and_times(env: Environment) -> tuple[set[str], list[datetime]]:
    scheduler_email = env.service_account_email(EXPECTED_SCHEDULER_SA)
    filter_expr = (
        'protoPayload.methodName:"RunJob" '
        f'AND protoPayload.resourceName:"jobs/{JOB_NAME}" '
        f'AND protoPayload.authenticationInfo.principalEmail="{scheduler_email}"'
    )
    entries = env.gcloud([
        "logging", "read", filter_expr,
        "--freshness=30d", "--limit=20", "--project", env.project,
    ]) or []

    names: set[str] = set()
    timestamps: list[datetime] = []
    for entry in entries:
        proto = entry.get("protoPayload", {})
        resp_name = proto.get("response", {}).get("name") or proto.get("response", {}).get("metadata", {}).get("name")
        if resp_name:
            names.add(str(resp_name).rsplit("/", 1)[-1])
        ts = _parse_timestamp(entry.get("timestamp"))
        if ts is not None:
            timestamps.append(ts)
    return names, timestamps


def _is_scheduled_execution(
    execution: dict[str, Any],
    scheduler_email: str,
    audit_names: set[str],
    audit_times: list[datetime],
) -> bool:
    annotations = execution.get("metadata", {}).get("annotations", {})
    creator = annotations.get("run.googleapis.com/creator", "")
    if creator == scheduler_email:
        return True
    name = _execution_name(execution)
    if name and name in audit_names:
        return True
    created = _parse_timestamp(
        execution.get("metadata", {}).get("creationTimestamp") or execution.get("createTime")
    )
    if created is not None and any(abs((created - audit_ts).total_seconds()) <= 120 for audit_ts in audit_times):
        return True
    return False


def _filter_scheduled_executions(env: Environment, executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scheduler_email = env.service_account_email(EXPECTED_SCHEDULER_SA)
    audit_names, audit_times = _scheduler_triggered_names_and_times(env)
    return [
        ex for ex in executions
        if _is_scheduled_execution(ex, scheduler_email, audit_names, audit_times)
    ]


def _list_executions(env: Environment) -> list[dict[str, Any]]:
    return env.gcloud([
        "run", "jobs", "executions", "list",
        "--job", JOB_NAME, "--region", env.region, "--project", env.project, "--limit", "20",
    ]) or []


def check_scheduled_runs(env: Environment) -> Outcome:
    executions = _list_executions(env)
    if not executions:
        return Outcome.pending(f"job {JOB_NAME} has not run yet")

    scheduled = _filter_scheduled_executions(env, executions)
    if not scheduled:
        return Outcome.pending("no scheduled refresh executions recorded yet (manual runs excluded)")

    latest = scheduled[:2]
    for ex in latest:
        if _completed_condition(ex) == "False":
            return Outcome.failed("a recent scheduled daily refresh execution failed")
    if any(_completed_condition(ex) == "Unknown" for ex in latest):
        return Outcome.pending("a recent scheduled daily refresh execution is still running")
    if len(scheduled) < 2:
        return Outcome.pending("only 1/2 scheduled daily refresh executions succeeded so far")
    return Outcome.passed("last 2 scheduled daily refresh executions succeeded")


def _latest_expected_scheduled_run(now_utc: datetime) -> datetime:
    now_et = now_utc.astimezone(ET_ZONE)
    candidate_date = now_et.date()
    pd = import_module("pandas")
    sessions = _xnys_calendar().sessions_in_range(
        pd.Timestamp(candidate_date - timedelta(days=14)),
        pd.Timestamp(candidate_date),
    )
    for session in reversed(sessions):
        session_et = datetime.combine(session.date(), dt_time(18, 30), tzinfo=ET_ZONE)
        if session_et + RUN_GRACE_PERIOD <= now_et:
            return session_et.astimezone(timezone.utc)
    return (now_et - FRESHNESS_MAX_AGE).astimezone(timezone.utc)


def check_freshness(env: Environment, *, now: datetime | None = None) -> Outcome:
    executions = _list_executions(env)
    scheduled = _filter_scheduled_executions(env, executions) if executions else []
    successful_times = [
        ts
        for ex in scheduled
        if _completed_condition(ex) == "True"
        for ts in [_execution_timestamp(ex)]
        if ts is not None
    ]
    if not successful_times:
        return Outcome.pending("no successful scheduled refresh execution recorded yet")

    now_utc = now or datetime.now(timezone.utc)
    latest_success = max(successful_times)
    expected_run = _latest_expected_scheduled_run(now_utc)

    # Fresh if we succeeded for the latest completed XNYS session (even across weekends/holidays),
    # or within the 26-hour window.
    if latest_success >= expected_run - RUN_GRACE_PERIOD:
        return Outcome.passed("scheduled daily refresh covers the latest XNYS trading session")
    if now_utc - latest_success <= FRESHNESS_MAX_AGE:
        return Outcome.passed("scheduled daily refresh succeeded within the last 26 hours")

    return Outcome.failed("scheduled daily refresh missed the latest XNYS session (>26h stale)")


def check_artifacts_bucket(env: Environment) -> Outcome:
    bucket_name = f"{env.project}-artifacts"
    bucket = env.gcloud(["storage", "buckets", "describe", f"gs://{bucket_name}"])
    if bucket is None:
        return Outcome.pending(f"artifacts bucket {bucket_name} does not exist yet")

    iam_cfg = bucket.get("iamConfiguration", {})
    ubla = iam_cfg.get("uniformBucketLevelAccess", {}).get("enabled") is True
    pap = iam_cfg.get("publicAccessPrevention") == "enforced"
    rules = bucket.get("lifecycle", {}).get("rule", [])
    has_180d_reports_lifecycle = any(
        rule.get("action", {}).get("type") == "Delete"
        and rule.get("condition", {}).get("age") == 180
        and "reports/" in (rule.get("condition", {}).get("matchesPrefix") or [])
        for rule in rules
    )

    problems: list[str] = []
    if not ubla:
        problems.append("uniformBucketLevelAccess disabled")
    if not pap:
        problems.append("publicAccessPrevention not enforced")
    if not has_180d_reports_lifecycle:
        problems.append("missing 180-day Delete lifecycle rule for reports/")
    if problems:
        return Outcome.failed(f"artifacts bucket policy issue: {', '.join(problems)}")

    latest_report = env.gcloud([
        "storage", "objects", "describe",
        f"gs://{bucket_name}/reports/ingest/coverage-latest.json",
    ])
    if latest_report is None:
        return Outcome.pending("artifacts bucket policy valid; coverage-latest.json not written yet")
    return Outcome.passed("artifacts bucket policy/lifecycle valid and coverage-latest.json exists")


CHECKS = (
    Check("gcp5.scheduler", GROUP, "Daily refresh Cloud Scheduler configured", Cost.FREE, check_scheduler),
    Check("gcp5.job", GROUP, "Daily refresh Cloud Run job configured", Cost.FREE, check_job),
    Check("gcp5.scheduled-runs", GROUP, "Last 2 scheduled refresh runs succeeded", Cost.FREE, check_scheduled_runs),
    Check("gcp5.freshness", GROUP, "Scheduled refresh freshness (XNYS-aware)", Cost.FREE, check_freshness),
    Check("gcp5.artifacts-bucket", GROUP, "Artifacts bucket policy and latest report", Cost.FREE, check_artifacts_bucket),
)
