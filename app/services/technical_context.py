"""Technical Analysis context builder.

Mirrors ``app.services.fundamental_context``: builds one immutable, hashed
``TechnicalAnalysisRequest`` per ticker/as-of snapshot before any Technical
Analyst model call. See ``docs/technical-analysis-context.md``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from app.db.models import PriceBar
from app.db.repository import (
    get_latest_bars,
    get_latest_macro_daily,
    get_latest_technical_feature,
    get_next_earnings_event,
    get_security,
)
from app.domain.technical_facts import build_technical_facts
from app.domain.technicals import DeterministicTechnicalAnalysis, TechnicalMetricRef, analyze_technicals
from app.ingestion.features import SMA_LONG, SMA_SHORT, compute_rsi, compute_sma
from app.schemas.technicals import (
    IndicatorSeriesPoint,
    TechnicalAnalysisRequest,
    TechnicalContextCoverage,
    TechnicalFact,
)
from app.services.context_hash import compute_context_hash

# Frozen constants for the technical context builder.
HISTORY_BARS_LOADED = 260
SERIES_WINDOW = 60
STALE_PRICE_DATA_THRESHOLD_DAYS = 7


def build_technical_analysis_request(
    ticker: str,
    run_id: str,
    *,
    as_of: date | None = None,
    engine=None,
    snapshot_at: datetime | None = None,
) -> TechnicalAnalysisRequest:
    normalized_ticker = ticker.strip().upper()
    as_of_date = as_of or datetime.now(timezone.utc).date()
    context_snapshot_at = snapshot_at or _snapshot_at(as_of_date)
    security = get_security(normalized_ticker, engine=engine)
    if security is None:
        raise ValueError(f"Unknown ticker: {normalized_ticker}")

    bars = list(
        reversed(get_latest_bars(normalized_ticker, HISTORY_BARS_LOADED, engine=engine, as_of_date=as_of_date))
    )
    technical_feature = get_latest_technical_feature(normalized_ticker, engine=engine, as_of_date=as_of_date)
    macro_daily = get_latest_macro_daily(engine=engine, as_of_date=as_of_date)
    vix_close = Decimal(str(macro_daily.vix)) if macro_daily is not None and macro_daily.vix is not None else None
    next_earnings = get_next_earnings_event(normalized_ticker, on_or_after=as_of_date, engine=engine)

    latest_bar = bars[-1] if bars else None
    deterministic_result = analyze_technicals(
        latest_bar,
        technical_feature,
        vix_close,
        ticker=normalized_ticker,
    )
    facts = _merge_facts(
        baseline_metrics=deterministic_result.metrics,
        derived_facts=build_technical_facts(
            bars,
            next_earnings_date=next_earnings.report_date if next_earnings is not None else None,
            as_of_date=as_of_date,
        ),
    )
    series = _build_series(bars)
    coverage = _build_coverage(
        bars=bars,
        latest_bar=latest_bar,
        facts=facts,
        series=series,
        as_of_date=as_of_date,
    )

    request = TechnicalAnalysisRequest(
        run_id=run_id,
        context_hash="pending",
        ticker=normalized_ticker,
        sector=_sector_value(security.sector_tag),
        snapshot_at=context_snapshot_at,
        as_of_date=as_of_date,
        deterministic_result=deterministic_result,
        facts=facts,
        series=series,
        coverage=coverage,
    )
    payload = request.model_dump(mode="json", exclude={"run_id", "context_hash", "snapshot_at"})
    return request.model_copy(update={"context_hash": compute_context_hash(payload)})


# Human-readable derivation text for each TA-1 baseline metric, keyed by its
# ``metric_name``. The baseline already computes these values; the text here
# only documents the source so the merged TechnicalFact stays self-describing.
_BASELINE_DERIVATIONS = {
    "close": "latest bar close",
    "sma_50": "simple moving average of the last 50 closes",
    "sma_200": "simple moving average of the last 200 closes",
    "rsi_14": "Wilder RSI-14",
    "volume_trend": "mean(last 20 volumes) / mean(prior 20 volumes), from TechnicalFeature",
    "vix_close": "latest VIX index close",
}


def _merge_facts(
    *,
    baseline_metrics: List[TechnicalMetricRef],
    derived_facts: List[TechnicalFact],
) -> List[TechnicalFact]:
    """Reuse every TA-1 baseline metric under its own ``metric_id``, then append
    the facts derived only for the technical context (no overlap in ``name``).
    """
    baseline_facts = [
        TechnicalFact(
            metric_id=metric.metric_id,
            name=metric.metric_name,
            value=metric.value,
            unit=metric.unit,
            derivation=_BASELINE_DERIVATIONS.get(metric.metric_name, metric.metric_name),
        )
        for metric in baseline_metrics
    ]
    return baseline_facts + derived_facts


def _build_series(bars: List[PriceBar]) -> List[IndicatorSeriesPoint]:
    """Build the last ``SERIES_WINDOW`` bars with their rolling indicator values.

    Each point's ``sma_50``/``sma_200``/``rsi_14`` are recomputed as of that
    bar's date (not the latest-only ``TechnicalFeature`` snapshot), so the
    series shows how indicators evolved rather than repeating one value.
    """
    closes = [Decimal(str(bar.close)) for bar in bars]
    windowed_bars = bars[-SERIES_WINDOW:]
    offset = len(bars) - len(windowed_bars)

    points: List[IndicatorSeriesPoint] = []
    for index, bar in enumerate(windowed_bars):
        prefix = closes[: offset + index + 1]
        points.append(
            IndicatorSeriesPoint(
                bar_date=bar.bar_date,
                close=Decimal(str(bar.close)),
                sma_50=compute_sma(prefix, SMA_SHORT),
                sma_200=compute_sma(prefix, SMA_LONG),
                rsi_14=compute_rsi(prefix),
                volume=bar.volume,
            )
        )
    return points


def _build_coverage(
    *,
    bars: List[PriceBar],
    latest_bar: Optional[PriceBar],
    facts: List[TechnicalFact],
    series: List[IndicatorSeriesPoint],
    as_of_date: date,
) -> TechnicalContextCoverage:
    return TechnicalContextCoverage(
        bar_count=len(bars),
        required_facts_missing=sorted(fact.name for fact in facts if fact.value is None),
        latest_bar_date=latest_bar.bar_date if latest_bar is not None else None,
        bars_stale=_is_stale(latest_bar, as_of_date=as_of_date),
        series_truncated=len(bars) > len(series),
    )


def _is_stale(latest_bar: Optional[PriceBar], *, as_of_date: date) -> bool:
    if latest_bar is None:
        return True
    return (as_of_date - latest_bar.bar_date).days > STALE_PRICE_DATA_THRESHOLD_DAYS


def _snapshot_at(as_of_date: date) -> datetime:
    return datetime.combine(as_of_date, time.max, tzinfo=timezone.utc)


def _sector_value(sector_tag) -> str:
    if isinstance(sector_tag, Enum):
        return str(sector_tag.value)
    return str(sector_tag)


__all__ = [
    "HISTORY_BARS_LOADED",
    "SERIES_WINDOW",
    "STALE_PRICE_DATA_THRESHOLD_DAYS",
    "build_technical_analysis_request",
    "DeterministicTechnicalAnalysis",
]
