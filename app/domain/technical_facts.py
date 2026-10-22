"""Pure, citable derived technical facts.

Everything a Technical Analyst might reason about beyond the four stored
``TechnicalFeature`` columns (``rsi_14``, ``sma_50``, ``sma_200``,
``volume_trend``) has to be computed deterministically here and given a
stable ``metric_id`` so the analyst can cite it. See
``docs/technical-analysis-context.md`` for the full field/formula reference.

Every calculator below is a pure function over plain ``Decimal``/``int``
sequences (ascending, oldest first) plus scalars - no DB access, no network,
no clock reads. ``build_technical_facts`` wraps the calculators into
``TechnicalFact`` rows for a given ``as_of_date``.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional, Sequence

from app.db.models import PriceBar
from app.ingestion.features import RSI_PERIOD, SMA_LONG, SMA_SHORT, compute_rsi, compute_sma
from app.schemas.technicals import TechnicalFact
from app.services.risk import SWING_HIGH_LOOKBACK, SWING_LOW_LOOKBACK, calculate_atr

FACT_PRECISION = Decimal("0.0001")

SMA50_SLOPE_LOOKBACK_DAYS = 10
SMA200_SLOPE_LOOKBACK_DAYS = 20
RSI_CHANGE_LOOKBACK_DAYS = 5
DIVERGENCE_LOOKBACK_DAYS = 20
RETURN_SHORT_LOOKBACK_DAYS = 20
RETURN_LONG_LOOKBACK_DAYS = 60
UP_VOLUME_LOOKBACK_DAYS = 20
HIGH_LOW_52W_LOOKBACK_DAYS = 252

_TRUE: Decimal = Decimal("1")
_FALSE: Decimal = Decimal("0")


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(FACT_PRECISION, rounding=ROUND_HALF_UP)


def _bool_to_decimal(value: Optional[bool]) -> Optional[Decimal]:
    if value is None:
        return None
    return _TRUE if value else _FALSE


def calculate_dist_pct(value: Decimal, reference: Optional[Decimal]) -> Optional[Decimal]:
    """``(value - reference) / reference``. ``None`` when the reference is missing or zero."""
    if reference is None or reference == 0:
        return None
    return _quantize((value - reference) / reference)


def calculate_sma_slope_pct(
    closes: Sequence[Decimal],
    *,
    window: int,
    lookback_days: int,
) -> Optional[Decimal]:
    """``(sma_t - sma_{t-lookback_days}) / sma_{t-lookback_days}``."""
    if len(closes) <= lookback_days:
        return None
    sma_now = compute_sma(list(closes), window)
    sma_then = compute_sma(list(closes[:-lookback_days]), window)
    if sma_now is None or sma_then is None or sma_then == 0:
        return None
    return _quantize((sma_now - sma_then) / sma_then)


def calculate_sma_above(sma_short: Optional[Decimal], sma_long: Optional[Decimal]) -> Optional[bool]:
    if sma_short is None or sma_long is None:
        return None
    return sma_short > sma_long


def calculate_days_since_sma_cross(
    closes: Sequence[Decimal],
    *,
    short_window: int,
    long_window: int,
) -> Optional[int]:
    """Bars since the last SMA-short/SMA-long sign change, scanning all loaded bars.

    ``None`` when there is not enough history to compute both SMAs, or when no
    sign change is found anywhere in the available history.
    """
    closes = list(closes)
    diffs: List[Optional[Decimal]] = []
    for i in range(len(closes)):
        prefix = closes[: i + 1]
        sma_short = compute_sma(prefix, short_window)
        sma_long = compute_sma(prefix, long_window)
        if sma_short is None or sma_long is None:
            diffs.append(None)
        else:
            diffs.append(sma_short - sma_long)

    last_index = len(diffs) - 1
    if last_index < 0:
        return None
    last_diff = diffs[last_index]
    if last_diff is None or last_diff == 0:
        return None
    last_sign = last_diff > 0

    for offset in range(1, last_index + 1):
        diff = diffs[last_index - offset]
        if diff is None:
            return None
        if diff == 0:
            continue
        if (diff > 0) != last_sign:
            return offset
    return None


def calculate_rsi_change(
    closes: Sequence[Decimal],
    *,
    period: int,
    lookback_days: int,
) -> Optional[Decimal]:
    closes = list(closes)
    if len(closes) <= lookback_days:
        return None
    rsi_now = compute_rsi(closes)
    rsi_then = compute_rsi(closes[:-lookback_days])
    if rsi_now is None or rsi_then is None:
        return None
    return _quantize(rsi_now - rsi_then)


def calculate_rsi_price_divergence(
    closes: Sequence[Decimal],
    *,
    period: int,
    lookback_days: int,
) -> Optional[bool]:
    """``True`` when price and RSI disagree on making a new ``lookback_days``-bar high.

    Price makes a new high when today's close equals the maximum close of the
    lookback window; RSI makes a new high the same way. Divergence is true
    when exactly one of the two makes a new high. Returns ``None`` when there
    are fewer than ``lookback_days`` bars, or RSI cannot be computed for every
    bar in the window.
    """
    closes = list(closes)
    if len(closes) < lookback_days:
        return None
    window_closes = closes[-lookback_days:]
    rsi_window = [compute_rsi(closes[: len(closes) - lookback_days + i + 1]) for i in range(lookback_days)]
    if any(value is None for value in rsi_window):
        return None
    known_rsi_window: List[Decimal] = [value for value in rsi_window if value is not None]

    price_new_high = window_closes[-1] == max(window_closes)
    rsi_new_high = known_rsi_window[-1] == max(known_rsi_window)
    return price_new_high != rsi_new_high


def calculate_atr_pct(atr: Optional[Decimal], close: Decimal) -> Optional[Decimal]:
    if atr is None or close == 0:
        return None
    return _quantize(atr / close)


def calculate_extreme(values: Sequence[Decimal], *, lookback_days: int, pick_max: bool) -> Optional[Decimal]:
    if not values:
        return None
    window = list(values)[-lookback_days:]
    return max(window) if pick_max else min(window)


def calculate_up_volume_ratio(
    closes: Sequence[Decimal],
    volumes: Sequence[int],
    *,
    lookback_days: int,
) -> Optional[Decimal]:
    """Sum of volume on up days / total volume, over the last ``lookback_days`` bars.

    An "up day" is a bar whose close is higher than the prior bar's close, so
    one extra bar of history (``lookback_days + 1`` closes) is required to
    classify the oldest bar in the window.
    """
    closes = list(closes)
    volumes = list(volumes)
    if len(closes) < lookback_days + 1 or len(volumes) < lookback_days + 1:
        return None

    window_closes = closes[-(lookback_days + 1):]
    window_volumes = volumes[-lookback_days:]
    total_volume = sum(Decimal(volume) for volume in window_volumes)
    if total_volume == 0:
        return None

    up_volume = Decimal("0")
    for i in range(1, lookback_days + 1):
        if window_closes[i] > window_closes[i - 1]:
            up_volume += Decimal(window_volumes[i - 1])
    return _quantize(up_volume / total_volume)


def calculate_return_pct(closes: Sequence[Decimal], *, lookback_days: int) -> Optional[Decimal]:
    closes = list(closes)
    if len(closes) <= lookback_days:
        return None
    start = closes[-(lookback_days + 1)]
    if start == 0:
        return None
    return _quantize((closes[-1] - start) / start)


def calculate_days_to_earnings(as_of_date: date, next_earnings_date: Optional[date]) -> Optional[int]:
    if next_earnings_date is None:
        return None
    return (next_earnings_date - as_of_date).days


def build_technical_facts(
    bars: Sequence[PriceBar],
    *,
    next_earnings_date: Optional[date],
    as_of_date: date,
) -> List[TechnicalFact]:
    """Build every citable derived fact for a single technical context snapshot.

    ``bars`` must be ascending (oldest first) and already filtered to
    ``as_of_date`` by the caller.

    ``close``, ``sma_50``, ``sma_200``, ``rsi_14``, ``volume_trend`` and
    ``vix_close`` are *not* produced here: they are already cited by the
    TA-1 baseline (``DeterministicTechnicalAnalysis.metrics``) under their
    own ``metric_id``, and the caller merges those in directly so every
    baseline citation is reused verbatim instead of being recomputed under
    a second, possibly differently-dated, ``metric_id``.
    """
    bars = list(bars)
    closes = [Decimal(str(bar.close)) for bar in bars]
    highs = [Decimal(str(bar.high)) for bar in bars]
    lows = [Decimal(str(bar.low)) for bar in bars]
    volumes = [bar.volume for bar in bars]

    close = closes[-1] if closes else None
    sma_50 = compute_sma(closes, SMA_SHORT) if closes else None
    sma_200 = compute_sma(closes, SMA_LONG) if closes else None
    atr_14 = calculate_atr(list(bars)) if bars else None
    swing_low_20 = calculate_extreme(lows, lookback_days=SWING_LOW_LOOKBACK, pick_max=False)
    swing_high_60 = calculate_extreme(highs, lookback_days=SWING_HIGH_LOOKBACK, pick_max=True)
    high_52w = calculate_extreme(highs, lookback_days=HIGH_LOW_52W_LOOKBACK_DAYS, pick_max=True)
    low_52w = calculate_extreme(lows, lookback_days=HIGH_LOW_52W_LOOKBACK_DAYS, pick_max=False)

    values_by_name: dict[str, tuple[Optional[Decimal], str, str]] = {
        "dist_sma50_pct": (
            calculate_dist_pct(close, sma_50) if close is not None else None,
            "pct",
            "(close - sma_50) / sma_50",
        ),
        "dist_sma200_pct": (
            calculate_dist_pct(close, sma_200) if close is not None else None,
            "pct",
            "(close - sma_200) / sma_200",
        ),
        "sma50_slope_10d_pct": (
            calculate_sma_slope_pct(closes, window=SMA_SHORT, lookback_days=SMA50_SLOPE_LOOKBACK_DAYS),
            "pct",
            "(sma_50_t - sma_50_t-10) / sma_50_t-10",
        ),
        "sma200_slope_20d_pct": (
            calculate_sma_slope_pct(closes, window=SMA_LONG, lookback_days=SMA200_SLOPE_LOOKBACK_DAYS),
            "pct",
            "(sma_200_t - sma_200_t-20) / sma_200_t-20",
        ),
        "sma50_above_sma200": (
            _bool_to_decimal(calculate_sma_above(sma_50, sma_200)),
            "bool",
            "sma_50 > sma_200",
        ),
        "days_since_sma_cross": (
            _int_to_decimal(
                calculate_days_since_sma_cross(closes, short_window=SMA_SHORT, long_window=SMA_LONG)
            ),
            "days",
            "bars since the last sign change of (sma_50 - sma_200)",
        ),
        "rsi_14_5d_change": (
            calculate_rsi_change(closes, period=RSI_PERIOD, lookback_days=RSI_CHANGE_LOOKBACK_DAYS),
            "rsi_points",
            "rsi_14_t - rsi_14_t-5",
        ),
        "rsi_price_divergence_20d": (
            _bool_to_decimal(
                calculate_rsi_price_divergence(
                    closes, period=RSI_PERIOD, lookback_days=DIVERGENCE_LOOKBACK_DAYS
                )
            ),
            "bool",
            "price makes a 20-bar high XOR rsi_14 makes a 20-bar high",
        ),
        "atr_14": (atr_14, "price", "mean true range over the last 14 bars"),
        "atr_14_pct": (
            calculate_atr_pct(atr_14, close) if close is not None else None,
            "pct",
            "atr_14 / close",
        ),
        "swing_low_20": (swing_low_20, "price", "min(low) over the last 20 bars"),
        "swing_high_60": (swing_high_60, "price", "max(high) over the last 60 bars"),
        "dist_swing_low_20_pct": (
            calculate_dist_pct(close, swing_low_20) if close is not None else None,
            "pct",
            "(close - swing_low_20) / swing_low_20",
        ),
        "dist_swing_high_60_pct": (
            calculate_dist_pct(close, swing_high_60) if close is not None else None,
            "pct",
            "(close - swing_high_60) / swing_high_60",
        ),
        "high_52w": (high_52w, "price", "max(high) over the last 252 bars (or fewer if unavailable)"),
        "low_52w": (low_52w, "price", "min(low) over the last 252 bars (or fewer if unavailable)"),
        "dist_high_52w_pct": (
            calculate_dist_pct(close, high_52w) if close is not None else None,
            "pct",
            "(close - high_52w) / high_52w",
        ),
        "up_volume_ratio_20d": (
            calculate_up_volume_ratio(closes, volumes, lookback_days=UP_VOLUME_LOOKBACK_DAYS),
            "ratio",
            "sum(volume on up days) / sum(volume) over the last 20 bars",
        ),
        "return_20d_pct": (
            calculate_return_pct(closes, lookback_days=RETURN_SHORT_LOOKBACK_DAYS),
            "pct",
            "(close_t - close_t-20) / close_t-20",
        ),
        "return_60d_pct": (
            calculate_return_pct(closes, lookback_days=RETURN_LONG_LOOKBACK_DAYS),
            "pct",
            "(close_t - close_t-60) / close_t-60",
        ),
        "days_to_earnings": (
            _int_to_decimal(calculate_days_to_earnings(as_of_date, next_earnings_date)),
            "days",
            "calendar days from as_of_date to the next earnings report date",
        ),
    }

    return [
        TechnicalFact(
            metric_id=f"{name}:{as_of_date.isoformat()}",
            name=name,
            value=value,
            unit=unit,
            derivation=derivation,
        )
        for name, (value, unit, derivation) in values_by_name.items()
    ]


def _int_to_decimal(value: Optional[int]) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(value)
