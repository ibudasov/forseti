"""Check registry and runner."""
from __future__ import annotations

from typing import Sequence

from scripts.cloud_scorecard import gcp2, later_groups
from scripts.cloud_scorecard.environment import CloudQueryError, Environment
from scripts.cloud_scorecard.model import Check, CheckReport, Cost, Outcome

GROUPS = ("GCP-2", "GCP-3", "GCP-3b", "GCP-4", "GCP-5", "GCP-6", "Exit criteria")
ALL_CHECKS: Sequence[Check] = (*gcp2.CHECKS, *later_groups.CHECKS)


def run_check(check: Check, env: Environment, include_paid: bool) -> CheckReport:
    if check.cost is Cost.PAID and not include_paid:
        return CheckReport(check, Outcome.skipped("paid check; run `/verify full`"))
    try:
        return CheckReport(check, check.run(env))
    except CloudQueryError as error:
        return CheckReport(check, Outcome.failed(str(error)))
    except Exception as error:  # a broken check must not hide the other results
        return CheckReport(check, Outcome.failed(f"check crashed: {type(error).__name__}"))


def run_checks(checks: Sequence[Check], env: Environment, include_paid: bool) -> Sequence[CheckReport]:
    return [run_check(check, env, include_paid) for check in checks]
