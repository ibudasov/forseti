"""Offline tests for the live cloud scorecard, driven by recorded gcloud JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Sequence, Tuple

import pytest

from scripts.cloud_scorecard import gcp2, gcp3b, gcp4
from scripts.cloud_scorecard.environment import CloudQueryError, Environment
from scripts.cloud_scorecard.model import Check, Cost, Outcome, Status
from scripts.cloud_scorecard.redaction import redact
from scripts.cloud_scorecard.registry import ALL_CHECKS, GROUPS, run_check, run_checks
from scripts.cloud_scorecard.render import render_markdown
from scripts.cloud_scorecard.__main__ import current_revision

FIXTURES = Path(__file__).parent / "fixtures" / "cloud_scorecard"
PROJECT = "forseti-test"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class RecordedQueries:
    """Answers queries by argument prefix; an unrecorded query fails the test."""

    def __init__(self, routes: Dict[Tuple[str, ...], Any]) -> None:
        self.routes = routes

    def __call__(self, arguments: Sequence[str]) -> Any:
        for prefix, response in self.routes.items():
            if tuple(arguments[: len(prefix)]) == prefix:
                return response
        raise AssertionError(f"unrecorded query: {list(arguments)}")


def environment(gcloud: Dict[Tuple[str, ...], Any], github: Dict[Tuple[str, ...], Any] | None = None) -> Environment:
    return Environment(
        project=PROJECT, region="europe-west4", repository="example/repo",
        repository_id="111", owner_id="222", manifest=fixture("manifest"),
        gcloud=RecordedQueries(gcloud), github=RecordedQueries(github or {}),
    )


SERVICE_ACCOUNTS = ("iam", "service-accounts", "list")
PROJECT_POLICY = ("projects", "get-iam-policy")
KEYS = ("iam", "service-accounts", "keys", "list")
PROVIDER = ("iam", "workload-identity-pools", "providers", "describe")
REPOSITORY = ("artifacts", "repositories", "describe")
SECRET_POLICY = ("secrets", "get-iam-policy")
JOBS = ("run", "jobs")
API_SERVICE = ("run", "services", "describe", "forseti-api")
DEPLOYER = "serviceAccount:forseti-deployer@forseti-test.iam.gserviceaccount.com"
HOUSEKEEPING = "serviceAccount:forseti-housekeeping@forseti-test.iam.gserviceaccount.com"
DRIFT = ("repos/example/repo/actions/workflows/infra.yml/runs",)


def drift_environment(name: str) -> Environment:
    path = "repos/example/repo/actions/workflows/infra.yml/runs?event=schedule&branch=main&status=completed&per_page=1"
    return environment({}, {(path,): fixture(name)})


class TestIdentities:
    def test_passes_when_roles_match_exactly(self):
        env = environment({SERVICE_ACCOUNTS: fixture("service_accounts"), PROJECT_POLICY: fixture("project_policy")})

        assert gcp2.check_identities(env).status is Status.PASS

    def test_fails_on_an_extra_role(self):
        env = environment(
            {SERVICE_ACCOUNTS: fixture("service_accounts"), PROJECT_POLICY: fixture("project_policy_excess_role")}
        )

        outcome = gcp2.check_identities(env)

        assert outcome.status is Status.FAIL
        assert "roles/editor" in outcome.detail

    def test_fails_when_only_some_identities_exist(self):
        env = environment({SERVICE_ACCOUNTS: fixture("service_accounts")[:1]})

        outcome = gcp2.check_identities(env)

        assert outcome.status is Status.FAIL
        assert "forseti-tf-apply" in outcome.detail

    def test_is_pending_before_bootstrap(self):
        env = environment({SERVICE_ACCOUNTS: []})

        assert gcp2.check_identities(env).status is Status.PENDING

    def test_conditional_role_without_condition_fails(self):
        policy = fixture("project_policy")
        for binding in policy["bindings"]:
            binding.pop("condition", None)
        env = environment({SERVICE_ACCOUNTS: fixture("service_accounts"), PROJECT_POLICY: policy})

        assert gcp2.check_identities(env).status is Status.FAIL


class TestResourceGrants:
    def test_passes_and_ignores_owner_members(self):
        env = environment({SECRET_POLICY: fixture("secret_policy")})

        assert gcp2.check_resource_grants(env).status is Status.PASS

    def test_fails_on_an_undeclared_identity_grant(self):
        env = environment({SECRET_POLICY: fixture("secret_policy_extra_reader")})

        assert gcp2.check_resource_grants(env).status is Status.FAIL

    def test_is_pending_when_the_resource_does_not_exist(self):
        env = environment({SECRET_POLICY: None})

        assert gcp2.check_resource_grants(env).status is Status.PENDING


class TestServiceAccountKeys:
    def test_passes_without_user_managed_keys(self):
        env = environment({SERVICE_ACCOUNTS: fixture("service_accounts"), KEYS: []})

        assert gcp2.check_no_service_account_keys(env).status is Status.PASS

    def test_fails_and_names_the_account_with_a_key(self):
        env = environment({SERVICE_ACCOUNTS: fixture("service_accounts"), KEYS: fixture("user_managed_keys")})

        outcome = gcp2.check_no_service_account_keys(env)

        assert outcome.status is Status.FAIL
        assert "forseti-api" in outcome.detail

    def test_is_pending_without_service_accounts(self):
        env = environment({SERVICE_ACCOUNTS: []})

        assert gcp2.check_no_service_account_keys(env).status is Status.PENDING


class TestFederationCondition:
    def test_passes_when_both_ids_are_pinned(self):
        env = environment({PROVIDER: fixture("provider")})

        assert gcp2.check_federation_condition(env).status is Status.PASS

    def test_fails_when_the_repository_is_not_pinned(self):
        env = environment({PROVIDER: fixture("provider_loose")})

        assert gcp2.check_federation_condition(env).status is Status.FAIL

    def test_is_pending_without_provider(self):
        env = environment({PROVIDER: None})

        assert gcp2.check_federation_condition(env).status is Status.PENDING


class TestArtifactRegistry:
    def test_passes_with_enforced_cleanup_policies(self):
        env = environment({REPOSITORY: fixture("artifact_repository")})

        assert gcp2.check_artifact_registry(env).status is Status.PASS

    def test_fails_in_dry_run_mode(self):
        env = environment({REPOSITORY: fixture("artifact_repository_dry_run")})

        assert gcp2.check_artifact_registry(env).status is Status.FAIL

    def test_fails_without_cleanup_policies(self):
        env = environment({REPOSITORY: {"format": "DOCKER"}})

        assert gcp2.check_artifact_registry(env).status is Status.FAIL

    def test_is_pending_without_repository(self):
        env = environment({REPOSITORY: None})

        assert gcp2.check_artifact_registry(env).status is Status.PENDING


class TestNightlyDrift:
    @pytest.mark.parametrize(
        ("recorded", "expected"),
        [("drift_success", Status.PASS), ("drift_failure", Status.FAIL), ("drift_none", Status.PENDING)],
    )
    def test_reflects_latest_nightly_run(self, recorded, expected):
        assert gcp2.check_nightly_drift(drift_environment(recorded)).status is expected


def job(service_account: str) -> dict:
    return {"spec": {"template": {"spec": {"template": {"spec": {"serviceAccountName": service_account}}}}}}


def policy(role: str, *members: str) -> dict:
    return {"bindings": [{"role": role, "members": list(members)}]}


class TestHousekeepingJob:
    def test_passes_when_the_job_runs_as_housekeeping(self):
        env = environment({JOBS: job("forseti-housekeeping@forseti-test.iam.gserviceaccount.com")})

        assert gcp3b.check_job_identity(env).status is Status.PASS

    def test_fails_when_the_job_runs_as_another_identity(self):
        env = environment({JOBS: job("forseti-api@forseti-test.iam.gserviceaccount.com")})

        assert gcp3b.check_job_identity(env).status is Status.FAIL

    def test_is_pending_without_the_job(self):
        assert gcp3b.check_job_identity(environment({JOBS: None})).status is Status.PENDING

    @pytest.mark.parametrize(
        ("executions", "expected"),
        [
            ([{"status": {"conditions": [{"type": "Completed", "status": "True"}]}}], Status.PASS),
            ([{"status": {"conditions": [{"type": "Completed", "status": "False"}]}}], Status.FAIL),
            ([{"status": {"conditions": [{"type": "Completed", "status": "Unknown"}]}}], Status.PENDING),
            ([{"status": {}}], Status.PENDING),
            ([], Status.PENDING),
        ],
    )
    def test_reflects_the_last_execution(self, executions, expected):
        env = environment({JOBS: executions})

        assert gcp3b.check_last_execution(env).status is expected


class TestOwnerSecretIsolation:
    def test_passes_when_only_housekeeping_reads_both_secrets(self):
        env = environment({SECRET_POLICY: policy("roles/secretmanager.secretAccessor", HOUSEKEEPING)})

        assert gcp3b.check_owner_secret_readers(env).status is Status.PASS

    def test_ignores_non_reader_roles(self):
        recorded = {"bindings": [
            {"role": "roles/secretmanager.secretAccessor", "members": [HOUSEKEEPING]},
            {"role": "roles/secretmanager.admin", "members": ["user:owner@example.com"]},
        ]}

        assert gcp3b.check_owner_secret_readers(environment({SECRET_POLICY: recorded})).status is Status.PASS

    def test_fails_when_another_identity_can_read(self):
        env = environment({SECRET_POLICY: policy("roles/secretmanager.secretAccessor", HOUSEKEEPING, DEPLOYER)})

        assert gcp3b.check_owner_secret_readers(env).status is Status.FAIL

    def test_is_pending_before_the_secrets_exist(self):
        assert gcp3b.check_owner_secret_readers(environment({SECRET_POLICY: None})).status is Status.PENDING


class TestJobRunners:
    def test_passes_for_the_deployer_and_the_owner(self):
        env = environment({JOBS: policy("roles/run.developer", DEPLOYER, "user:owner@example.com")})

        assert gcp3b.check_job_runners(env).status is Status.PASS

    def test_passes_without_job_level_grants(self):
        assert gcp3b.check_job_runners(environment({JOBS: {}})).status is Status.PASS

    @pytest.mark.parametrize(
        "member",
        ["allUsers", HOUSEKEEPING, "serviceAccount:forseti-verifier@forseti-test.iam.gserviceaccount.com"],
    )
    def test_fails_for_any_other_member(self, member):
        env = environment({JOBS: policy("roles/run.invoker", DEPLOYER, member)})

        assert gcp3b.check_job_runners(env).status is Status.FAIL

    def test_is_pending_without_the_job(self):
        assert gcp3b.check_job_runners(environment({JOBS: None})).status is Status.PENDING


def api_service() -> dict:
    environment = {
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
        "GOOGLE_CLOUD_PROJECT": PROJECT,
        "VERTEX_AI_PROJECT": PROJECT,
        "GOOGLE_CLOUD_LOCATION": "europe-west4",
        "VERTEX_AI_LOCATION": "europe-west4",
        "GEMINI_MODEL": "gemini-test",
        "EMBEDDING_MODEL": "embedding-test",
        "EDGAR_USER_AGENT": "Forseti/test",
    }
    return {
        "status": {
            "url": "https://forseti-api.example.invalid",
            "latestReadyRevisionName": "forseti-api-00002",
            "traffic": [{"revisionName": "forseti-api-00002", "percent": 100}],
        },
        "spec": {
            "template": {
                "spec": {
                    "serviceAccountName": "forseti-api@forseti-test.iam.gserviceaccount.com",
                    "containers": [{
                        "env": [{"name": name, "value": value} for name, value in environment.items()],
                    }],
                },
            },
        },
    }


class TestApiRuntime:
    def test_passes_with_enforced_agents_and_latest_revision_serving(self):
        assert gcp4.check_runtime_configuration(environment({API_SERVICE: api_service()})).status is Status.PASS

    def test_fails_when_runtime_settings_or_traffic_drift(self):
        service = api_service()
        variables = service["spec"]["template"]["spec"]["containers"][0]["env"]
        fundamental_mode = next(variable for variable in variables if variable["name"] == "FUNDAMENTAL_AGENT_MODE")
        fundamental_mode["value"] = "shadow"
        service["status"]["traffic"][0]["percent"] = 90

        outcome = gcp4.check_runtime_configuration(environment({API_SERVICE: service}))

        assert outcome.status is Status.FAIL
        assert "FUNDAMENTAL_AGENT_MODE" in outcome.detail
        assert "100% latest-revision traffic" in outcome.detail

    def test_is_pending_before_api_service_exists(self):
        assert gcp4.check_runtime_configuration(environment({API_SERVICE: None})).status is Status.PENDING

    def test_private_access_requires_authenticated_healthy_endpoints(self, monkeypatch):
        requests = []

        def http_get(url, identity_token=None):
            requests.append((url, identity_token))
            if identity_token is None:
                return 403, ""
            if url.endswith("/livez"):
                return 200, '{"app":"ok"}'
            return 200, '{"app":"ok","db":"ok"}'

        monkeypatch.setattr(gcp4, "_http_get", http_get)
        monkeypatch.setattr(gcp4, "_identity_token", lambda _audience: "test-token")

        outcome = gcp4.check_private_api_access(environment({API_SERVICE: api_service()}))

        assert outcome.status is Status.PASS
        assert requests == [
            ("https://forseti-api.example.invalid/livez", None),
            ("https://forseti-api.example.invalid/livez", "test-token"),
            ("https://forseti-api.example.invalid/healthz", "test-token"),
        ]

    def test_private_access_fails_if_anonymous_request_is_allowed(self, monkeypatch):
        monkeypatch.setattr(gcp4, "_http_get", lambda _url, _token=None: (200, '{"app":"ok"}'))

        outcome = gcp4.check_private_api_access(environment({API_SERVICE: api_service()}))

        assert outcome.status is Status.FAIL
        assert "expected 403" in outcome.detail


class TestRunner:
    def test_paid_checks_are_skipped_unless_requested(self):
        check = Check("paid.example", "Exit criteria", "Paid", Cost.PAID, lambda _: Outcome.passed("ran"))

        assert run_check(check, environment({}), include_paid=False).outcome.status is Status.SKIP
        assert run_check(check, environment({}), include_paid=True).outcome.status is Status.PASS

    def test_query_errors_become_failures_without_raw_output(self):
        def denied(_):
            raise CloudQueryError("gcloud access denied")

        check = Check("free.example", "GCP-2", "Denied", Cost.FREE, denied)

        outcome = run_check(check, environment({}), include_paid=False).outcome

        assert (outcome.status, outcome.detail) == (Status.FAIL, "gcloud access denied")

    def test_a_crashing_check_reports_only_its_type(self):
        def crash(_):
            raise KeyError("secret-looking-value")

        check = Check("free.crash", "GCP-2", "Crash", Cost.FREE, crash)

        outcome = run_check(check, environment({}), include_paid=False).outcome

        assert outcome.status is Status.FAIL
        assert "secret-looking-value" not in outcome.detail

    def test_every_check_belongs_to_a_known_group(self):
        assert {check.group for check in ALL_CHECKS} <= set(GROUPS)
        assert set(GROUPS) == {check.group for check in ALL_CHECKS}

    def test_current_revision_reads_latest_ready_revision(self):
        env = environment({("run", "services", "describe"): fixture("cloud_run_service")})

        assert current_revision(env) == "forseti-api-00012-abc"

    def test_current_revision_before_deployment(self):
        assert current_revision(environment({("run", "services", "describe"): None})) == "none yet"


class TestMarkdown:
    def render(self, include_paid: bool = False) -> str:
        env = environment({
            SERVICE_ACCOUNTS: fixture("service_accounts"), PROJECT_POLICY: fixture("project_policy"),
            KEYS: [], PROVIDER: fixture("provider_loose"), REPOSITORY: None, SECRET_POLICY: fixture("secret_policy"),
            JOBS: None, API_SERVICE: None,
        }, {("repos/example/repo/actions/workflows/infra.yml/runs?event=schedule&branch=main&status=completed&per_page=1",):
            fixture("drift_success")})
        reports = run_checks(ALL_CHECKS, env, include_paid)
        return render_markdown(reports, GROUPS, "abc1234", "forseti-api-00012-abc")

    def test_shows_one_table_per_group_with_all_four_statuses(self):
        markdown = self.render()

        for group in GROUPS:
            assert f"### {group}" in markdown
        for status in Status:
            assert status.value in markdown

    def test_reports_commit_revision_and_totals(self):
        markdown = self.render()

        assert "Commit `abc1234` · Cloud Run revision `forseti-api-00012-abc`" in markdown
        assert "✅ 4 · ❌ 2 · ⏳ 9 · ➖ 1" in markdown

    def test_full_run_includes_paid_checks(self):
        assert "➖ 0" in self.render(include_paid=True)

    def test_escapes_table_separators_in_details(self):
        check = Check("free.pipe", "GCP-2", "Pipes", Cost.FREE, lambda _: Outcome.failed("a | b\nc"))
        reports = run_checks([check], environment({}), include_paid=False)

        assert "a \\| b c" in render_markdown(reports, ["GCP-2"], "abc1234", "none yet")


class TestRedaction:
    @pytest.mark.parametrize(
        ("sensitive", "leaked"),
        [
            ("see https://forseti-api-abc123-ew.a.run.app/docs now", "run.app"),
            ("owner someone@example.com failed", "someone@example.com"),
            ("forseti-api@forseti-test.iam.gserviceaccount.com", "iam.gserviceaccount.com"),
            ("projects/123456789012/locations/global", "123456789012"),
            ("unique id 104857612345678901234", "104857612345678901234"),
            ("key 0123456789abcdef0123456789abcdef01234567", "0123456789abcdef0123456789abcdef01234567"),
            ("gs://bucket-name/object", "gs://"),
        ],
    )
    def test_removes_sensitive_values(self, sensitive, leaked):
        assert leaked not in redact(sensitive)

    def test_keeps_ordinary_scorecard_text(self):
        text = "Commit `abc1234` · `gcp2.identities` missing ['roles/editor']"

        assert redact(text) == text

    def test_rendered_scorecard_from_sensitive_fixtures_is_clean(self):
        check = Check(
            "free.leak", "GCP-2", "Leaky", Cost.FREE,
            lambda _: Outcome.failed("https://example.com a@b.co projects/123456789012 104857612345678901234"),
        )
        reports = run_checks([check], environment({}), include_paid=False)

        scrubbed = redact(render_markdown(reports, ["GCP-2"], "abc1234", "none yet"))

        for leaked in ("https://", "a@b.co", "123456789012", "104857612345678901234"):
            assert leaked not in scrubbed
