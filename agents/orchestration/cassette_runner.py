"""Offline replay runner for recorded ADK event files."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from agents.orchestration.workflow import RunnerFactory

_EVENT_FILE_GLOB = "003-event-*.json"


class CassetteExhaustedError(RuntimeError):
    """Raised when a cassette skips an expected event file."""


class CassetteNotFoundError(FileNotFoundError):
    """Raised when a cassette directory does not exist."""


@dataclass(frozen=True)
class RecordedPart:
    text: str | None = None
    function_call: Any | None = None
    function_response: Any | None = None


@dataclass(frozen=True)
class RecordedContent:
    parts: list[RecordedPart]


@dataclass(frozen=True)
class RecordedEvent:
    author: str
    content: RecordedContent
    usage_metadata: Any | None = None
    error_code: str | None = None
    error_message: str | None = None


def _function_call_part(name: str) -> RecordedPart:
    return RecordedPart(function_call=SimpleNamespace(name=name))


def _function_response_part(payload: Any) -> RecordedPart:
    return RecordedPart(function_response=payload)


def _usage_metadata(payload: dict[str, Any]) -> Any | None:
    if not payload:
        return None
    return SimpleNamespace(**payload)


def _recorded_event_from_payload(path: Path, payload: dict[str, Any]) -> RecordedEvent:
    try:
        function_calls = payload.get("function_calls") or []
        function_responses = payload.get("function_responses") or []
        parts = []
        text = payload.get("text")
        if isinstance(text, str) and text:
            parts.append(RecordedPart(text=text))
        parts.extend(_function_call_part(str(name)) for name in function_calls)
        parts.extend(_function_response_part(response) for response in function_responses)
        return RecordedEvent(
            author=str(payload["author"]),
            content=RecordedContent(parts=parts),
            usage_metadata=_usage_metadata(dict(payload.get("usage_metadata") or {})),
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Malformed cassette event {path}: {exc}") from exc


def _load_event_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed cassette event {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Malformed cassette event {path}: expected a JSON object")
    return payload


def _iter_recorded_events(cassette_dir: Path) -> Iterator[RecordedEvent]:
    if not cassette_dir.is_dir():
        raise CassetteNotFoundError(f"Cassette directory not found: {cassette_dir}")

    event_paths = sorted(cassette_dir.glob(_EVENT_FILE_GLOB))
    for expected_index, path in enumerate(event_paths):
        expected_name = f"003-event-{expected_index:03d}.json"
        if path.name != expected_name:
            raise CassetteExhaustedError(
                f"Cassette {cassette_dir} is missing event file {expected_name}"
            )
        yield _recorded_event_from_payload(path, _load_event_payload(path))


def build_cassette_runner_factory(cassette_dir: Path) -> RunnerFactory:
    """Build a workflow-compatible runner that yields recorded events from disk."""

    def replay_runner(registry: Any, ticker: str) -> Iterator[RecordedEvent]:
        # The recorded event files already encode which agents ran and what they
        # emitted, so replay ignores the live registry by contract.
        del registry, ticker
        yield from _iter_recorded_events(cassette_dir)

    setattr(replay_runner, "ignores_registry", True)
    return replay_runner
