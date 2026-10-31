"""Check vocabulary: statuses, outcomes, and the registry entry type."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable

from scripts.cloud_scorecard.environment import Environment


class Status(Enum):
    PASS = "✅"
    FAIL = "❌"
    PENDING = "⏳"
    SKIP = "➖"


class Cost(Enum):
    FREE = "free"
    PAID = "paid"


@dataclass(frozen=True)
class Outcome:
    status: Status
    detail: str

    @staticmethod
    def passed(detail: str) -> "Outcome":
        return Outcome(Status.PASS, detail)

    @staticmethod
    def failed(detail: str) -> "Outcome":
        return Outcome(Status.FAIL, detail)

    @staticmethod
    def pending(detail: str) -> "Outcome":
        return Outcome(Status.PENDING, detail)

    @staticmethod
    def skipped(detail: str) -> "Outcome":
        return Outcome(Status.SKIP, detail)


def worst_outcome(outcomes: Iterable[Outcome], passing_detail: str) -> Outcome:
    """Combine sub-results: any failure wins over pending, which wins over pass."""
    collected = list(outcomes)
    for status in (Status.FAIL, Status.PENDING):
        details = [outcome.detail for outcome in collected if outcome.status is status]
        if details:
            return Outcome(status, "; ".join(details))
    return Outcome.passed(passing_detail)


@dataclass(frozen=True)
class Check:
    identifier: str
    group: str
    description: str
    cost: Cost
    run: Callable[[Environment], Outcome]


@dataclass(frozen=True)
class CheckReport:
    check: Check
    outcome: Outcome
