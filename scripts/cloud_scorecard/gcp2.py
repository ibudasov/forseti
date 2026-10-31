"""GCP-2 checks: identities, grants, keys, federation, Artifact Registry and drift."""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from scripts.cloud_scorecard.environment import Environment
from scripts.cloud_scorecard.model import Check, Cost, Outcome, worst_outcome

GROUP = "GCP-2"
ARTIFACT_REPOSITORY = "forseti"
EXPECTED_CLEANUP_POLICIES = ("delete-old-untagged", "keep-recent")
RESOURCE_POLICY_COMMANDS = {
    "secret": ("secrets", "get-iam-policy"),
    "service": ("run", "services", "get-iam-policy"),
    "job": ("run", "jobs", "get-iam-policy"),
    "bucket": ("storage", "buckets", "get-iam-policy"),
}

RoleBinding = Tuple[str, bool]


def _declared_accounts(env: Environment) -> Dict[str, Any]:
    return dict(env.manifest["service_accounts"])


def _existing_emails(env: Environment) -> Set[str]:
    accounts = env.gcloud(["iam", "service-accounts", "list", "--project", env.project]) or []
    return {account["email"] for account in accounts}


def _expected_roles(account: Dict[str, Any]) -> Set[RoleBinding]:
    unconditional = {(f"roles/{role}", False) for role in account["project_roles"]}
    conditional = {(f"roles/{grant['role']}", True) for grant in account.get("conditional_project_roles", [])}
    return unconditional | conditional


def _actual_roles(policy: Dict[str, Any], email: str) -> Set[RoleBinding]:
    member = f"serviceAccount:{email}"
    return {
        (binding["role"], "condition" in binding)
        for binding in policy.get("bindings", [])
        if member in binding.get("members", [])
    }


def _describe_role_difference(name: str, expected: Set[RoleBinding], actual: Set[RoleBinding]) -> str:
    missing = sorted(role for role, _ in expected - actual)
    unexpected = sorted(role for role, _ in actual - expected)
    return f"{name}: missing {missing or 'none'}, unexpected {unexpected or 'none'}"


def check_identities(env: Environment) -> Outcome:
    accounts = _declared_accounts(env)
    existing = _existing_emails(env)
    absent = sorted(name for name in accounts if env.service_account_email(name) not in existing)
    if len(absent) == len(accounts):
        return Outcome.pending("no declared identity exists yet; the owner has not run bootstrap")
    if absent:
        return Outcome.failed(f"missing identities: {', '.join(absent)}")
    policy = env.gcloud(["projects", "get-iam-policy", env.project]) or {}
    mismatches = [
        _describe_role_difference(name, _expected_roles(account), _actual_roles(policy, env.service_account_email(name)))
        for name, account in accounts.items()
        if _expected_roles(account) != _actual_roles(policy, env.service_account_email(name))
    ]
    if mismatches:
        return Outcome.failed("; ".join(mismatches))
    return Outcome.passed(f"{len(accounts)} identities with exactly the declared project roles")


def _grant_pairs(env: Environment, resource: str, kind: str) -> Set[Tuple[str, str]]:
    return {
        (env.service_account_email(grant["member"]), f"roles/{grant['role']}")
        for grant in env.manifest["resource_grants"]
        if grant["resource"] == resource and grant["kind"] == kind and grant["member"] != "owner"
    }


def _managed_pairs(env: Environment, policy: Dict[str, Any]) -> Set[Tuple[str, str]]:
    managed = {env.service_account_email(name) for name in _declared_accounts(env)}
    return {
        (member.removeprefix("serviceAccount:"), binding["role"])
        for binding in policy.get("bindings", [])
        for member in binding.get("members", [])
        if member.removeprefix("serviceAccount:") in managed
    }


def _policy_query(env: Environment, kind: str, resource: str) -> List[str]:
    target = f"gs://{resource}" if kind == "bucket" else resource
    scope: List[str] = [] if kind in ("secret", "bucket") else ["--region", env.region]
    return [*RESOURCE_POLICY_COMMANDS[kind], target, *scope, "--project", env.project]


def _check_resource_grant(env: Environment, kind: str, resource: str) -> Outcome:
    policy = env.gcloud(_policy_query(env, kind, resource))
    if policy is None:
        return Outcome.pending(f"{kind} {resource} does not exist yet")
    if _grant_pairs(env, resource, kind) != _managed_pairs(env, policy):
        return Outcome.failed(f"{kind} {resource} grants differ from identities.json")
    return Outcome.passed(f"{kind} {resource}")


def check_resource_grants(env: Environment) -> Outcome:
    resources = sorted({(grant["kind"], grant["resource"]) for grant in env.manifest["resource_grants"]})
    if not resources:
        return Outcome.pending("identities.json declares no resource grants yet")
    outcomes = [_check_resource_grant(env, kind, resource) for kind, resource in resources]
    return worst_outcome(outcomes, f"{len(resources)} resources with exactly the declared grants")


def _user_managed_keys(env: Environment, email: str) -> List[Any]:
    command = ["iam", "service-accounts", "keys", "list", "--iam-account", email, "--managed-by=user"]
    return env.gcloud(command) or []


def check_no_service_account_keys(env: Environment) -> Outcome:
    emails = sorted(_existing_emails(env))
    if not emails:
        return Outcome.pending("no service accounts exist yet")
    holders = [email.split("@")[0] for email in emails if _user_managed_keys(env, email)]
    if holders:
        return Outcome.failed(f"user-managed keys on: {', '.join(holders)}")
    return Outcome.passed(f"no user-managed keys on {len(emails)} service accounts")


def expected_provider_condition(env: Environment) -> str:
    return f"assertion.repository_id == '{env.repository_id}' && assertion.repository_owner_id == '{env.owner_id}'"


def check_federation_condition(env: Environment) -> Outcome:
    query = [
        "iam", "workload-identity-pools", "providers", "describe", "github",
        "--workload-identity-pool", "github", "--location", "global", "--project", env.project,
    ]
    provider = env.gcloud(query)
    if provider is None:
        return Outcome.pending("the GitHub identity provider does not exist yet")
    if provider.get("attributeCondition") != expected_provider_condition(env):
        return Outcome.failed("provider condition does not pin exactly the repository and owner IDs")
    return Outcome.passed("provider condition pins the repository and owner IDs")


def check_artifact_registry(env: Environment) -> Outcome:
    query = [
        "artifacts", "repositories", "describe", ARTIFACT_REPOSITORY,
        "--location", env.region, "--project", env.project,
    ]
    repository = env.gcloud(query)
    if repository is None:
        return Outcome.pending(f"Artifact Registry repository {ARTIFACT_REPOSITORY} does not exist yet")
    policies = repository.get("cleanupPolicies") or {}
    missing = [name for name in EXPECTED_CLEANUP_POLICIES if name not in policies]
    if missing:
        return Outcome.failed(f"missing cleanup policies: {', '.join(missing)}")
    if repository.get("cleanupPolicyDryRun"):
        return Outcome.failed("cleanup policies are in dry-run mode")
    return Outcome.passed("repository exists with its cleanup policies enforced")


def check_nightly_drift(env: Environment) -> Outcome:
    path = f"repos/{env.repository}/actions/workflows/infra.yml/runs?event=schedule&branch=main&status=completed&per_page=1"
    runs = (env.github([path]) or {}).get("workflow_runs", [])
    if not runs:
        return Outcome.pending("no nightly infrastructure run has completed yet")
    conclusion = runs[0]["conclusion"]
    if conclusion != "success":
        return Outcome.failed(f"latest nightly drift check concluded {conclusion}")
    return Outcome.passed("latest nightly drift check passed")


CHECKS = (
    Check("gcp2.identities", GROUP, "Every identity exists with exactly its project roles", Cost.FREE, check_identities),
    Check("gcp2.resource-grants", GROUP, "Resource grants match identities.json", Cost.FREE, check_resource_grants),
    Check("gcp2.no-keys", GROUP, "No user-managed service-account keys", Cost.FREE, check_no_service_account_keys),
    Check("gcp2.federation", GROUP, "Federation provider pins repository and owner IDs", Cost.FREE,
          check_federation_condition),
    Check("gcp2.artifact-registry", GROUP, "Artifact Registry repo and cleanup policy", Cost.FREE,
          check_artifact_registry),
    Check("gcp2.drift", GROUP, "Latest nightly drift check passed", Cost.FREE, check_nightly_drift),
)
