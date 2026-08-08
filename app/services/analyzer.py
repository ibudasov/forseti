from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING, List, Optional
from uuid import uuid4

from app.db.models import Decision, PriceBar, Recommendation
from app.db.repository import (
    get_latest_bars,
    get_latest_fundamental,
    get_latest_macro_daily,
    get_latest_technical_feature,
    get_next_earnings_event,
    get_security,
    save_recommendation,
)
from app.services.checklist import evaluate_checklist
from app.services.risk import RiskConfig, calculate_risk_levels, RiskDowngrade
from app.services.vetoes import check_vetoes
from app.settings import get_settings

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse

logger = logging.getLogger(__name__)

# Frozen constants
ENGINE_VERSION = "v1.rules.0"
MIN_TICKER_LENGTH = 1
MAX_TICKER_LENGTH = 10
SAFE_TICKER_RE = re.compile(r"^[A-Z0-9.-]+$")
URL_HINTS = ("http://", "https://", "www.", "/", "?", "&", "=", ":")

# Decision thresholds
SCORE_TRADE_MIN = 8
SCORE_WATCHLIST_MIN = 5
CONFIDENCE_WARNING_PENALTY = Decimal("0.05")

# Data gate
STALE_PRICE_DATA_THRESHOLD_DAYS = 7
SMA_LONG = 200


def validate_and_normalize_ticker(ticker: str) -> str:
    """Validate and normalize a ticker symbol."""
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
    """Main entry point for analysis request."""
    from fastapi import HTTPException

    normalized_ticker = validate_and_normalize_ticker(request.ticker)
    trace_id = str(uuid4())
    today = datetime.now(timezone.utc).date()

    logger.info(
        "analyze_request_received",
        extra={"trace_id": trace_id, "ticker": normalized_ticker},
    )

    # Check if ticker exists
    security = get_security(normalized_ticker, engine=engine)
    if security is None:
        raise HTTPException(status_code=404, detail=f"ticker_not_found: {normalized_ticker}")

    response = analyze(normalized_ticker, engine=engine, today=today)

    # Persist recommendation
    try:
        recommendation = _to_recommendation(security.id, response, today)
        save_recommendation(recommendation, engine=engine)
        logger.info(
            "recommendation_persisted",
            extra={
                "trace_id": trace_id,
                "ticker": normalized_ticker,
                "decision": response.decision,
            },
        )
    except Exception:
        logger.exception(
            "recommendation_persistence_failed",
            extra={"trace_id": trace_id, "ticker": normalized_ticker},
        )

    # Add trace info
    response.trace_id = trace_id
    logger.info("analyze_completed", extra={"trace_id": trace_id, "ticker": normalized_ticker})
    return response


def analyze(
    symbol: str,
    engine: Optional["Engine"] = None,
    today: Optional[date] = None,
) -> "AnalyzeResponse":
    """
    6-step evaluation pipeline:
    Step 0: Data gate (freshness/completeness)
    Step 1: Hard vetoes
    Step 2: Checklist scoring
    Step 3: Decision thresholds
    Step 4: Risk math (if trade)
    Step 5: Confidence and warnings
    """
    from app.schemas.analyze import AnalyzeResponse

    if today is None:
        today = datetime.now(timezone.utc).date()

    # Step 0: Data gate
    security = get_security(symbol, engine=engine)
    if security is None:
        return AnalyzeResponse(
            ticker=symbol,
            decision="no_trade",
            entry_range=None,
            stop_loss=None,
            take_profit=None,
            risk_reward=None,
            position_size_eur=None,
            confidence=0.0,
            reasons=[],
            warnings=[],
            engine_version=ENGINE_VERSION,
            trace_id="",
        )

    gates = _evaluate_data_gate(symbol, security, today, engine)
    decision = gates["initial_decision"]
    gate_warnings = gates["warnings"]
    bars = gates["bars"]

    if decision != "trade":
        # Already decided by gate
        confidence = _calculate_confidence(0, gate_warnings)
        return AnalyzeResponse(
            ticker=symbol,
            decision=decision,
            entry_range=None,
            stop_loss=None,
            take_profit=None,
            risk_reward=None,
            position_size_eur=None,
            confidence=float(confidence),
            reasons=gates.get("gate_reasons", []),
            warnings=gate_warnings,
            engine_version=ENGINE_VERSION,
            trace_id="",
        )

    # Get all required data for evaluation
    latest_bar = bars[0] if bars else None
    technical_feature = get_latest_technical_feature(symbol, engine=engine)
    fundamental = get_latest_fundamental(symbol, engine=engine)
    vix_row = get_latest_macro_daily(engine=engine)
    next_earnings = get_next_earnings_event(symbol, on_or_after=today, engine=engine)

    vix_close = Decimal(str(vix_row.vix)) if vix_row and vix_row.vix is not None else None

    # Step 1: Hard vetoes
    veto = check_vetoes(
        rsi=technical_feature.rsi_14 if technical_feature else None,
        latest_bar=latest_bar,
        vix_close=vix_close,
        next_earnings=next_earnings,
        sma_200=technical_feature.sma_200 if technical_feature else None,
        today=today,
    )

    if veto:
        # Veto triggers downgrade or stop
        if veto.rule_id == "earnings_too_close":
            decision = "watchlist"
        else:
            decision = "no_trade"
        confidence = _calculate_confidence(0, gate_warnings)
        return AnalyzeResponse(
            ticker=symbol,
            decision=decision,
            entry_range=None,
            stop_loss=None,
            take_profit=None,
            risk_reward=None,
            position_size_eur=None,
            confidence=float(confidence),
            reasons=[f"{veto.rule_id}: {veto.detail}"],
            warnings=gate_warnings,
            engine_version=ENGINE_VERSION,
            trace_id="",
        )

    # Step 2: Checklist scoring
    score, checklist_results = evaluate_checklist(
        latest_bar=latest_bar,
        fundamental=fundamental,
        technical_feature=technical_feature,
        vix_close=vix_close,
    )

    # Step 3: Decision thresholds
    if score >= SCORE_TRADE_MIN:
        decision = "trade"
    elif score >= SCORE_WATCHLIST_MIN:
        decision = "watchlist"
    else:
        decision = "no_trade"

    # Apply gate caps
    if "stale_price_data" in gate_warnings or "no_fundamentals" in gate_warnings:
        if decision == "trade":
            decision = "watchlist"

    # Step 4: Risk math (only for trade)
    risk_levels = None
    risk_downgrade = None
    if decision == "trade" and latest_bar and bars:
        settings = get_settings()
        risk_config = RiskConfig(
            capital_eur=Decimal(str(settings.ACCOUNT_CAPITAL_EUR)),
            risk_per_trade_pct=Decimal(str(settings.RISK_PER_TRADE_PCT)),
        )
        result = calculate_risk_levels(bars, risk_config)
        if isinstance(result, RiskDowngrade):
            risk_downgrade = result
            decision = "watchlist"
        elif result:
            risk_levels = result

    # Build reasons
    reasons = []
    for check in checklist_results:
        reasons.append(f"{check.rule_id}: {check.detail}")
    if risk_downgrade:
        reasons.append(f"{risk_downgrade.reason}: {risk_downgrade.detail}")

    # Step 5: Confidence
    confidence = _calculate_confidence(score, gate_warnings)

    entry_range = None
    stop_loss = None
    take_profit = None
    risk_reward = None
    position_size_eur = None

    if decision == "trade" and risk_levels:
        entry_range = (float(risk_levels.entry_low), float(risk_levels.entry_high))
        stop_loss = float(risk_levels.stop_loss)
        take_profit = (float(risk_levels.take_profit_1), float(risk_levels.take_profit_2))
        risk_reward = float(risk_levels.risk_reward)
        position_size_eur = float(risk_levels.position_size_eur)

    return AnalyzeResponse(
        ticker=symbol,
        decision=decision,
        entry_range=entry_range,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        position_size_eur=position_size_eur,
        confidence=float(confidence),
        reasons=reasons,
        warnings=gate_warnings,
        engine_version=ENGINE_VERSION,
        trace_id="",
    )


def _evaluate_data_gate(
    symbol: str,
    security,
    today: date,
    engine,
) -> dict:
    """
    Step 0: Data gate.
    Returns dict with initial_decision, warnings, bars, and gate_reasons.
    """
    warnings = []
    gate_reasons = []

    if not security.is_active:
        warnings.append("security_inactive")
        return {
            "initial_decision": "no_trade",
            "warnings": warnings,
            "bars": [],
            "gate_reasons": ["security_inactive: security is inactive"],
        }

    # Get latest bars
    bars = list(reversed(get_latest_bars(symbol, 250, engine=engine)))
    if not bars:
        warnings.append("no_price_data")
        return {
            "initial_decision": "no_trade",
            "warnings": warnings,
            "bars": [],
            "gate_reasons": ["no_price_data: no price data available"],
        }

    # Check for insufficient bars for SMA_LONG
    if len(bars) < SMA_LONG:
        warnings.append("insufficient_price_data")
        return {
            "initial_decision": "watchlist",
            "warnings": warnings,
            "bars": bars,
            "gate_reasons": ["insufficient_price_data: fewer than 200 price bars available"],
        }

    latest_bar = bars[-1]
    bars_age_days = (today - latest_bar.bar_date).days

    if bars_age_days > STALE_PRICE_DATA_THRESHOLD_DAYS:
        warnings.append("stale_price_data")

    # Check for technical features
    technical_feature = get_latest_technical_feature(symbol, engine=engine)
    if technical_feature is None:
        warnings.append("no_technical_features")

    # Check for fundamentals
    fundamental = get_latest_fundamental(symbol, engine=engine)
    if fundamental is None:
        warnings.append("no_fundamentals")

    # Check for earnings data
    next_earnings = get_next_earnings_event(symbol, on_or_after=date.min, engine=engine)
    if next_earnings is None:
        warnings.append("no_earnings_data")

    return {
        "initial_decision": "trade",  # will be refined further
        "warnings": warnings,
        "bars": bars,
        "gate_reasons": gate_reasons,
    }


def _calculate_confidence(score: int, warnings: List[str]) -> Decimal:
    """
    Calculate confidence as clamp(score / 11 - 0.05 * len(warnings), 0.0, 1.0).
    """
    confidence = Decimal(score) / Decimal(11) - (CONFIDENCE_WARNING_PENALTY * len(warnings))
    confidence = max(Decimal("0"), min(Decimal("1"), confidence))
    return confidence.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _to_recommendation(security_id: int, response: "AnalyzeResponse", today: date) -> Recommendation:
    """Convert response to Recommendation model for persistence."""
    entry_range = response.entry_range or (None, None)
    take_profit = response.take_profit or (None, None)

    return Recommendation(
        security_id=security_id,
        created_at=datetime.now(timezone.utc),
        decision=response.decision,
        entry_low=Decimal(str(entry_range[0])) if entry_range[0] else None,
        entry_high=Decimal(str(entry_range[1])) if entry_range[1] else None,
        stop_loss=Decimal(str(response.stop_loss)) if response.stop_loss else None,
        take_profit_1=Decimal(str(take_profit[0])) if take_profit[0] else None,
        take_profit_2=Decimal(str(take_profit[1])) if take_profit[1] else None,
        risk_reward=Decimal(str(response.risk_reward)) if response.risk_reward else None,
        position_size=Decimal(str(response.position_size_eur)) if response.position_size_eur else None,
        confidence=Decimal(str(response.confidence)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
        reasons=response.reasons,
        warnings=response.warnings,
        full_payload={
            "trace_id": response.trace_id,
            "ticker": response.ticker,
            "engine_version": ENGINE_VERSION,
            "decision": response.decision,
        },
        engine_version=ENGINE_VERSION,
    )
