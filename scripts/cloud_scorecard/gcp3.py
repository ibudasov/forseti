"""GCP-3 checks: Cloud SQL configuration, database users and secret versions."""
from __future__ import annotations

from typing import Any, Dict, List

from scripts.cloud_scorecard.environment import Environment
from scripts.cloud_scorecard.model import Check, Cost, Outcome

GROUP = "GCP-3"
INSTANCE_NAME = "forseti-pg"
DATABASE_NAME = "forseti"
EXPECTED_REGION = "europe-west4"
EXPECTED_DATABASE_USERS = {"forseti_owner"}
SECRETS = (
    "forseti-database-url",
    "forseti-owner-database-url",
    "forseti-app-db-password",
    "forseti-alpha-vantage-api-key",
)


def _instance(env: Environment) -> Dict[str, Any] | None:
    return env.gcloud([
        "sql", "instances", "describe", INSTANCE_NAME, "--project", env.project,
    ])


def check_instance(env: Environment) -> Outcome:
    instance = _instance(env)
    if instance is None:
        return Outcome.pending("Cloud SQL instance is not available yet")

    settings = instance.get("settings") or {}
    mismatches = [
        instance.get("state") != "RUNNABLE",
        instance.get("region") != EXPECTED_REGION,
        instance.get("databaseVersion") != "POSTGRES_15",
        settings.get("tier") != "db-f1-micro",
        settings.get("availabilityType") != "ZONAL",
        settings.get("edition") != "ENTERPRISE",
    ]
    if any(mismatches):
        return Outcome.failed(f"{sum(mismatches)} instance settings differ from policy")
    return Outcome.passed("1 instance matches policy")


def check_network(env: Environment) -> Outcome:
    instance = _instance(env)
    if instance is None:
        return Outcome.pending("Cloud SQL instance is not available yet")

    ip_configuration = (instance.get("settings") or {}).get("ipConfiguration") or {}
    mismatches = [
        bool(ip_configuration.get("authorizedNetworks")),
        ip_configuration.get("sslMode") != "ENCRYPTED_ONLY",
    ]
    if any(mismatches):
        return Outcome.failed(f"{sum(mismatches)} network settings differ from policy")
    return Outcome.passed("2 network settings match policy")


def check_protection_backups(env: Environment) -> Outcome:
    instance = _instance(env)
    if instance is None:
        return Outcome.pending("Cloud SQL instance is not available yet")

    settings = instance.get("settings") or {}
    backups = settings.get("backupConfiguration") or {}
    retention = backups.get("backupRetentionSettings") or {}
    mismatches = [
        not settings.get("deletionProtectionEnabled", False),
        not backups.get("enabled", False),
        backups.get("startTime") != "02:00",
        retention.get("retainedBackups") != 7,
        backups.get("pointInTimeRecoveryEnabled", False),
    ]
    if any(mismatches):
        return Outcome.failed(f"{sum(mismatches)} protection or backup settings differ from policy")
    return Outcome.passed("5 protection and backup settings match policy")


def check_database_users(env: Environment) -> Outcome:
    databases = env.gcloud([
        "sql", "databases", "list", "--instance", INSTANCE_NAME, "--project", env.project,
    ])
    users = env.gcloud([
        "sql", "users", "list", "--instance", INSTANCE_NAME, "--project", env.project,
    ])
    if databases is None or users is None:
        return Outcome.pending("Cloud SQL database and users are not available yet")

    database_exists = any(database.get("name") == DATABASE_NAME for database in databases)
    user_names = {user.get("name") for user in users}
    users_match = user_names == EXPECTED_DATABASE_USERS
    if not database_exists or not users_match:
        mismatch_count = int(not database_exists) + int(not users_match)
        return Outcome.failed(
            f"{mismatch_count} database or user settings differ; "
            f"database count={len(databases)}, API-created user count={len(users)}"
        )
    return Outcome.passed(f"1 database and {len(users)} API-created user match policy")


def check_secrets(env: Environment) -> Outcome:
    missing_secrets = 0
    secrets_without_enabled_versions = 0
    for secret in SECRETS:
        description = env.gcloud(["secrets", "describe", secret, "--project", env.project])
        if description is None:
            missing_secrets += 1
            continue
        versions: List[Dict[str, Any]] = env.gcloud([
            "secrets", "versions", "list", secret, "--filter=state=ENABLED", "--limit=1",
            "--project", env.project,
        ]) or []
        if not versions:
            secrets_without_enabled_versions += 1

    if secrets_without_enabled_versions:
        return Outcome.failed(
            f"{secrets_without_enabled_versions} of {len(SECRETS)} secrets have no enabled version"
        )
    if missing_secrets:
        return Outcome.pending(f"{len(SECRETS) - missing_secrets} of {len(SECRETS)} secrets are available")
    return Outcome.passed(f"{len(SECRETS)} secrets have enabled versions")


CHECKS = (
    Check("gcp3.instance", GROUP, "Cloud SQL instance configuration", Cost.FREE, check_instance),
    Check("gcp3.network", GROUP, "Cloud SQL network configuration", Cost.FREE, check_network),
    Check("gcp3.protection-backups", GROUP, "Cloud SQL protection and backups", Cost.FREE,
          check_protection_backups),
    Check("gcp3.database-users", GROUP, "Application database and API-created users", Cost.FREE,
          check_database_users),
    Check("gcp3.secrets", GROUP, "Required database and API secrets have enabled versions", Cost.FREE,
          check_secrets),
)
