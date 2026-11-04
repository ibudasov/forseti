"""Fail when a deployed Terraform resource is not mentioned in the architecture document.

Usage: terraform state list | python -m scripts.doc_coverage docs/architecture-gcp.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

INSTANCE_KEY = re.compile(r"\[.*\]$")
DECLARATION = re.compile(r'^resource\s+"([a-z0-9_]+)"\s+"([a-z0-9_]+)"', re.MULTILINE)


def resource_names(state_addresses: Iterable[str]) -> set[str]:
    """Reduce state addresses to `type.name`, dropping data sources and for_each keys."""
    names = set()
    for address in state_addresses:
        address = address.strip()
        if address and not address.startswith("data."):
            names.add(INSTANCE_KEY.sub("", address))
    return names


def declared_resources(configuration_directory: Path) -> set[str]:
    return {
        f"{resource_type}.{name}"
        for source in configuration_directory.glob("*.tf")
        for resource_type, name in DECLARATION.findall(source.read_text())
    }


def undocumented(resources: Iterable[str], document: str) -> list[str]:
    return sorted(resource for resource in resources if f"`{resource}`" not in document)


def main(argv: list[str]) -> int:
    document = Path(argv[0]).read_text()
    missing = undocumented(resource_names(sys.stdin), document)
    for resource in missing:
        print(f"Not mentioned in {argv[0]}: {resource}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
