"""Shared canonical-JSON hashing for immutable analyst context payloads.

Every deterministic analyst context (fundamental, critique, technical, ...)
is hashed the same way: canonical, sorted, whitespace-stable JSON over a
payload with run-identifying and self-referential fields removed. This keeps
identical database state producing an identical hash across analysts and
across replay runs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def compute_context_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
