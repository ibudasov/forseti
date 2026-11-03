"""GCP-6 checks: logs-based observability, dashboards, alerts and trace coverage."""
from __future__ import annotations

from typing import Any

from scripts.cloud_scorecard.environment import Environment
from scripts.cloud_scorecard.model import Check, Cost, Outcome

GROUP = "GCP-6"
EXPECTED_METRICS = {
    "forseti/analysis_count",
    "forseti/analysis_latency_ms",
    "forseti/analysis_cost_usd",
    "forseti/analysis_confidence",
    "forseti/analysis_degraded_count",
    "forseti/agent_degraded_count",
    "forseti/agent_downgrade_count",
    "forseti/daily_refresh_status",
}
EXPECTED_ALERTS = {
    "Forseti daily refresh failed",
    "Forseti daily refresh missing",
    "Forseti API returned a 5xx",
    "Forseti Cloud SQL disk above 80 percent",
    "Forseti Cloud SQL connections above 20",
    "Forseti daily LLM cost exceeds threshold",
    "Forseti analysis has unpriced model usage",
}
API_5XX_FILTER = (
    'resource.type="cloud_run_revision" '
    'AND resource.labels.service_name="forseti-api" '
    "AND httpRequest.status>=500"
)


def _project_arguments(env: Environment) -> list[str]:
    return ["--project", env.project]


def check_log_metrics(env: Environment) -> Outcome:
    metrics = env.gcloud(["logging", "metrics", "list", *_project_arguments(env)]) or []
    configured = {
        metric.get("name")
        for metric in metrics
        if not metric.get("disabled", False)
    }
    missing = sorted(EXPECTED_METRICS - configured)
    if missing:
        return Outcome.failed(f"missing or disabled log-based metrics: {', '.join(missing)}")
    return Outcome.passed("all GCP-6 log-based metrics exist and are enabled")


def check_dashboard(env: Environment) -> Outcome:
    dashboards = env.gcloud(["monitoring", "dashboards", "list", *_project_arguments(env)]) or []
    if any(dashboard.get("displayName") == "Forseti" for dashboard in dashboards):
        return Outcome.passed("Forseti monitoring dashboard exists")
    return Outcome.failed("Forseti monitoring dashboard is missing")


def _is_enabled(resource: dict[str, Any]) -> bool:
    enabled = resource.get("enabled", True)
    if isinstance(enabled, dict):
        return enabled.get("value", True) is True
    return enabled is True


def check_alert_policies(env: Environment) -> Outcome:
    policies = env.gcloud(["monitoring", "policies", "list", *_project_arguments(env)]) or []
    enabled_policies = {
        policy.get("displayName")
        for policy in policies
        if _is_enabled(policy)
    }
    missing = sorted(EXPECTED_ALERTS - enabled_policies)
    if missing:
        return Outcome.failed(f"missing or disabled alert policies: {', '.join(missing)}")
    return Outcome.passed("all GCP-6 alert policies exist and are enabled")


def check_email_channel(env: Environment) -> Outcome:
    channels = env.gcloud(["monitoring", "channels", "list", *_project_arguments(env)]) or []
    has_enabled_email = any(
        channel.get("type") == "email"
        and channel.get("displayName") == "Forseti owner email"
        and _is_enabled(channel)
        and channel.get("labels", {}).get("email_address")
        for channel in channels
    )
    if not has_enabled_email:
        return Outcome.failed("enabled owner email notification channel is missing")
    return Outcome.passed("an enabled email notification channel is configured")


def check_recent_analysis_traces(env: Environment) -> Outcome:
    filter_text = 'jsonPayload.event="analysis_completed" AND trace:*'
    entries = env.gcloud(
        ["logging", "read", filter_text, "--freshness=7d", "--limit=1", *_project_arguments(env)]
    ) or []
    if entries:
        return Outcome.passed("recent analysis-completed logs include a Cloud Trace identifier")
    return Outcome.failed("no recent /analyze completion log has a trace identifier")


def check_no_recent_api_5xx(env: Environment) -> Outcome:
    entries = env.gcloud(
        ["logging", "read", API_5XX_FILTER, "--freshness=7d", "--limit=1", *_project_arguments(env)]
    ) or []
    if entries:
        return Outcome.failed("Cloud Run API has a 5xx log entry within the last 7 days")
    return Outcome.passed("no Cloud Run API 5xx log entries in the last 7 days")


CHECKS = (
    Check("gcp6.log-metrics", GROUP, "Log-based metrics exist and are enabled", Cost.FREE, check_log_metrics),
    Check("gcp6.dashboard", GROUP, "Observability dashboard exists", Cost.FREE, check_dashboard),
    Check("gcp6.alert-policies", GROUP, "Alert policies exist and are enabled", Cost.FREE, check_alert_policies),
    Check("gcp6.email-channel", GROUP, "Owner email notification channel exists", Cost.FREE, check_email_channel),
    Check("gcp6.recent-traces", GROUP, "Recent analysis requests have traces", Cost.FREE, check_recent_analysis_traces),
    Check("gcp6.no-5xx", GROUP, "No API 5xx responses in the last 7 days", Cost.FREE, check_no_recent_api_5xx),
)
