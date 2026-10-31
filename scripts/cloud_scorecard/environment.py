"""Everything a check may read: manifest, identifiers and two read-only JSON sources."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

JsonQuery = Callable[[Sequence[str]], Any]

NOT_FOUND_MARKERS = ("NOT_FOUND", "was not found", "does not exist", "HTTPError 404")
ACCESS_DENIED_MARKERS = ("PERMISSION_DENIED", "HTTPError 403")
QUERY_TIMEOUT_SECONDS = 120


class CloudQueryError(Exception):
    """A read failed. The message is deliberately generic: raw tool output may hold private values."""


@dataclass(frozen=True)
class Environment:
    project: str
    region: str
    repository: str
    repository_id: str
    owner_id: str
    manifest: Mapping[str, Any]
    gcloud: JsonQuery
    github: JsonQuery

    def service_account_email(self, name: str) -> str:
        return f"{name}@{self.project}.iam.gserviceaccount.com"


def _run_json(command: Sequence[str], tool: str) -> Any:
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=QUERY_TIMEOUT_SECONDS, check=False,
    )
    if completed.returncode == 0:
        return json.loads(completed.stdout or "null")
    if any(marker in completed.stderr for marker in NOT_FOUND_MARKERS):
        return None
    if any(marker in completed.stderr for marker in ACCESS_DENIED_MARKERS):
        raise CloudQueryError(f"{tool} access denied")
    raise CloudQueryError(f"{tool} query failed")


def query_gcloud(arguments: Sequence[str]) -> Any:
    """Run a read-only gcloud command. Returns None when the resource does not exist."""
    return _run_json(["gcloud", *arguments, "--format=json"], "gcloud")


def query_github(arguments: Sequence[str]) -> Any:
    """Read one GitHub REST path through the gh CLI."""
    return _run_json(["gh", "api", *arguments], "GitHub")
