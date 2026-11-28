"""Render Terraform plan and drift summaries without exposing resource names or values."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import sys
from typing import Any

RESOURCE_TYPE = re.compile(r"^[A-Za-z0-9_]+$")
DEFAULT_TITLE = "Drift summary (redacted)"
ACTION_ORDER = ("create", "update", "replace", "delete", "read")
ACTION_LABELS = {
    ("create",): "create",
    ("update",): "update",
    ("delete",): "delete",
    ("read",): "read",
    ("delete", "create"): "replace",
    ("create", "delete"): "replace",
}


def _action_label(actions: Any) -> str:
    if not isinstance(actions, list) or not all(isinstance(action, str) for action in actions):
        raise ValueError("Terraform resource actions are missing or invalid.")

    if actions == ["no-op"]:
        return "no-op"

    label = ACTION_LABELS.get(tuple(actions))
    if label is None:
        raise ValueError("Terraform resource actions are missing or invalid.")
    return label


def render_summary(plan: Any, title: str = DEFAULT_TITLE) -> str:
    if not isinstance(plan, dict) or not isinstance(plan.get("resource_changes"), list):
        raise ValueError("Terraform plan has no resource changes list.")

    counts: Counter[tuple[str, str]] = Counter()
    action_totals: Counter[str] = Counter()
    for resource_change in plan["resource_changes"]:
        if not isinstance(resource_change, dict):
            raise ValueError("Terraform resource change is invalid.")

        resource_type = resource_change.get("type")
        change = resource_change.get("change")
        if not isinstance(resource_type, str) or not RESOURCE_TYPE.fullmatch(resource_type):
            raise ValueError("Terraform resource type is missing or invalid.")
        if not isinstance(change, dict):
            raise ValueError("Terraform resource change details are missing.")

        action = _action_label(change.get("actions"))
        if action != "no-op":
            counts[(resource_type, action)] += 1
            action_totals[action] += 1

    if not counts:
        return f"## {title}\n\nNo resource changes to summarize.\n"

    totals = ", ".join(
        f"{action_totals[action]} to {action}"
        for action in ACTION_ORDER
        if action_totals[action] or action in ("create", "update", "replace", "delete")
    )
    rows = [
        f"| {resource_type} | {action} | {count} |"
        for (resource_type, action), count in sorted(counts.items())
    ]
    return "\n".join([
        f"## {title}",
        "",
        f"Plan: {totals}.",
        "",
        "| Resource type | Action | Count |",
        "|---|---|---:|",
        *rows,
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", default=DEFAULT_TITLE)
    arguments = parser.parse_args(argv)
    try:
        summary = render_summary(json.load(sys.stdin), title=arguments.title)
    except json.JSONDecodeError as error:
        print(f"Invalid Terraform plan JSON: {error.msg}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    sys.stdout.write(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
