from __future__ import annotations

from datetime import date, datetime
from typing import Sequence

from app.schemas.analyze import AnalyzeResponse
from app.schemas.critique import AnalystView
from app.schemas.synthesis import DecisionSynthesisRequest
from app.services.decision_synthesizer import build_decision_synthesis_request

_DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}


def build_synthesis_request(
    *,
    run_id: str,
    response: AnalyzeResponse,
    analyst_views: Sequence[AnalystView],
    as_of_date: date,
    snapshot_at: datetime,
    critic_findings: Sequence[str] = (),
) -> DecisionSynthesisRequest:
    """Freeze the current deterministic response and analyst views for one synthesis call.

    The response's decision and confidence are the ceiling: the request never allows a promotion.
    """
    return build_decision_synthesis_request(
        run_id=run_id,
        ticker=response.ticker,
        as_of_date=as_of_date,
        snapshot_at=snapshot_at,
        baseline_decision=response.decision,
        baseline_confidence=response.confidence,
        entry_range=response.entry_range,
        stop_loss=response.stop_loss,
        take_profit=response.take_profit,
        risk_reward=response.risk_reward,
        position_size_eur=response.position_size_eur,
        time_stop_at=response.time_stop_at,
        reasons=response.reasons,
        warnings=response.warnings,
        diagnosis=response.diagnosis,
        analyst_views=list(analyst_views),
        known_metric_ids=_known_metric_ids(analyst_views),
        known_chunk_ids=_known_chunk_ids(analyst_views),
        known_warning_codes=_known_warning_codes(response.warnings),
        critic_findings=list(critic_findings),
        allowed_decisions=_decisions_up_to(response.decision),
        max_confidence=response.confidence,
    )


def _decisions_up_to(decision: str) -> list[str]:
    ceiling = _DECISION_RANK[decision]
    return [name for name, rank in _DECISION_RANK.items() if rank <= ceiling]


def _known_metric_ids(analyst_views: Sequence[AnalystView]) -> list[str]:
    return sorted({metric_id for view in analyst_views for finding in view.findings for metric_id in finding.metric_ids})


def _known_chunk_ids(analyst_views: Sequence[AnalystView]) -> list[int]:
    return sorted({chunk_id for view in analyst_views for finding in view.findings for chunk_id in finding.chunk_ids})


def _known_warning_codes(warnings: Sequence[str]) -> list[str]:
    return sorted({warning.split(":", maxsplit=1)[0] for warning in warnings if warning.strip()})
