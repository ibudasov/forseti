"""Tests for ADK workflow trace metadata."""
from __future__ import annotations

from agents.orchestration.workflow import _token_usage_from_event


def test_token_usage_reads_adk_usage_metadata_object():
    class Usage:
        prompt_token_count = 10
        candidates_token_count = 4
        total_token_count = 14

    class Event:
        usage_metadata = Usage()

    assert _token_usage_from_event(Event()) == {
        "prompt_token_count": 10,
        "candidates_token_count": 4,
        "total_token_count": 14,
    }


def test_token_usage_reads_mapping_metadata():
    event = {"usage_metadata": {"prompt_token_count": 3, "total_token_count": 3}}

    assert _token_usage_from_event(event) == {
        "prompt_token_count": 3,
        "total_token_count": 3,
    }