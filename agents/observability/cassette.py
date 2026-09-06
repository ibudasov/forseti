"""Offline replay support for recorded agent events."""
from __future__ import annotations

import argparse
import json
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator


class CassetteError(ValueError):
    """Raised when a replay cassette is invalid."""


class NetworkAccessError(RuntimeError):
    """Raised when replay code attempts to access the network."""


def _event_from_payload(payload: dict[str, Any]) -> Any:
    parts = [SimpleNamespace(text=payload.get("text", ""))]
    parts.extend(
        SimpleNamespace(function_call=SimpleNamespace(name=name))
        for name in payload.get("function_calls", [])
    )
    return SimpleNamespace(
        author=payload.get("author"),
        content=SimpleNamespace(parts=parts),
        usage_metadata=SimpleNamespace(**payload.get("token_usage", {})),
        error_code=payload.get("error_code"),
        error_message=payload.get("error_message"),
    )


@dataclass(frozen=True)
class Cassette:
    """A normalized, deterministic recording of one agent run."""

    run_id: str
    ticker: str
    events: tuple[Any, ...]
    expected: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "Cassette":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CassetteError(f"could not read cassette {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise CassetteError(f"cassette {path} must contain an object")
        try:
            events = tuple(_event_from_payload(item) for item in payload["events"])
            return cls(
                run_id=str(payload["run_id"]),
                ticker=str(payload["ticker"]),
                events=events,
                expected=dict(payload["expected"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CassetteError(f"cassette {path} has an invalid shape: {exc}") from exc


class CassetteRunner:
    """ADK-shaped runner which only yields events stored in a cassette."""

    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette

    @classmethod
    def from_path(cls, path: Path) -> "CassetteRunner":
        return cls(Cassette.load(path))

    def __call__(self, registry: Any, ticker: str) -> Iterator[Any]:
        if ticker != self.cassette.ticker:
            raise CassetteError(
                f"cassette ticker is {self.cassette.ticker!r}, received {ticker!r}"
            )
        return iter(self.cassette.events)


@contextmanager
def offline_network_guard() -> Iterator[None]:
    """Reject socket access while replaying a cassette."""

    def reject(*args: Any, **kwargs: Any) -> None:
        raise NetworkAccessError("network access is disabled during cassette replay")

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection
    setattr(socket.socket, "connect", reject)
    setattr(socket.socket, "connect_ex", reject)
    setattr(socket, "create_connection", reject)
    try:
        yield
    finally:
        setattr(socket.socket, "connect", original_connect)
        setattr(socket.socket, "connect_ex", original_connect_ex)
        setattr(socket, "create_connection", original_create_connection)


def replay_cassette(path: Path) -> Cassette:
    """Load and validate a cassette without contacting external services."""

    cassette = Cassette.load(path)
    with offline_network_guard():
        list(CassetteRunner(cassette)(None, cassette.ticker))
    return cassette


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--cassette-dir", type=Path, default=Path("tests/fixtures/golden"))
    args = parser.parse_args()
    path = args.cassette_dir / f"{args.run_id}.json"
    cassette = replay_cassette(path)
    print(json.dumps({"run_id": cassette.run_id, "ticker": cassette.ticker,
                      "event_count": len(cassette.events)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
