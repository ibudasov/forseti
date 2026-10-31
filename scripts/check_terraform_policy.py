"""Conservative offline checks for Terraform declarations and saved plans."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


def forbidden_resource(resource_type: str, layer: str) -> bool:
    if resource_type == "google_service_account_key":
        return True
    if layer != "main":
        return False
    return (
        resource_type.startswith(("google_project_iam_", "google_service_account"))
        or resource_type.endswith("_iam_policy")
    )


def check_resources(resources: list[dict], layer: str) -> list[str]:
    return [
        f"{layer}: forbidden resource {resource['type']}.{resource['name']}"
        for resource in resources if forbidden_resource(resource["type"], layer)
    ]


def check_public_members(text: str, label: str) -> list[str]:
    if re.search(r"\ball(?:Authenticated)?Users\b", text):
        return [f"{label}: public IAM principals are forbidden"]
    return []


def hcl_declarations(text: str) -> tuple[list[dict], bool]:
    tokens = re.findall(
        r'//[^\n]*|\#[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|[A-Za-z_][\w-]*|[^\s]',
        text,
    )
    tokens = [token for token in tokens if not token.startswith(("//", "#", "/*"))]
    resources = []
    has_modules = False
    for index, token in enumerate(tokens):
        if token == "module" and index + 2 < len(tokens) and tokens[index + 2] == "{":
            has_modules = True
        if token != "resource" or index + 3 >= len(tokens) or tokens[index + 3] != "{":
            continue
        resource_type, name = tokens[index + 1:index + 3]
        resources.append({
            "type": json.loads(resource_type) if resource_type.startswith('"') else resource_type,
            "name": json.loads(name) if name.startswith('"') else name,
        })
    return resources, has_modules


def check_layer(directory: Path) -> list[str]:
    failures = []
    for path in sorted(directory.glob("*.tf*")):
        if path.name.endswith((".tfstate", ".tfstate.backup", ".tfvars")):
            continue
        text = path.read_text()
        failures.extend(check_public_members(text, str(path)))
        if path.name.endswith(".tf.json"):
            configuration = json.loads(text)
            has_modules = bool(configuration.get("module"))
            resources = [
                {"type": kind, "name": name}
                for kind, declarations in configuration.get("resource", {}).items()
                for name in declarations
            ]
        elif path.suffix == ".tf":
            resources, has_modules = hcl_declarations(text)
        else:
            continue
        failures.extend(check_resources(resources, directory.name))
        # Local/remote modules could hide forbidden resource declarations.
        if has_modules:
            failures.append(f"{path}: modules require extending the policy checker before use")
    return failures


def check_destroy(plan: dict) -> list[str]:
    return [
        change["address"]
        for change in plan.get("resource_changes", [])
        if "delete" in change["change"]["actions"]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("infra/terraform"))
    parser.add_argument("--plan", type=Path)
    arguments = parser.parse_args()
    if arguments.plan:
        result = subprocess.run(
            ["terraform", "show", "-json", str(arguments.plan)],
            capture_output=True, text=True, check=True,
        )
        destroyed = check_destroy(json.loads(result.stdout))
        if destroyed:
            print("Destructive changes refused: " + ", ".join(destroyed), file=sys.stderr)
            return 1
        return 0
    failures = check_layer(arguments.root / "bootstrap") + check_layer(arguments.root / "main")
    identities = arguments.root / "bootstrap/identities.json"
    failures.extend(check_public_members(identities.read_text(), str(identities)))
    for failure in failures:
        print(failure, file=sys.stderr)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
