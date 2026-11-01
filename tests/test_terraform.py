"""Cloud-free policy, owner-authentication and state-migration regression tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import check_terraform_policy as policy
from scripts import iam_doc, terraform_cli

ROOT = Path(__file__).resolve().parents[1]

STUB = r'''
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
arguments = sys.argv[1:]
with Path(os.environ["STUB_LOG"]).open("a") as log:
    log.write(json.dumps({
        "name": name, "args": arguments,
        "github_token": os.environ.get("GITHUB_TOKEN"),
        "gh_token": os.environ.get("GH_TOKEN"),
    }) + "\n")
if name == "gh":
    if arguments == ["auth", "token"]:
        print("owner-test-token")
        raise SystemExit(int(os.environ.get("STUB_TOKEN_FAILURE", "0")))
    if os.environ.get("STUB_PERMISSION_FAILURE"):
        raise SystemExit(1)
    if arguments == ["api", "user"]:
        print(json.dumps({"login": os.environ.get("STUB_LOGIN", "ibudasov")}))
    elif arguments == ["api", "repos/ibudasov/forseti"]:
        print('{"permissions": {"admin": true}}')
    else:
        print("{}")
elif name == "gcloud":
    if arguments[:3] == ["auth", "application-default", "print-access-token"]:
        print("dummy-adc-token")
        raise SystemExit(int(os.environ.get("STUB_ADC_FAILURE", "0")))
    if os.environ.get("STUB_BUCKET_DENIED"):
        print("HTTPError 403: permission denied", file=sys.stderr)
        raise SystemExit(1)
    if os.environ.get("STUB_BUCKET_EXISTS"):
        print("{}")
    else:
        print("HTTPError 404: bucket not found", file=sys.stderr)
        raise SystemExit(1)
elif name == "docker":
    directory = Path(os.environ["STUB_ROOT"]) / "infra/terraform/bootstrap"
    if arguments[-1] == "apply" and not (directory / "backend.generated.tf").exists():
        (directory / "terraform.tfstate").write_text('{"serial": 1}')
        (directory / "terraform.tfstate.backup").write_text('{"serial": 0}')
        if os.environ.get("STUB_APPLY_FAILURE"):
            raise SystemExit(1)
    if "-migrate-state" in arguments and os.environ.get("STUB_MIGRATION_FAILURE"):
        raise SystemExit(1)
'''


@pytest.fixture
def owner_tools(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    directory = root / "infra/terraform/bootstrap"
    directory.mkdir(parents=True)
    home = tmp_path / "home"
    credentials = home / ".config/gcloud/application_default_credentials.json"
    credentials.parent.mkdir(parents=True)
    credentials.write_text('{"type":"authorized_user"}')
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("gh", "gcloud", "docker"):
        executable = binaries / name
        executable.write_text(f"#!{sys.executable}\n{STUB}")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binaries}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "commands.jsonl"))
    monkeypatch.setenv("STUB_ROOT", str(root))
    monkeypatch.setenv("GITHUB_TOKEN", "codespace-test-token")
    monkeypatch.setenv("GH_TOKEN", "ambient-test-token")
    monkeypatch.delenv("TF_GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(terraform_cli, "ROOT", root)
    return directory, credentials, tmp_path / "commands.jsonl"


def commands(log):
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_bootstrap_ignores_builtin_tokens_and_migrates_state(owner_tools):
    directory, _, log = owner_tools
    terraform_cli.bootstrap()
    recorded = commands(log)
    github = [command for command in recorded if command["name"] == "gh"]
    assert github[0]["args"] == ["auth", "token"]
    assert github[0]["github_token"] is None
    assert github[0]["gh_token"] is None
    assert all(command["gh_token"] == "owner-test-token" for command in github[1:])
    docker = [command for command in recorded if command["name"] == "docker"]
    assert all(command["github_token"] == "owner-test-token" for command in docker)
    assert all(command["gh_token"] is None for command in docker)
    assert any("-migrate-state" in command["args"] for command in docker)
    assert 'prefix = "bootstrap/"' in (directory / "backend.generated.tf").read_text()
    assert not (directory / "terraform.tfstate").exists()
    assert not (directory / "terraform.tfstate.backup").exists()
    assert not any("owner-test-token" in argument for command in docker for argument in command["args"])
    workloads = [
        command["args"] for command in docker
        if "/workspace/infra/terraform/main" in command["args"]
    ]
    assert workloads[0][-2:] == ["init", "-backend-config=bucket=forseti-505116-tfstate"]
    assert workloads[1][-2:] == ["state", "list"]
    assert not any("apply" in command for command in workloads)


def test_explicit_owner_token_does_not_read_ambient_login(owner_tools, monkeypatch):
    _, _, log = owner_tools
    monkeypatch.setenv("TF_GITHUB_TOKEN", "explicit-test-token")
    environment = terraform_cli.github_preflight(terraform_cli.github_environment())
    assert environment["GITHUB_TOKEN"] == "explicit-test-token"
    assert all(command["args"] != ["auth", "token"] for command in commands(log))


@pytest.mark.parametrize("kind", ["missing", "directory", "expired"])
def test_adc_failure_stops_before_terraform(owner_tools, monkeypatch, kind):
    _, credentials, log = owner_tools
    if kind != "expired":
        credentials.unlink()
    if kind == "directory":
        credentials.mkdir()
    if kind == "expired":
        monkeypatch.setenv("STUB_ADC_FAILURE", "1")
    with pytest.raises(RuntimeError, match="make gcloud-login"):
        terraform_cli.bootstrap()
    assert all(command["name"] != "docker" for command in commands(log))


@pytest.mark.parametrize("setting,value", [
    ("STUB_TOKEN_FAILURE", "1"),
    ("STUB_PERMISSION_FAILURE", "1"),
    ("STUB_LOGIN", "someone-else"),
])
def test_github_failure_stops_before_terraform(owner_tools, monkeypatch, setting, value):
    _, _, log = owner_tools
    monkeypatch.setenv(setting, value)
    with pytest.raises(RuntimeError, match="make gh-login"):
        terraform_cli.bootstrap()
    assert all(command["name"] != "docker" for command in commands(log))


def test_failed_migration_preserves_recovery_state_and_retry_cleans_up(owner_tools, monkeypatch):
    directory, _, log = owner_tools
    monkeypatch.setenv("STUB_MIGRATION_FAILURE", "1")
    with pytest.raises(subprocess.CalledProcessError):
        terraform_cli.bootstrap()
    assert (directory / "terraform.tfstate").exists()
    assert (directory / "terraform.tfstate.backup").exists()
    monkeypatch.delenv("STUB_MIGRATION_FAILURE")
    terraform_cli.bootstrap()
    assert not (directory / "terraform.tfstate").exists()
    assert sum("-migrate-state" in command["args"] for command in commands(log)) == 2


def test_fresh_checkout_reconnects_to_remote_state(owner_tools, monkeypatch):
    directory, _, log = owner_tools
    monkeypatch.setenv("STUB_BUCKET_EXISTS", "1")
    terraform_cli.bootstrap()
    assert (directory / "backend.generated.tf").exists()
    assert not any("-migrate-state" in command["args"] for command in commands(log))
    assert not (directory / "terraform.tfstate").exists()


def test_bucket_access_failure_does_not_fall_back_to_local_state(owner_tools, monkeypatch):
    directory, _, log = owner_tools
    monkeypatch.setenv("STUB_BUCKET_DENIED", "1")
    with pytest.raises(RuntimeError, match="state bucket access"):
        terraform_cli.bootstrap()
    assert not (directory / "backend.generated.tf").exists()
    assert all(command["name"] != "docker" for command in commands(log))


def test_partial_apply_keeps_local_state_for_retry(owner_tools, monkeypatch):
    directory, _, _ = owner_tools
    monkeypatch.setenv("STUB_APPLY_FAILURE", "1")
    with pytest.raises(subprocess.CalledProcessError):
        terraform_cli.bootstrap()
    assert (directory / "terraform.tfstate").exists()
    assert not (directory / "backend.generated.tf").exists()
    monkeypatch.delenv("STUB_APPLY_FAILURE")
    terraform_cli.bootstrap()
    assert (directory / "backend.generated.tf").exists()
    assert not (directory / "terraform.tfstate").exists()


@pytest.mark.parametrize("resource", [
    'resource "google_service_account_key" "key" {}',
    'resource google_service_account_key key {}',
    'resource /* comment */ "google_service_account_key" /* comment */ "key" {}',
    r'resource "google_service_account_\u006bey" "key" {}',
    'resource\n"google_project_iam_member"\n"grant" {}',
    'resource "google_service_account_iam_member" "grant" {}',
    'resource "google_storage_bucket_iam_policy" "grant" {}',
])
def test_workload_policy_rejects_identity_changes(tmp_path, resource):
    directory = tmp_path / "main"
    directory.mkdir()
    (directory / "unsafe.tf").write_text(resource)
    assert policy.check_layer(directory)


def test_policy_accepts_resource_members_and_rejects_public_access(tmp_path):
    directory = tmp_path / "main"
    directory.mkdir()
    resource = directory / "access.tf"
    resource.write_text('resource "google_storage_bucket_iam_member" "grant" { member = "owner" }')
    assert policy.check_layer(directory) == []
    resource.write_text('resource "google_storage_bucket_iam_member" "grant" { member = "allUsers" }')
    assert "public IAM principals" in policy.check_layer(directory)[0]


def test_bootstrap_policy_rejects_keys_but_allows_identity_creation(tmp_path):
    directory = tmp_path / "bootstrap"
    directory.mkdir()
    resource = directory / "identity.tf"
    resource.write_text('resource "google_service_account" "runtime" {}')
    assert policy.check_layer(directory) == []
    resource.write_text('resource "google_service_account_key" "runtime" {}')
    assert policy.check_layer(directory)


def test_policy_checks_json_resources_and_rejects_hidden_modules(tmp_path):
    directory = tmp_path / "main"
    directory.mkdir()
    resource = directory / "identity.tf.json"
    resource.write_text(json.dumps({"resource": {"google_service_account_key": {"key": {}}}}))
    assert policy.check_layer(directory)
    resource.write_text(json.dumps({"module": {"hidden": {"source": "./module"}}}))
    assert policy.check_layer(directory)
    resource.unlink()
    (directory / "module.tf").write_text('module /* first */ // second\n /* third */ "hidden" { source = "./module" }')
    assert policy.check_layer(directory)


def test_apply_workflow_applies_saved_plan_without_destroy_gate():
    workflow = (ROOT / ".github/workflows/infra.yml").read_text()

    assert "terraform -chdir=\"$directory\" apply -input=false apply.tfplan" in workflow
    assert "check_terraform_policy.py --plan" not in workflow
    assert "allow_destroy" not in workflow


def test_document_generation_preserves_prose_and_detects_manifest_changes():
    manifest = json.loads((ROOT / "infra/terraform/bootstrap/identities.json").read_text())
    document = (ROOT / "docs/gcp-access.md").read_text()
    assert iam_doc.update_document(document, manifest) == document
    manifest["service_accounts"]["forseti-api"]["project_roles"].append("new.role")
    updated = iam_doc.update_document(document, manifest)
    assert "new.role" in updated
    assert updated.split(iam_doc.START)[0] == document.split(iam_doc.START)[0]
    assert updated.split(iam_doc.END)[1] == document.split(iam_doc.END)[1]
