"""Owner-only Terraform entrypoint. Never echo credentials or saved plans."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_IMAGE = "hashicorp/terraform:1.17.1"


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, env=env, check=True)


def github_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("GITHUB_TOKEN", None)
    environment.pop("GH_TOKEN", None)
    return environment


def github_token(environment: dict[str, str]) -> str:
    token = environment.get("TF_GITHUB_TOKEN")
    if token:
        return token
    result = subprocess.run(
        ["gh", "auth", "token"], env=environment, capture_output=True, text=True,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Owner GitHub credentials missing. Run make gh-login.")
    return result.stdout.strip()


def github_preflight(environment: dict[str, str]) -> dict[str, str]:
    environment = dict(environment)
    environment["GH_TOKEN"] = github_token(environment)
    endpoints = [
        "user", "repos/ibudasov/forseti",
        "repos/ibudasov/forseti/environments",
        "repos/ibudasov/forseti/actions/secrets",
        "repos/ibudasov/forseti/actions/variables",
    ]
    responses = []
    for endpoint in endpoints:
        response = subprocess.run(
            ["gh", "api", endpoint], env=environment, capture_output=True, text=True,
        )
        if response.returncode:
            raise RuntimeError("GitHub permission check failed. Run make gh-login with repo scope.")
        responses.append(json.loads(response.stdout))
    owner = responses[0].get("login", "")
    if owner.lower() != "ibudasov" or not responses[1].get("permissions", {}).get("admin"):
        raise RuntimeError("Bootstrap requires the repository owner's login. Run make gh-login.")
    environment["GITHUB_TOKEN"] = environment.pop("GH_TOKEN")
    return environment


def credential_preflight() -> Path:
    directory = Path.home() / ".config" / "gcloud"
    credentials = directory / "application_default_credentials.json"
    if not credentials.is_file() or credentials.stat().st_size == 0:
        raise RuntimeError("Application-default credentials missing. Run make gcloud-login.")
    response = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True, text=True,
    )
    if response.returncode or not response.stdout.strip():
        raise RuntimeError("Application-default credentials expired. Run make gcloud-login.")
    return directory


def terraform(directory: str, arguments: list[str], environment: dict[str, str]) -> None:
    command = [
        "docker", "run", "--rm", "-i", "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "HOME=/tmp", "-e", "TF_IN_AUTOMATION=1",
        "-v", f"{ROOT}:/workspace", "-w", f"/workspace/infra/terraform/{directory}",
    ]
    credentials = Path.home() / ".config" / "gcloud"
    if credentials.is_dir():
        command.extend([
            "-v", f"{credentials}:/gcloud:ro",
            "-e", "GOOGLE_APPLICATION_CREDENTIALS=/gcloud/application_default_credentials.json",
        ])
    for name in sorted(environment):
        if name.startswith("TF_VAR_") or name == "GITHUB_TOKEN":
            command.extend(["-e", name])
    command.extend([TERRAFORM_IMAGE, *arguments])
    run(command, env=environment)


def state_bucket() -> str:
    variables = ROOT / "infra/terraform/bootstrap/terraform.tfvars"
    project = os.environ.get("TF_VAR_project_id", "forseti-505116")
    if variables.exists():
        match = re.search(r'(?m)^\s*project_id\s*=\s*"([a-z0-9-]+)"', variables.read_text())
        if match:
            project = match.group(1)
    return f"{project}-tfstate"


def write_backend(bucket: str) -> Path:
    backend = ROOT / "infra/terraform/bootstrap/backend.generated.tf"
    backend.write_text(
        'terraform {\n  backend "gcs" {\n'
        f'    bucket = "{bucket}"\n    prefix = "bootstrap/"\n  }}\n}}\n'
    )
    return backend


def remove_local_state() -> None:
    directory = ROOT / "infra/terraform/bootstrap"
    for name in ("terraform.tfstate", "terraform.tfstate.backup"):
        (directory / name).unlink(missing_ok=True)


def bootstrap() -> None:
    credential_preflight()
    environment = github_preflight(github_environment())
    directory = ROOT / "infra/terraform/bootstrap"
    bucket = state_bucket()
    backend = directory / "backend.generated.tf"
    local_state = directory / "terraform.tfstate"
    # A fresh checkout must reconnect to remote state, never create duplicate resources.
    if not backend.exists() and not local_state.exists():
        result = subprocess.run(
            ["gcloud", "storage", "buckets", "describe", f"gs://{bucket}", "--format=json"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            write_backend(bucket)
        elif "404" not in result.stderr and "not exist" not in result.stderr.lower():
            raise RuntimeError("Cannot check state bucket access. Run make gcloud-login and check project permissions.")
    if backend.exists() and local_state.exists():
        terraform("bootstrap", ["init", "-migrate-state", "-force-copy"], environment)
        remove_local_state()
    else:
        terraform("bootstrap", ["init"], environment)
    terraform("bootstrap", ["apply"], environment)
    if not backend.exists():
        write_backend(bucket)
        terraform("bootstrap", ["init", "-migrate-state", "-force-copy"], environment)
        remove_local_state()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fmt", "validate", "init", "plan", "bootstrap"])
    parser.add_argument("--dir", choices=["bootstrap", "main"], default="main")
    arguments = parser.parse_args()
    try:
        if arguments.command == "bootstrap":
            bootstrap()
            return 0
        environment = github_environment()
        if arguments.command == "fmt":
            terraform(arguments.dir, ["fmt", "-recursive", "/workspace/infra/terraform"], environment)
            return 0
        if arguments.command == "validate":
            terraform(arguments.dir, ["init", "-backend=false"], environment)
            terraform(arguments.dir, ["validate"], environment)
            return 0
        if arguments.dir == "bootstrap":
            credential_preflight()
            environment = github_preflight(environment)
        command = [arguments.command]
        if arguments.command == "init" and arguments.dir == "main":
            command.append(f"-backend-config=bucket={state_bucket()}")
        if arguments.command == "plan" and arguments.dir == "main":
            command.append("-lock=false")
        terraform(arguments.dir, command, environment)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Terraform command failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
