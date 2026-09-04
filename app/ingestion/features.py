from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple

from app.db.models import TechnicalFeature
from app.db.repository import (
    get_latest_bars,
    list_active_securities,
    upsert_technical_features,
)

logger = logging.getLogger(__name__)

# Frozen constants for feature computation
RSI_PERIOD = 14
SMA_SHORT = 50
SMA_LONG = 200
VOLUME_WINDOW = 20
FEATURE_PRECISION = Decimal("0.0001")


def compute_rsi(closes: List[Decimal]) -> Optional[Decimal]:
    """
    Compute Wilder's RSI with period 14.
    Requires at least 15 bars (14 for calculation + 1 for initialization).
    """
    if len(closes) < RSI_PERIOD + 1:
        return None

    # Calculate gains and losses
    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(Decimal("0"))
        else:
            gains.append(Decimal("0"))
            losses.append(abs(change))

    # Wilder's smoothing: first average is simple mean
    avg_gain = sum(gains[:RSI_PERIOD]) / RSI_PERIOD
    avg_loss = sum(losses[:RSI_PERIOD]) / RSI_PERIOD

    # Then use Wilder's smoothing for remaining bars
    for i in range(RSI_PERIOD, len(gains)):
        avg_gain = (avg_gain * (RSI_PERIOD - 1) + gains[i]) / RSI_PERIOD
        avg_loss = (avg_loss * (RSI_PERIOD - 1) + losses[i]) / RSI_PERIOD

    # Avoid division by zero
    if avg_loss == 0:
        if avg_gain > 0:
            return Decimal("100")
        else:
            return Decimal("50")

    rs = avg_gain / avg_loss
    rsi = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
    return _quantize_feature(rsi)


def compute_sma(closes: List[Decimal], window: int) -> Optional[Decimal]:
    """
    Compute simple moving average.
    Returns None if fewer bars than required by window.
    """
    if len(closes) < window:
        return None
    return _quantize_feature(sum(closes[-window:]) / window)


def compute_volume_trend(volumes: List[int]) -> Optional[Decimal]:
    """
    Compute volume trend: mean(last 20 volumes) / mean(prior 20 volumes).
    Returns None if < 40 bars or if prior mean is 0.
    """
    if len(volumes) < 40:
        return None

    recent_volumes = [Decimal(v) for v in volumes[-VOLUME_WINDOW:]]
    prior_volumes = [Decimal(v) for v in volumes[-2 * VOLUME_WINDOW:-VOLUME_WINDOW]]

    recent_mean = sum(recent_volumes) / VOLUME_WINDOW
    prior_mean = sum(prior_volumes) / VOLUME_WINDOW

    if prior_mean == 0:
        return None

    trend = recent_mean / prior_mean
    return _quantize_feature(trend)


def _quantize_feature(value: Decimal) -> Decimal:
    """Quantize to 4 decimal places."""
    return value.quantize(FEATURE_PRECISION, rounding=ROUND_HALF_UP)


def compute_technical_features(engine=None) -> Tuple[int, List[str]]:
    """
    Main feature computation loop.
    Returns tuple of (rows_upserted, failed_tickers).
    """
    securities = list_active_securities(engine=engine)
    features_to_upsert = []
    failed_tickers = []

    for security in securities:
        try:
            # Get latest 250 bars in descending order
            bars = get_latest_bars(security.ticker, 250, engine=engine)
            if not bars:
                logger.debug("no_bars_for_feature_computation: ticker=%s", security.ticker)
                continue

            # Reverse to ascending order for computation
            bars = list(reversed(bars))

            # Extract closes and volumes
            closes = [Decimal(str(bar.close)) for bar in bars]
            volumes = [bar.volume for bar in bars]

            # Compute features
            rsi = compute_rsi(closes)
            sma_50 = compute_sma(closes, SMA_SHORT)
            sma_200 = compute_sma(closes, SMA_LONG)
            volume_trend = compute_volume_trend(volumes)

            # Get as_of_date from latest bar
            as_of_date = bars[-1].bar_date

            # Create feature
            feature = TechnicalFeature(
                security_id=security.id,
                as_of_date=as_of_date,
                rsi_14=rsi,
                sma_50=sma_50,
                sma_200=sma_200,
                volume_trend=volume_trend,
            )
            features_to_upsert.append(feature)

            logger.debug(
                "technical_feature_computed: ticker=%s rsi=%s sma50=%s sma200=%s vol_trend=%s",
                security.ticker,
                rsi,
                sma_50,
                sma_200,
                volume_trend,
            )

        except Exception:
            logger.exception("feature_computation_failed: ticker=%s", security.ticker)
            failed_tickers.append(security.ticker)

    # Batch upsert all features
    if features_to_upsert:
        upsert_technical_features(features_to_upsert, engine=engine)

    return len(features_to_upsert), failed_tickers
