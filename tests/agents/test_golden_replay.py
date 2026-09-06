"""Golden cassette coverage for deterministic, offline agent runs."""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from agents.observability.cassette import (
    CassetteError,
    CassetteRunner,
    NetworkAccessError,
    offline_network_guard,
    replay_cassette,
)

GOLDEN_DIR = Path(__file__).parents[1] / "fixtures" / "golden"


@pytest.mark.parametrize("case_name", ["happy-path", "degraded-critic", "no-agent-events"])
def test_golden_cassette_replays_expected_events(case_name):
    cassette = replay_cassette(GOLDEN_DIR / f"{case_name}.json")
    observed = [
        [event.author, "failed" if event.error_code else "completed"]
        for event in CassetteRunner(cassette)(None, cassette.ticker)
    ]
    assert observed == cassette.expected["steps"]


def test_replay_rejects_network_access():
    with pytest.raises(NetworkAccessError, match="network access is disabled"):
        with offline_network_guard():
            socket.create_connection(("example.invalid", 443))


def test_replay_rejects_wrong_ticker():
    cassette = replay_cassette(GOLDEN_DIR / "happy-path.json")
    with pytest.raises(CassetteError, match="cassette ticker"):
        list(CassetteRunner(cassette)(None, "AAPL"))


def test_golden_fixtures_have_stable_expected_values():
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["expected"]["decision"] == "watchlist"
        assert payload["expected"]["confidence"] == 0.4
