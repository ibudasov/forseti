from datetime import date, datetime, timezone

from app.db.models import AgentRun
from app.services.agent_run_dates import (
    render_agent_run_date_summary,
    resolve_agent_run_as_of_date,
    summarize_agent_run_dates,
)


def _agent_run(**overrides) -> AgentRun:
    values = {
        "run_id": "run-1",
        "ticker": "NVDA",
        "created_at": datetime(2026, 10, 24, tzinfo=timezone.utc),
        "token_usage": {},
        "warnings": [],
    }
    values.update(overrides)
    return AgentRun(**values)


def test_resolve_agent_run_as_of_date_prefers_persisted_date():
    run = _agent_run(as_of_date=date(2024, 1, 2))

    assert resolve_agent_run_as_of_date(run) == date(2024, 1, 2)


def test_resolve_agent_run_as_of_date_warns_before_falling_back(caplog):
    run = _agent_run()

    assert resolve_agent_run_as_of_date(run) == date(2026, 10, 24)
    assert "agent_run_as_of_date_missing run_id=run-1 fallback=created_at" in caplog.text


def test_summarize_agent_run_dates_reports_range_and_backfilled_count():
    runs = [
        _agent_run(
            run_id="backfilled",
            as_of_date=date(2024, 1, 2),
            as_of_date_source="created_at_backfill",
        ),
        _agent_run(
            run_id="requested",
            as_of_date=date(2024, 2, 1),
            as_of_date_source="request",
        ),
    ]

    summary = summarize_agent_run_dates(runs)

    assert summary == {
        "as_of_date_start": "2024-01-02",
        "as_of_date_end": "2024-02-01",
        "backfilled_rows_used": 1,
    }
    assert "backfilled_rows_used: 1" in render_agent_run_date_summary(summary)
