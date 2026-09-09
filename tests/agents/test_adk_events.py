from __future__ import annotations

from types import SimpleNamespace

from agents.orchestration.adk_events import (
    event_author,
    event_function_calls,
    event_text,
    event_token_usage,
)


def test_function_calls_are_extracted_from_object_event():
    event = SimpleNamespace(
        content=SimpleNamespace(
            parts=[
                SimpleNamespace(function_call=SimpleNamespace(name="structured_data_collector")),
                SimpleNamespace(function_call=SimpleNamespace(name="transfer_to_agent")),
                SimpleNamespace(function_call=SimpleNamespace(name="transfer_to_agent")),
            ]
        )
    )

    assert event_function_calls(event) == [
        "structured_data_collector",
        "transfer_to_agent",
        "transfer_to_agent",
    ]


def test_function_calls_are_extracted_from_mapping_event():
    event = {
        "content": {
            "parts": [
                {"function_call": {"name": "a"}},
                {"function_call": {"name": "b"}},
            ]
        }
    }

    assert event_function_calls(event) == ["a", "b"]


def test_text_is_extracted_from_multi_part_content():
    event = {
        "content": {
            "parts": [
                {"text": "first "},
                {"function_call": {"name": "ignored"}},
                {"text": "second"},
            ]
        }
    }

    assert event_text(event) == "first second"


def test_missing_content_returns_empty_text():
    assert event_text({}) == ""


def test_author_falls_back_to_unknown_agent():
    assert event_author({}) == "unknown_agent"


def test_token_usage_reads_adk_usage_metadata_object():
    class Usage:
        prompt_token_count = 10
        candidates_token_count = 4
        total_token_count = 14

    class Event:
        usage_metadata = Usage()

    assert event_token_usage(Event()) == {
        "prompt_token_count": 10,
        "candidates_token_count": 4,
        "total_token_count": 14,
    }


def test_token_usage_reads_mapping_metadata():
    event = {"usage_metadata": {"prompt_token_count": 3, "total_token_count": 3}}

    assert event_token_usage(event) == {
        "prompt_token_count": 3,
        "total_token_count": 3,
    }
