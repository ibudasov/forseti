"""GCP-4 checks: private API access, runtime configuration and traffic."""
from __future__ import annotations

import json
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.cloud_scorecard.environment import CloudQueryError, Environment
from scripts.cloud_scorecard.model import Check, Cost, Outcome

GROUP = "GCP-4"
SERVICE_NAME = "forseti-api"
EXPECTED_ENVIRONMENT = {
    "APP_ENV": "cloud",
    "PIPELINE_MODE": "agentic",
    "GOOGLE_GENAI_USE_VERTEXAI": "TRUE",
    "FUNDAMENTAL_AGENT_MODE": "enforced",
    "TECHNICAL_AGENT_MODE": "enforced",
    "CRITIC_MODE": "enforced",
    "SYNTHESIZER_CONTRACT": "typed",
    "ALLOW_PIPELINE_OVERRIDE": "false",
    "DEBUG_LLM_IO": "false",
    "RAG_FAIL_LOUD": "true",
    "DB_POOL_SIZE": "3",
    "DB_MAX_OVERFLOW": "2",
}
REQUIRED_ENVIRONMENT_VARIABLES = ("GEMINI_MODEL", "EMBEDDING_MODEL", "EDGAR_USER_AGENT")


def _service(env: Environment) -> dict[str, Any] | None:
    return env.gcloud([
        "run", "services", "describe", SERVICE_NAME,
        "--region", env.region, "--project", env.project,
    ])


def _service_url(service: dict[str, Any]) -> str:
    return (
        service.get("status", {}).get("url")
        or service.get("uri")
        or service.get("status", {}).get("uri")
        or ""
    )


def _template(service: dict[str, Any]) -> dict[str, Any]:
    template = service.get("spec", {}).get("template") or service.get("template") or {}
    return template.get("spec") or template


def _runtime_environment(service: dict[str, Any]) -> dict[str, str]:
    containers = _template(service).get("containers", [])
    if not containers:
        return {}
    return {
        variable["name"]: str(variable.get("value", ""))
        for variable in containers[0].get("env", [])
    }


def _service_account(service: dict[str, Any]) -> str:
    return (
        _template(service).get("serviceAccount")
        or _template(service).get("serviceAccountName")
        or ""
    )


def _traffic(service: dict[str, Any]) -> list[dict[str, Any]]:
    status = service.get("status", {})
    return status.get("traffic") or service.get("traffic") or status.get("trafficStatuses") or []


def _latest_ready_revision(service: dict[str, Any]) -> str:
    status = service.get("status", {})
    return status.get("latestReadyRevisionName") or status.get("latestReadyRevision") or ""


def check_runtime_configuration(env: Environment) -> Outcome:
    service = _service(env)
    if service is None:
        return Outcome.pending(f"Cloud Run service {SERVICE_NAME} does not exist yet")

    actual_environment = _runtime_environment(service)
    expected_environment = {
        **EXPECTED_ENVIRONMENT,
        "GOOGLE_CLOUD_PROJECT": env.project,
        "VERTEX_AI_PROJECT": env.project,
        "GOOGLE_CLOUD_LOCATION": env.region,
        "VERTEX_AI_LOCATION": env.region,
    }
    mismatched_environment = sorted(
        name for name, value in expected_environment.items()
        if actual_environment.get(name) != value
    )
    mismatched_environment.extend(
        name for name in REQUIRED_ENVIRONMENT_VARIABLES if not actual_environment.get(name)
    )
    expected_account = env.service_account_email("forseti-api")
    if _service_account(service) != expected_account:
        mismatched_environment.append("service account")

    ready_revision = _latest_ready_revision(service)
    serves_latest = any(
        (traffic.get("revisionName") or traffic.get("revision")) == ready_revision
        and int(traffic.get("percent") or 0) == 100
        for traffic in _traffic(service)
    )
    if not ready_revision or not serves_latest:
        mismatched_environment.append("100% latest-revision traffic")

    if mismatched_environment:
        return Outcome.failed(f"API runtime differs from policy: {', '.join(mismatched_environment)}")
    return Outcome.passed("API runtime settings, identity and latest-revision traffic match policy")


def _identity_token(audience: str) -> str:
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CloudQueryError("gcloud could not create a service identity token") from error
    token = result.stdout.strip()
    if not token:
        raise CloudQueryError("gcloud returned an empty service identity token")
    return token


def _http_get(url: str, identity_token: str | None = None) -> tuple[int, str]:
    headers = {}
    if identity_token:
        headers["Authorization"] = f"Bearer {identity_token}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8")
    except (OSError, URLError, TimeoutError) as error:
        raise CloudQueryError("Cloud Run API request failed") from error


def check_private_api_access(env: Environment) -> Outcome:
    service = _service(env)
    if service is None:
        return Outcome.pending(f"Cloud Run service {SERVICE_NAME} does not exist yet")
    service_url = _service_url(service).rstrip("/")
    if not service_url:
        return Outcome.pending(f"Cloud Run service {SERVICE_NAME} has no URL yet")

    anonymous_status, _ = _http_get(f"{service_url}/livez")
    if anonymous_status != 403:
        return Outcome.failed(f"anonymous /livez request returned HTTP {anonymous_status}, expected 403")

    token = _identity_token(service_url)
    live_status, _ = _http_get(f"{service_url}/livez", token)
    if live_status != 200:
        return Outcome.failed(f"authenticated /livez request returned HTTP {live_status}")

    health_status, health_body = _http_get(f"{service_url}/healthz", token)
    if health_status != 200:
        return Outcome.failed(f"authenticated /healthz request returned HTTP {health_status}")
    try:
        health = json.loads(health_body)
    except json.JSONDecodeError:
        return Outcome.failed("authenticated /healthz response was not valid JSON")
    if health.get("app") != "ok" or health.get("db") != "ok":
        return Outcome.failed("authenticated /healthz did not report app and database healthy")
    return Outcome.passed("anonymous requests are denied; authenticated liveness and database checks pass")


CHECKS = (
    Check(
        "gcp4.runtime",
        GROUP,
        "API agents are enforced, typed synthesis is enabled and latest revision serves as forseti-api",
        Cost.FREE,
        check_runtime_configuration,
    ),
    Check(
        "gcp4.private-access",
        GROUP,
        "API denies anonymous access and authenticated health checks pass",
        Cost.FREE,
        check_private_api_access,
    ),
)
