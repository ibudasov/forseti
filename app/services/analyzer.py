from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING, Optional, Sequence
from uuid import uuid4

from app.db.models import Decision, PriceBar, Recommendation
from app.db.repository import get_latest_bars, get_security, save_recommendation

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse

logger = logging.getLogger(__name__)

ENGINE_VERSION = "v1.placeholder.0"
DEFAULT_ACCOUNT_SIZE_EUR = Decimal("10000")
DEFAULT_RISK_PCT = Decimal("0.01")
PRICE_QUANT = Decimal("0.0001")
SIZE_QUANT = Decimal("0.01")
MOVE_THRESHOLD = Decimal("0.01")
ENTRY_BAND_PCT = Decimal("0.005")
STOP_LOSS_PCT = Decimal("0.04")
MIN_TICKER_LENGTH = 1
MAX_TICKER_LENGTH = 10
SAFE_TICKER_RE = re.compile(r"^[A-Z0-9.-]+$")
URL_HINTS = ("http://", "https://", "www.", "/", "?", "&", "=", ":")


@dataclass
class AnalysisResult:
    response: "AnalyzeResponse"
    full_payload: dict


def validate_and_normalize_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("Ticker must not be empty.")
    if any(marker in normalized for marker in URL_HINTS):
        raise ValueError("Ticker must be a ticker abbreviation, not a URL or reference.")
    if not (MIN_TICKER_LENGTH <= len(normalized) <= MAX_TICKER_LENGTH):
        raise ValueError("Ticker must be between 1 and 10 ticker-safe characters.")
    if normalized.isalpha() and len(normalized) > 5:
        raise ValueError("Ticker must be a ticker abbreviation, not a company description.")
    if not SAFE_TICKER_RE.fullmatch(normalized):
        raise ValueError("Ticker may contain only A-Z, 0-9, dot, and hyphen.")
    return normalized


def analyze_request(request: "AnalyzeRequest", engine: Optional["Engine"] = None) -> "AnalyzeResponse":
    normalized_ticker = validate_and_normalize_ticker(request.ticker)
    trace_id = str(uuid4())
    created_at = datetime.now(timezone.utc)
    logger.info(
        "analyze_request_received",
        extra={
            "trace_id": trace_id,
            "ticker": normalized_ticker,
            "has_as_of_date": request.as_of_date is not None,
            "has_notes": bool(request.notes),
        },
    )

    warnings: list[str] = []
    try:
        bars = get_latest_bars(normalized_ticker, 2, engine=engine, as_of_date=request.as_of_date)
    except Exception:
        logger.exception(
            "analyze_market_data_load_failed",
            extra={"trace_id": trace_id, "ticker": normalized_ticker},
        )
        bars = []
        warnings.append("market_data_unavailable")

    result = analyze_bars(
        ticker=normalized_ticker,
        bars=bars,
        account_size_eur=request.account_size_eur,
        risk_pct=request.risk_pct,
        max_position_size_eur=request.max_position_size_eur,
        as_of_date=request.as_of_date.isoformat() if request.as_of_date else None,
        notes=request.notes,
        trace_id=trace_id,
        created_at=created_at,
        base_warnings=warnings,
    )

    try:
        security = get_security(normalized_ticker, engine=engine)
        if security is None:
            logger.info(
                "analyze_persistence_skipped",
                extra={"trace_id": trace_id, "ticker": normalized_ticker},
            )
        else:
            recommendation = _to_recommendation(security.id, result.response, result.full_payload)
            save_recommendation(recommendation, engine=engine)
            logger.info(
                "analyze_persisted",
                extra={
                    "trace_id": trace_id,
                    "ticker": normalized_ticker,
                    "decision": result.response.decision,
                },
            )
    except Exception:
        logger.exception(
            "analyze_persistence_failed",
            extra={
                "trace_id": trace_id,
                "ticker": normalized_ticker,
                "decision": result.response.decision,
            },
        )
        if "persistence_failed" not in result.response.warnings:
            result.response.warnings.append("persistence_failed")
        result.full_payload["persistence"] = {"status": "failed"}

    logger.info(
        "analyze_completed",
        extra={
            "trace_id": trace_id,
            "ticker": normalized_ticker,
            "decision": result.response.decision,
        },
    )
    return result.response


def analyze_bars(
    *,
    ticker: str,
    bars: Sequence[PriceBar],
    account_size_eur: Optional[float],
    risk_pct: Optional[float],
    max_position_size_eur: Optional[float],
    as_of_date: Optional[str],
    notes: Optional[str],
    trace_id: str,
    created_at: datetime,
    base_warnings: Optional[list[str]] = None,
) -> AnalysisResult:
    from app.schemas.analyze import AnalyzeResponse

    warnings = list(base_warnings or [])
    sorted_bars = sorted(bars, key=lambda bar: bar.bar_date)
    closes = [Decimal(str(bar.close)) for bar in sorted_bars]
    decision = Decision.watchlist
    confidence = Decimal("0.35")
    reasons: list[str] = []
    entry_range = None
    stop_loss = None
    take_profit = None
    risk_reward = None
    position_size_eur = None
    change_pct = None

    if len(sorted_bars) == 0:
        reasons.append("No recent price bars are available for deterministic analysis.")
        warnings.append("insufficient_price_data")
    elif len(sorted_bars) == 1:
        reasons.append("Only one recent price bar is available, so trend evidence is incomplete.")
        warnings.append("insufficient_price_data")
        confidence = Decimal("0.40")
    else:
        previous_close = closes[-2]
        latest_close = closes[-1]
        if previous_close > 0:
            change_pct = (latest_close - previous_close) / previous_close
        else:
            change_pct = Decimal("0")

        entry_low = _quantize(latest_close * (Decimal("1") - ENTRY_BAND_PCT))
        entry_high = _quantize(latest_close * (Decimal("1") + ENTRY_BAND_PCT))

        if change_pct > MOVE_THRESHOLD:
            decision = Decision.trade
            confidence = Decimal("0.72")
            reasons.extend(
                [
                    "Latest close moved higher than the deterministic momentum threshold.",
                    "Risk parameters were derived from the latest close using the placeholder engine.",
                ]
            )
            entry_range = (entry_low, entry_high)
            stop_loss = _quantize(entry_low * (Decimal("1") - STOP_LOSS_PCT))
            risk_per_share = _quantize(_midpoint(entry_range) - stop_loss)
            if risk_per_share > 0:
                take_profit = (
                    _quantize(_midpoint(entry_range) + (risk_per_share * Decimal("1.5"))),
                    _quantize(_midpoint(entry_range) + (risk_per_share * Decimal("2.0"))),
                )
                risk_reward = _quantize((take_profit[0] - _midpoint(entry_range)) / risk_per_share)
                position_size_eur = _calculate_position_size(
                    entry_mid=_midpoint(entry_range),
                    stop_loss=stop_loss,
                    account_size_eur=account_size_eur,
                    risk_pct=risk_pct,
                    max_position_size_eur=max_position_size_eur,
                )
        elif change_pct < Decimal("0"):
            decision = Decision.no_trade
            confidence = Decimal("0.46")
            reasons.append("Latest close declined versus the previous close, so momentum is unfavorable.")
            warnings.append("negative_price_momentum")
        else:
            confidence = Decimal("0.55")
            reasons.append("Latest close stayed within the neutral deterministic threshold, so the ticker remains on watchlist.")

    response = AnalyzeResponse(
        ticker=ticker,
        decision=decision.value,
        entry_range=_to_float_pair(entry_range),
        stop_loss=_to_float(stop_loss),
        take_profit=_to_float_pair(take_profit),
        risk_reward=_to_float(risk_reward),
        position_size_eur=_to_float(position_size_eur),
        confidence=float(_clamp(confidence, Decimal("0"), Decimal("1"))),
        reasons=reasons,
        warnings=_dedupe_preserve_order(warnings),
        engine_version=ENGINE_VERSION,
        created_at=created_at,
        trace_id=trace_id,
    )
    payload = {
        "trace_id": trace_id,
        "ticker": ticker,
        "engine_version": ENGINE_VERSION,
        "request": {
            "account_size_eur": account_size_eur,
            "risk_pct": risk_pct,
            "max_position_size_eur": max_position_size_eur,
            "as_of_date": as_of_date,
            "notes": notes,
        },
        "bars_considered": [
            {
                "bar_date": bar.bar_date.isoformat(),
                "close": float(Decimal(str(bar.close))),
            }
            for bar in sorted_bars
        ],
        "calculations": {
            "close_change_pct": _to_float(_quantize(change_pct)) if change_pct is not None else None,
            "entry_range": list(response.entry_range) if response.entry_range else None,
            "stop_loss": response.stop_loss,
            "take_profit": list(response.take_profit) if response.take_profit else None,
            "risk_reward": response.risk_reward,
            "position_size_eur": response.position_size_eur,
        },
        "decision": response.decision,
        "warnings": response.warnings,
    }
    return AnalysisResult(response=response, full_payload=payload)


def _to_recommendation(security_id: int, response: "AnalyzeResponse", full_payload: dict) -> Recommendation:
    entry_range = response.entry_range or (None, None)
    take_profit = response.take_profit or (None, None)
    return Recommendation(
        security_id=security_id,
        created_at=response.created_at,
        decision=response.decision,
        entry_low=_to_decimal(entry_range[0]),
        entry_high=_to_decimal(entry_range[1]),
        stop_loss=_to_decimal(response.stop_loss),
        take_profit_1=_to_decimal(take_profit[0]),
        take_profit_2=_to_decimal(take_profit[1]),
        risk_reward=_to_decimal(response.risk_reward),
        position_size=_to_decimal(response.position_size_eur),
        confidence=_to_decimal(response.confidence, quant=Decimal("0.001")),
        reasons=response.reasons,
        warnings=response.warnings,
        full_payload=full_payload,
        engine_version=response.engine_version,
    )


def _calculate_position_size(
    *,
    entry_mid: Decimal,
    stop_loss: Decimal,
    account_size_eur: Optional[float],
    risk_pct: Optional[float],
    max_position_size_eur: Optional[float],
) -> Optional[Decimal]:
    risk_per_share = entry_mid - stop_loss
    if risk_per_share <= 0 or entry_mid <= 0:
        return None

    account_size = Decimal(str(account_size_eur)) if account_size_eur is not None else DEFAULT_ACCOUNT_SIZE_EUR
    risk_fraction = Decimal(str(risk_pct)) if risk_pct is not None else DEFAULT_RISK_PCT
    risk_budget = account_size * risk_fraction
    position_size = _quantize((risk_budget * entry_mid) / risk_per_share, quant=SIZE_QUANT)
    capped_size = min(position_size, _quantize(account_size, quant=SIZE_QUANT))
    if max_position_size_eur is not None:
        capped_size = min(capped_size, _quantize(Decimal(str(max_position_size_eur)), quant=SIZE_QUANT))
    return capped_size


def _midpoint(value_range: tuple[Decimal, Decimal]) -> Decimal:
    return _quantize((value_range[0] + value_range[1]) / Decimal("2"))


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return max(lower, min(value, upper))


def _quantize(value: Optional[Decimal], quant: Decimal = PRICE_QUANT) -> Optional[Decimal]:
    if value is None:
        return None
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _to_decimal(value: Optional[float], quant: Decimal = PRICE_QUANT) -> Optional[Decimal]:
    if value is None:
        return None
    return _quantize(Decimal(str(value)), quant=quant)


def _to_float(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _to_float_pair(value: Optional[tuple[Decimal, Decimal]]) -> Optional[tuple[float, float]]:
    if value is None:
        return None
    return float(value[0]), float(value[1])


def _dedupe_preserve_order(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(items))
