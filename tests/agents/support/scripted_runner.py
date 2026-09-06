"""Small, deterministic ADK-shaped runner used by trajectory tests."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Sequence


@dataclass(frozen=True)
class Turn:
    """One scripted interaction in an agent run."""

    author: str
    tool_calls: tuple[str, ...] = ()
    text: str = ""
    token_usage: dict[str, int] | None = None
    error_code: str | None = None
    error_message: str | None = None


def _event_for(turn: Turn) -> Any:
    parts = [SimpleNamespace(text=turn.text)]
    parts.extend(
        SimpleNamespace(function_call=SimpleNamespace(name=name))
        for name in turn.tool_calls
    )
    return SimpleNamespace(
        author=turn.author,
        content=SimpleNamespace(parts=parts),
        usage_metadata=SimpleNamespace(**(turn.token_usage or {})),
        error_code=turn.error_code,
        error_message=turn.error_message,
    )


def scripted_runner(script: Sequence[Turn]) -> Callable[[Any, str], Iterable[Any]]:
    """Return a runner factory that yields one ADK-shaped event per turn."""

    events = tuple(_event_for(turn) for turn in script)

    def run(registry: Any, ticker: str) -> Iterable[Any]:
        return iter(events)

    return run


def exploding_runner(exception: Exception) -> Callable[[Any, str], Iterable[Any]]:
    """Return a runner factory that raises the supplied exception."""

    def run(registry: Any, ticker: str) -> Iterable[Any]:
        raise exception

    return run


def empty_runner() -> Callable[[Any, str], Iterable[Any]]:
    """Return a runner factory that produces no events."""

    return scripted_runner(())
