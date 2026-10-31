"""Entrypoint: `python -m scripts.cloud_scorecard [--full] > scorecard.md`."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scripts.cloud_scorecard.environment import CloudQueryError, Environment, query_gcloud, query_github
from scripts.cloud_scorecard.redaction import redact
from scripts.cloud_scorecard.registry import ALL_CHECKS, GROUPS, run_checks
from scripts.cloud_scorecard.render import render_markdown

MANIFEST = Path("infra/terraform/bootstrap/identities.json")
COMMIT_LENGTH = 7
API_SERVICE = "forseti-api"


def build_environment() -> Environment:
    return Environment(
        project=os.environ["GCP_PROJECT_ID"],
        region=os.environ["GCP_REGION"],
        repository=os.environ["GITHUB_REPOSITORY"],
        repository_id=os.environ["GITHUB_REPOSITORY_ID"],
        owner_id=os.environ["GITHUB_REPOSITORY_OWNER_ID"],
        manifest=json.loads(MANIFEST.read_text()),
        gcloud=query_gcloud,
        github=query_github,
    )


def current_revision(env: Environment) -> str:
    try:
        service = env.gcloud(["run", "services", "describe", API_SERVICE, "--region", env.region, "--project", env.project])
    except CloudQueryError:
        return "unavailable"
    if service is None:
        return "none yet"
    return service.get("status", {}).get("latestReadyRevisionName", "none yet")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="also run paid checks")
    arguments = parser.parse_args()
    env = build_environment()
    reports = run_checks(ALL_CHECKS, env, include_paid=arguments.full)
    commit = os.environ.get("GITHUB_SHA", "unknown")[:COMMIT_LENGTH]
    print(redact(render_markdown(reports, GROUPS, commit, current_revision(env))), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
