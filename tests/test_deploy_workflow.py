"""Execute deployment shell steps offline against a deterministic gcloud stub."""
import os
from pathlib import Path
import subprocess

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/deploy.yml"
ROOT = WORKFLOW.parents[2]


@pytest.fixture
def steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["deploy"]["steps"]


@pytest.fixture
def cloud_environment(tmp_path):
    executable = tmp_path / "gcloud"
    executable.write_text(
        '#!/bin/bash\n'
        'printf "%s\\n" "$*" >> "$COMMAND_LOG"\n'
        'if [ "$2" = deploy ]; then printf "%s\\n" "$DEPLOYED_REVISION"; fi\n'
        'if [ "$3" = describe ]; then printf "%s\\n" "$TRAFFIC"; fi\n'
    )
    executable.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "COMMAND_LOG": str(tmp_path / "commands"),
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
        "IMAGE": "offline-image",
        "GCP_PROJECT_ID": "offline-project",
        "GCP_REGION": "offline-region",
        "DEPLOYED_REVISION": "api-new",
        "REVISION": "api-new",
        "PREVIOUS_REVISION": "api-old",
        "TRAFFIC": "api-new\t100",
    }


def run_step(steps, name, environment):
    step = next(step for step in steps if step.get("name") == name)
    return subprocess.run(["bash", "-c", step["run"]], env=environment, capture_output=True, text=True)


def test_deploy_housekeeping_revision_remains_available_to_alembic():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))

    revision = ScriptDirectory.from_config(config).get_revision("0015_agent_run_trace_id")

    assert revision is not None
    assert revision.down_revision == "0014_agent_run_cost"


def test_deploy_routes_to_captured_revision_before_smoke(steps, cloud_environment):
    environment = cloud_environment
    assert run_step(steps, "Deploy API image", environment).returncode == 0
    assert Path(environment["GITHUB_OUTPUT"]).read_text() == "revision=api-new\n"
    assert run_step(steps, "Route traffic to deployed revision", environment).returncode == 0
    commands = Path(environment["COMMAND_LOG"]).read_text()
    assert "--no-traffic" in commands
    assert "--to-revisions api-new=100" in commands
    names = [step.get("name") for step in steps]
    assert names.index("Route traffic to deployed revision") < names.index("Smoke test private service")


@pytest.mark.parametrize("traffic", ["api-old\t100", "api-new\t50", "api-new;api-old\t50;50", ""])
def test_wrong_or_split_traffic_fails_validation(steps, cloud_environment, traffic):
    result = run_step(steps, "Route traffic to deployed revision", {**cloud_environment, "TRAFFIC": traffic})
    assert result.returncode != 0
    assert "did not assign 100% traffic" in result.stdout


def test_empty_deployed_revision_fails_before_routing(steps, cloud_environment):
    result = run_step(steps, "Deploy API image", {**cloud_environment, "DEPLOYED_REVISION": ""})
    assert result.returncode != 0
    assert "no deployed revision" in result.stdout
    assert not Path(cloud_environment["GITHUB_OUTPUT"]).exists()


def test_rollback_covers_all_post_deploy_failures_and_restores_old_revision(steps, cloud_environment):
    step = next(step for step in steps if step.get("name") == "Roll traffic back after failed deployment validation")
    assert step["if"] == "failure() && steps.deploy.outcome == 'success'"
    result = run_step(steps, step["name"], cloud_environment)
    assert result.returncode == 0
    assert "--to-revisions api-old=100" in Path(cloud_environment["COMMAND_LOG"]).read_text()


@pytest.mark.parametrize("database_status", ["ok", "unreachable"])
def test_smoke_uses_non_reserved_paths_and_checks_database(steps, cloud_environment, database_status):
    directory = Path(cloud_environment["COMMAND_LOG"]).parent
    curl = directory / "curl"
    curl.write_text(
        '#!/bin/bash\n'
        'authorization=""\n'
        'url=""\n'
        'while (($#)); do\n'
        '  if [ "$1" = "--header" ]; then authorization="$2"; shift 2; continue; fi\n'
        '  url="$1"\n'
        '  shift\n'
        'done\n'
        'if [ "$authorization" != "Authorization: Bearer offline-token" ]; then\n'
        '  echo "unexpected authorization header: $authorization" >&2\n'
        '  exit 22\n'
        'fi\n'
        'printf "%s %s\\n" "$authorization" "$url" >> "$COMMAND_LOG"\n'
        'case "$url" in\n'
        '  */live|*/ticker/NVDA) printf 200 ;;\n'
        '  */health) printf \'{"app":"ok","db":"%s"}\' "$DATABASE_STATUS" ;;\n'
        '  *) exit 22 ;;\n'
        'esac\n'
    )
    curl.chmod(0o755)
    jq = directory / "jq"
    jq.write_text(
        '#!/usr/bin/env python3\n'
        'import json, sys\n'
        'assert sys.argv[1:] == ["-e", \'.app == "ok" and .db == "ok"\']\n'
        'body = json.load(sys.stdin)\n'
        'sys.exit(0 if body == {"app": "ok", "db": "ok"} else 1)\n'
    )
    jq.chmod(0o755)
    result = run_step(steps, "Smoke test private service", {
        **cloud_environment, "SERVICE_URL": "https://offline.invalid", "ID_TOKEN": "offline-token",
        "DATABASE_STATUS": database_status,
    })
    assert result.returncode == (0 if database_status == "ok" else 1)
    requests = Path(cloud_environment["COMMAND_LOG"]).read_text().splitlines()
    authorization_header = "Authorization: Bearer offline-token"
    expected_requests = [
        f"{authorization_header} https://offline.invalid/live",
        f"{authorization_header} https://offline.invalid/health",
    ]
    if database_status == "ok":
        expected_requests.append(f"{authorization_header} https://offline.invalid/ticker/NVDA")
    else:
        assert "did not report app and database healthy" in result.stdout
    assert requests == expected_requests
