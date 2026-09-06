from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

from app.db.repository import (
    count_document_chunks,
    count_earnings_events,
    count_price_bars,
    get_latest_bars,
    get_latest_fundamental,
    get_latest_macro_daily,
    get_latest_technical_feature,
    get_next_earnings_event,
    list_active_securities,
)
from app.schemas.diagnostics import (
    UniverseCoverage,
    UniverseDiagnosticsItem,
    UniverseDiagnosticsResponse,
)
from app.services.analyzer import ENGINE_VERSION, analyze

logger = logging.getLogger(__name__)


def _sector(security) -> str:
    return security.sector_tag.value if hasattr(security.sector_tag, "value") else str(security.sector_tag)


def build_universe_diagnostics(engine=None, today: date | None = None) -> UniverseDiagnosticsResponse:
    today = today or datetime.now(timezone.utc).date()
    started = time.perf_counter()
    securities = list_active_securities(engine=engine)
    coverage = UniverseCoverage()
    blocked_by: dict[str, Counter[str]] = defaultdict(Counter)
    items = []
    macro = get_latest_macro_daily(engine=engine)

    for security in securities:
        ticker = security.ticker
        bars = get_latest_bars(ticker, 1, engine=engine)
        price_bar_count = count_price_bars(ticker, engine=engine)
        technical = get_latest_technical_feature(ticker, engine=engine)
        fundamental = get_latest_fundamental(ticker, engine=engine)
        earnings_count = count_earnings_events(ticker, engine=engine)
        next_earnings = get_next_earnings_event(ticker, date.min, engine=engine)
        document_count = count_document_chunks(ticker, engine=engine)
        coverage.tickers_with_price_bars += price_bar_count > 0
        coverage.tickers_with_200_bars += price_bar_count >= 200
        coverage.tickers_with_technical_features += technical is not None
        coverage.tickers_with_fundamentals += fundamental is not None
        coverage.tickers_with_earnings_events += earnings_count > 0
        coverage.tickers_with_document_chunks += document_count > 0

        try:
            response = analyze(ticker, engine=engine, today=today)
        except Exception as exc:
            blocked_by["errors"]["count"] += 1
            items.append(
                UniverseDiagnosticsItem(
                    ticker=ticker,
                    sector_tag=_sector(security),
                    price_bar_count=price_bar_count,
                    latest_bar_date=bars[0].bar_date if bars else None,
                    price_age_days=(today - bars[0].bar_date).days if bars else None,
                    has_technical_features=technical is not None,
                    technical_as_of=technical.as_of_date if technical else None,
                    has_fundamentals=fundamental is not None,
                    fundamental_as_of=fundamental.as_of_date if fundamental else None,
                    earnings_event_count=earnings_count,
                    next_earnings_date=next_earnings.report_date if next_earnings else None,
                    document_chunk_count=document_count,
                    decision="error",
                    debug_reason=(
                        f"error/{type(exc).__name__}: "
                        f"{str(exc).splitlines()[0] if str(exc) else type(exc).__name__}"
                    ),
                )
            )
            continue

        diagnosis = response.diagnosis
        if response.decision == "trade":
            blocked_by["passed"]["count"] += 1
        elif diagnosis is not None:
            stage_counts = blocked_by.setdefault(diagnosis.stage, Counter())
            assert isinstance(stage_counts, Counter)
            stage_counts[diagnosis.rule_id] += 1

        latest_bar = bars[0] if bars else None
        items.append(
            UniverseDiagnosticsItem(
                ticker=ticker,
                sector_tag=_sector(security),
                price_bar_count=price_bar_count,
                latest_bar_date=latest_bar.bar_date if latest_bar else None,
                price_age_days=(today - latest_bar.bar_date).days if latest_bar else None,
                has_technical_features=technical is not None,
                technical_as_of=technical.as_of_date if technical else None,
                has_fundamentals=fundamental is not None,
                fundamental_as_of=fundamental.as_of_date if fundamental else None,
                earnings_event_count=earnings_count,
                next_earnings_date=next_earnings.report_date if next_earnings else None,
                document_chunk_count=document_count,
                decision=response.decision,
                checklist_score=diagnosis.checklist_score if diagnosis else None,
                debug_reason=diagnosis.debug_reason if diagnosis else "error/DiagnosisMissing: analyzer returned no diagnosis",
            )
        )

    coverage.latest_macro_daily_date = macro.obs_date if macro else None
    blocked_by_payload = {
        stage: (counts["count"] if stage in {"errors", "passed"} else dict(counts))
        for stage, counts in blocked_by.items()
    }
    blocked_by_payload.setdefault("data_gate", {})
    blocked_by_payload.setdefault("hard_veto", {})
    blocked_by_payload.setdefault("checklist", {})
    blocked_by_payload.setdefault("risk_math", {})
    blocked_by_payload.setdefault("passed", 0)
    response = UniverseDiagnosticsResponse(
        generated_at=datetime.now(timezone.utc),
        engine_version=ENGINE_VERSION,
        universe_size=len(securities),
        coverage=coverage,
        blocked_by=blocked_by_payload,
        items=items,
    )
    logger.info(
        "universe_diagnostics_completed",
        extra={"universe_size": len(securities), "duration_ms": round((time.perf_counter() - started) * 1000, 3)},
    )
    return response
