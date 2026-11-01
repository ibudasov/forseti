"""Placeholders for milestones whose resources do not exist yet; each issue replaces its row with real checks."""
from __future__ import annotations

from scripts.cloud_scorecard.environment import Environment
from scripts.cloud_scorecard.model import Check, Cost, Outcome

PLANNED = (
    ("GCP-3", "database", "#125", Cost.FREE),
    ("GCP-5", "refresh job and reports bucket", "#127", Cost.FREE),
    ("GCP-6", "observability", "#128", Cost.FREE),
    ("Exit criteria", "demo and trust", "#129", Cost.PAID),
)


def _planned_check(group: str, subject: str, issue: str, cost: Cost) -> Check:
    def not_started(_: Environment) -> Outcome:
        return Outcome.pending(f"checks arrive with {issue}")

    identifier = f"{group.lower().replace(' ', '-')}.planned"
    return Check(identifier, group, f"{subject[0].upper()}{subject[1:]} checks", cost, not_started)


CHECKS = tuple(_planned_check(*planned) for planned in PLANNED)
