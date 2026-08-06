from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from app.db.models import EarningsEvent, Fundamental, PriceBar, TechnicalFeature
from app.db.repository import (
    count_price_bars,
    get_latest_bars,
    get_latest_fundamental,
    get_latest_technical_feature,
    get_next_earnings_event,
    get_security,
)
from app.schemas.ticker import (
    DataFreshness,
    FundamentalsSnapshot,
    PriceBarSnapshot,
    TechnicalFeaturesSnapshot,
    TickerProfileResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

STALE_PRICE_DATA_THRESHOLD_DAYS = 7


def build_ticker_profile(
    symbol: str,
    engine: Optional["Engine"] = None,
    today: Optional[date] = None,
) -> Optional[TickerProfileResponse]:
    if today is None:
        today = datetime.now(timezone.utc).date()

    security = get_security(symbol, engine=engine)
    if security is None:
        return None

    bars = get_latest_bars(symbol, 1, engine=engine)
    latest_bar = bars[0] if bars else None
    bars_stored = count_price_bars(symbol, engine=engine)
    technical_feature = get_latest_technical_feature(symbol, engine=engine)
    fundamental = get_latest_fundamental(symbol, engine=engine)
    next_earnings = get_next_earnings_event(symbol, on_or_after=today, engine=engine)

    warnings = _build_warnings(
        security=security,
        latest_bar=latest_bar,
        bars_stored=bars_stored,
        technical_feature=technical_feature,
        fundamental=fundamental,
        next_earnings=next_earnings,
        symbol=symbol,
        today=today,
        engine=engine,
    )

    profile = TickerProfileResponse(
        ticker=security.ticker,
        name=security.name,
        exchange=security.exchange,
        sector_tag=security.sector_tag,
        currency=security.currency or "USD",
        is_active=security.is_active,
        latest_price_bar=_to_price_bar_snapshot(latest_bar) if latest_bar else None,
        price_bars_stored=bars_stored,
        data_freshness=_to_data_freshness(latest_bar, today),
        latest_technical_features=_to_technical_features_snapshot(technical_feature) if technical_feature else None,
        latest_fundamentals=_to_fundamentals_snapshot(fundamental) if fundamental else None,
        next_earnings_date=next_earnings.report_date if next_earnings else None,
        warnings=warnings,
    )

    logger.info("ticker_profile_served", extra={"ticker": symbol, "warnings": warnings})
    return profile


def _build_warnings(
    security,
    latest_bar,
    bars_stored: int,
    technical_feature,
    fundamental,
    next_earnings,
    symbol: str,
    today: date,
    engine,
) -> list[str]:
    warnings: list[str] = []

    if not security.is_active:
        warnings.append("security_inactive")

    if bars_stored == 0:
        warnings.append("no_price_data")
    elif _is_price_data_stale(latest_bar, today):
        warnings.append("stale_price_data")

    if technical_feature is None:
        warnings.append("no_technical_features")

    if fundamental is None:
        warnings.append("no_fundamentals")

    if _has_no_earnings_data(symbol, next_earnings, today, engine):
        warnings.append("no_earnings_data")

    return warnings


def _is_price_data_stale(latest_bar: PriceBar, today: date) -> bool:
    return (today - latest_bar.bar_date).days > STALE_PRICE_DATA_THRESHOLD_DAYS


def _has_no_earnings_data(symbol: str, next_earnings: Optional[EarningsEvent], today: date, engine) -> bool:
    if next_earnings is not None:
        return False
    any_earnings = get_next_earnings_event(symbol, on_or_after=date.min, engine=engine)
    return any_earnings is None


def _to_float(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _to_price_bar_snapshot(bar: PriceBar) -> PriceBarSnapshot:
    return PriceBarSnapshot(
        bar_date=bar.bar_date,
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=bar.volume,
    )


def _to_technical_features_snapshot(feature: TechnicalFeature) -> TechnicalFeaturesSnapshot:
    return TechnicalFeaturesSnapshot(
        as_of_date=feature.as_of_date,
        rsi_14=_to_float(feature.rsi_14),
        sma_50=_to_float(feature.sma_50),
        sma_200=_to_float(feature.sma_200),
        volume_trend=_to_float(feature.volume_trend),
    )


def _to_fundamentals_snapshot(fundamental: Fundamental) -> FundamentalsSnapshot:
    return FundamentalsSnapshot(
        as_of_date=fundamental.as_of_date,
        revenue_growth=_to_float(fundamental.revenue_growth),
        fcf=_to_float(fundamental.fcf),
        debt_to_equity=_to_float(fundamental.debt_to_equity),
        eps_trend=_to_float(fundamental.eps_trend),
        margins=_to_float(fundamental.margins),
    )


def _to_data_freshness(latest_bar: Optional[PriceBar], today: date) -> DataFreshness:
    if latest_bar is None:
        return DataFreshness(
            latest_price_bar_date=None,
            price_data_age_days=None,
            stale_threshold_days=STALE_PRICE_DATA_THRESHOLD_DAYS,
            is_price_data_stale=True,
        )
    age_days = (today - latest_bar.bar_date).days
    return DataFreshness(
        latest_price_bar_date=latest_bar.bar_date,
        price_data_age_days=age_days,
        stale_threshold_days=STALE_PRICE_DATA_THRESHOLD_DAYS,
        is_price_data_stale=age_days > STALE_PRICE_DATA_THRESHOLD_DAYS,
    )
