"""Scrub anything that must not appear in a public comment or job log."""
from __future__ import annotations

import re

URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PROJECT_NUMBER = re.compile(r"(projects/)\d+")
LONG_NUMBER = re.compile(r"\b\d{9,}\b")
LONG_IDENTIFIER = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")


def redact(text: str) -> str:
    """Replace URLs, emails, project numbers and long identifiers with placeholders."""
    scrubbed = URL.sub("[url]", text)
    scrubbed = EMAIL.sub("[email]", scrubbed)
    scrubbed = PROJECT_NUMBER.sub(r"\1[project-number]", scrubbed)
    scrubbed = LONG_NUMBER.sub("[id]", scrubbed)
    return LONG_IDENTIFIER.sub("[id]", scrubbed)
