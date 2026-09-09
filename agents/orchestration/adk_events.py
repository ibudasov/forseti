"""Safe ADK event accessors used by workflow trace observation."""
from __future__ import annotations

from typing import Any

_TOKEN_FIELDS = (
    "prompt_token_count",
    "candidates_token_count",
    "total_token_count",
    "cached_content_token_count",
    "thoughts_token_count",
)


def _event_value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _content_parts(event: Any) -> list[Any]:
    content = _event_value(event, "content")
    parts = _event_value(content, "parts")
    if not isinstance(parts, list):
        return []
    return parts


def _first_line(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return ""
    return text.splitlines()[0]


def event_author(event: Any) -> str:
    return str(_event_value(event, "author") or "unknown_agent")


def event_function_calls(event: Any) -> list[str]:
    calls: list[str] = []
    for part in _content_parts(event):
        function_call = _event_value(part, "function_call")
        name = _event_value(function_call, "name")
        if name:
            calls.append(str(name))
    return calls


def event_text(event: Any) -> str:
    text_parts: list[str] = []
    for part in _content_parts(event):
        text = _event_value(part, "text")
        if isinstance(text, str):
            text_parts.append(text)
    return "".join(text_parts)


def event_error(event: Any) -> str | None:
    message = _event_value(event, "error_message")
    if message:
        detail = _first_line(message)
        return detail or None
    code = _event_value(event, "error_code")
    if code:
        detail = _first_line(code)
        return detail or None
    return None


def event_token_usage(event: Any) -> dict[str, int]:
    usage_metadata = _event_value(event, "usage_metadata")
    if usage_metadata is None:
        return {}

    usage: dict[str, int] = {}
    for field_name in _TOKEN_FIELDS:
        value = _event_value(usage_metadata, field_name)
        if isinstance(value, int):
            usage[field_name] = value
    return usage
