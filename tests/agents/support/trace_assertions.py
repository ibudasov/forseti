"""Readable assertions for agent trajectory traces."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any


_DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}
_RISK_FIELDS = (
    "entry_range",
    "stop_loss",
    "take_profit",
    "risk_reward",
    "position_size_eur",
)


def _observed_steps(trace: Any) -> list[tuple[str, str]]:
    return [(step.agent_name, step.status) for step in trace.steps]


def assert_steps(trace: Any, expected: Sequence[tuple[str, str]]) -> None:
    """Assert the complete trace sequence and show both sequences on failure."""

    observed = _observed_steps(trace)
    expected_list = list(expected)
    if observed == expected_list:
        return

    expected_lines = "\n".join(f"  {index}: {step}" for index, step in enumerate(expected_list, 1))
    observed_lines = "\n".join(f"  {index}: {step}" for index, step in enumerate(observed, 1))
    raise AssertionError(
        "trajectory steps differ\n"
        f"expected:\n{expected_lines or '  <empty>'}\n"
        f"observed:\n{observed_lines or '  <empty>'}"
    )


def assert_step_order(trace: Any, agent_names: Sequence[str]) -> None:
    """Assert that named steps occur in relative order."""

    observed_names = [step.agent_name for step in trace.steps]
    positions = [observed_names.index(name) for name in agent_names]
    if positions != sorted(positions):
        raise AssertionError(
            f"agent order differs\nexpected: {list(agent_names)}\nobserved: {observed_names}"
        )


def assert_tool_calls(trace: Any, agent_name: str, expected_calls: Sequence[str]) -> None:
    """Assert tool calls for the first step belonging to an agent."""

    matching_steps = [step for step in trace.steps if step.agent_name == agent_name]
    if not matching_steps:
        raise AssertionError(f"agent {agent_name!r} was not observed")
    observed = matching_steps[0].tool_calls
    if observed != list(expected_calls):
        raise AssertionError(
            f"tool calls for {agent_name!r} differ\n"
            f"expected: {list(expected_calls)}\nobserved: {observed}"
        )


def assert_skipped_reason(trace: Any, agent_name: str, reason: str) -> None:
    """Assert that an agent was skipped for the supplied reason."""

    matching_steps = [step for step in trace.steps if step.agent_name == agent_name]
    if not matching_steps:
        raise AssertionError(f"agent {agent_name!r} was not observed")
    step = matching_steps[0]
    if step.status != "skipped" or step.skip_reason != reason:
        raise AssertionError(
            f"skip for {agent_name!r} differs\n"
            f"expected: ('skipped', {reason!r})\n"
            f"observed: ({step.status!r}, {step.skip_reason!r})"
        )


def assert_retries(trace: Any, agent_name: str, expected: int) -> None:
    """Assert retry count for the first step belonging to an agent."""

    matching_steps = [step for step in trace.steps if step.agent_name == agent_name]
    if not matching_steps:
        raise AssertionError(f"agent {agent_name!r} was not observed")
    observed = matching_steps[0].retries
    if observed != expected:
        raise AssertionError(
            f"retries for {agent_name!r} differ\nexpected: {expected}\nobserved: {observed}"
        )


def assert_never_upgraded(response: Any, deterministic: Any) -> None:
    """Assert that agent output only downgrades or annotates deterministic output."""

    response_rank = _DECISION_RANK[response.decision]
    deterministic_rank = _DECISION_RANK[deterministic.decision]
    if response_rank > deterministic_rank:
        raise AssertionError(
            f"decision was upgraded\n"
            f"deterministic: {deterministic.decision}\nresponse: {response.decision}"
        )
    if response.confidence > deterministic.confidence:
        raise AssertionError(
            f"confidence was upgraded\n"
            f"deterministic: {deterministic.confidence}\nresponse: {response.confidence}"
        )
    for field_name in _RISK_FIELDS:
        expected = getattr(deterministic, field_name)
        observed = getattr(response, field_name)
        if observed != expected:
            raise AssertionError(
                f"risk value changed for {field_name}\n"
                f"deterministic: {expected}\nresponse: {observed}"
            )
    if getattr(response, "time_stop_at", None) != deterministic.time_stop_at:
        raise AssertionError(
            "deterministic value changed for time_stop_at\n"
            f"deterministic: {deterministic.time_stop_at}\n"
            f"response: {getattr(response, 'time_stop_at', None)}"
        )
