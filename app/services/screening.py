from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Optional

from app.db.repository import list_active_securities
from app.schemas.analyze import AnalyzeResponse
from app.schemas.screening import ScreeningItem, ScreeningResponse
from app.services import analyzer as analyzer_module
from app.services.analyzer import ENGINE_VERSION

logger = logging.getLogger(__name__)

_DECISION_RANK = {"trade": 0, "watchlist": 1, "no_trade": 2}


def _to_screening_item(security, response: AnalyzeResponse) -> ScreeningItem:
    return ScreeningItem(
        ticker=response.ticker,
        sector_tag=security.sector_tag.value if hasattr(security.sector_tag, "value") else str(security.sector_tag),
        status="ok",
        decision=response.decision,
        entry_range=response.entry_range,
        stop_loss=response.stop_loss,
        take_profit=response.take_profit,
        risk_reward=response.risk_reward,
        position_size_eur=response.position_size_eur,
        confidence=float(response.confidence),
        warnings=list(response.warnings),
        error=None,
    )


def _to_error_item(security, exc: Exception) -> ScreeningItem:
    return ScreeningItem(
        ticker=security.ticker,
        sector_tag=security.sector_tag.value if hasattr(security.sector_tag, "value") else str(security.sector_tag),
        status="error",
        decision=None,
        entry_range=None,
        stop_loss=None,
        take_profit=None,
        risk_reward=None,
        position_size_eur=None,
        confidence=None,
        warnings=[],
        error=str(exc),
    )


def _sort_key(item: ScreeningItem):
    if item.status == "error":
        return (1, 0, 0.0, item.ticker)
    return (0, _DECISION_RANK[item.decision], -(item.confidence or 0.0), item.ticker)


def _sort_items(items: list[ScreeningItem]) -> list[ScreeningItem]:
    return sorted(items, key=_sort_key)


def run_screening(engine=None, today: Optional[date] = None) -> ScreeningResponse:
    if today is None:
        today = datetime.now(timezone.utc).date()

    started = time.perf_counter()
    securities = list_active_securities(engine=engine)
    items = []

    for security in securities:
        try:
            response = analyzer_module.analyze(security.ticker, engine=engine, today=today)
            items.append(_to_screening_item(security, response))
        except Exception as exc:
            logger.exception("screening_ticker_failed", extra={"ticker": security.ticker})
            items.append(_to_error_item(security, exc))

    ordered_items = _sort_items(items)
    trade_count = sum(1 for item in ordered_items if item.status == "ok" and item.decision == "trade")
    watchlist_count = sum(1 for item in ordered_items if item.status == "ok" and item.decision == "watchlist")
    no_trade_count = sum(1 for item in ordered_items if item.status == "ok" and item.decision == "no_trade")
    failed_count = sum(1 for item in ordered_items if item.status == "error")
    analyzed_count = len(ordered_items) - failed_count

    response = ScreeningResponse(
        generated_at=datetime.now(timezone.utc),
        engine_version=ENGINE_VERSION,
        universe_size=len(securities),
        analyzed_count=analyzed_count,
        failed_count=failed_count,
        trade_count=trade_count,
        watchlist_count=watchlist_count,
        no_trade_count=no_trade_count,
        items=ordered_items,
    )
    response.summary = {
        "total": len(ordered_items),
        "trade": trade_count,
        "watchlist": watchlist_count,
        "no_trade": no_trade_count,
        "errors": failed_count,
    }
    logger.info(
        "screening_completed",
        extra={
            "universe_size": len(securities),
            "analyzed_count": analyzed_count,
            "failed_count": failed_count,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        },
    )
    return response
