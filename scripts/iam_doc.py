"""Render access tables from the same manifest Terraform consumes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

START = "<!-- BEGIN GENERATED IAM -->"
END = "<!-- END GENERATED IAM -->"


def render_tables(manifest: dict) -> str:
    rows = [
        "| Identity | Purpose | Project roles | May act as | Workflow |",
        "|---|---|---|---|---|",
    ]
    for name, account in manifest["service_accounts"].items():
        roles = list(account["project_roles"])
        roles.extend(
            f"{grant['role']} ({grant['condition']})"
            for grant in account.get("conditional_project_roles", [])
        )
        workflow = account["workflow"]
        used_by = "Runtime only"
        if workflow:
            used_by = f"{workflow['file']} on main; {workflow['environment'] or 'no environment'}"
        rows.append(
            f"| `{name}` | {account['purpose']} | {', '.join(roles) or 'None'}"
            f" | {', '.join(account['act_as']) or 'None'} | {used_by} |"
        )
    rows.extend([
        "", "| State identity | Role | Object prefix |", "|---|---|---|",
        f"| owner | {manifest['state_owner_role']} | All |",
        f"| CI state identities | {manifest['state_listing_role']} | Bucket metadata/object names only |",
    ])
    for grant in manifest["state_grants"]:
        rows.append(f"| {grant['member']} | {grant['role']} | `{grant['prefix']}` |")
    rows.extend(["", "| Resource kind | Resource | Member | Role |", "|---|---|---|---|"])
    for grant in manifest["resource_grants"]:
        rows.append(f"| {grant['kind']} | {grant['resource']} | {grant['member']} | {grant['role']} |")
    if not manifest["resource_grants"]:
        rows.append("| None yet | Added by later milestones | None | None |")
    return "\n".join(rows)


def update_document(document: str, manifest: dict) -> str:
    before, remainder = document.split(START, 1)
    _, after = remainder.split(END, 1)
    return f"{before}{START}\n{render_tables(manifest)}\n{END}{after}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    document = Path("docs/gcp-access.md")
    manifest = json.loads(Path("infra/terraform/bootstrap/identities.json").read_text())
    current = document.read_text()
    generated = update_document(current, manifest)
    if arguments.check:
        if current != generated:
            print("IAM documentation is stale. Run make iam-doc.")
            return 1
        return 0
    document.write_text(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
