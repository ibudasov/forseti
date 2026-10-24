from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

from app.db.models import AgentRun

logger = logging.getLogger(__name__)


def resolve_agent_run_as_of_date(run: AgentRun) -> date:
    if run.as_of_date is not None:
        return run.as_of_date
    logger.warning(
        "agent_run_as_of_date_missing run_id=%s fallback=created_at",
        run.run_id,
    )
    return run.created_at.date()


def summarize_agent_run_dates(runs: Sequence[AgentRun]) -> dict[str, Any]:
    if not runs:
        return {
            "as_of_date_start": None,
            "as_of_date_end": None,
            "backfilled_rows_used": 0,
        }

    as_of_dates = [resolve_agent_run_as_of_date(run) for run in runs]
    return {
        "as_of_date_start": min(as_of_dates).isoformat(),
        "as_of_date_end": max(as_of_dates).isoformat(),
        "backfilled_rows_used": sum(
            run.as_of_date_source == "created_at_backfill" for run in runs
        ),
    }


def render_agent_run_date_summary(summary: dict[str, Any]) -> str:
    date_range = "n/a"
    if summary["as_of_date_start"] is not None:
        date_range = f"{summary['as_of_date_start']} through {summary['as_of_date_end']}"
    return (
        f"- as_of_date_range: {date_range}\n"
        f"- backfilled_rows_used: {summary['backfilled_rows_used']}\n"
    )
