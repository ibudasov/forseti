"""Load and seed hand-checkable golden analysis fixtures."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlmodel import Session

from app.db.models import EarningsEvent, Fundamental, MacroDaily, PriceBar, Security, TechnicalFeature
from app.db.repository import (
    upsert_earnings_event,
    upsert_fundamental,
    upsert_macro_daily_rows,
    upsert_price_bars,
    upsert_technical_feature,
)
from app.schemas.analyze import AnalyzeResponse

from .price_series import build_trade_ready_series, build_uptrend_series

_FIXTURE_DIR = Path(__file__).resolve().parent
_DETERMINISTIC_FIELDS = (
    "decision",
    "entry_range",
    "stop_loss",
    "take_profit",
    "risk_reward",
    "position_size_eur",
    "confidence",
    "engine_version",
    "warnings",
    "reasons",
)


@dataclass(frozen=True)
class GoldenCase:
    name: str
    ticker: str
    today: date
    inputs: dict[str, Any]
    expected: dict[str, Any]
    cassette_path: Path | None

    def seed(self, session: Session) -> None:
        security = Security(**self.inputs["security"])
        session.add(security)
        session.commit()
        session.refresh(security)

        macro_daily = self.inputs.get("macro_daily")
        if macro_daily is not None:
            upsert_macro_daily_rows(
                [
                    MacroDaily(
                        obs_date=date.fromisoformat(macro_daily["obs_date"]),
                        vix=_decimal_or_none(macro_daily["vix"]),
                    )
                ],
                engine=session.bind,
            )

        upsert_price_bars(
            [
                PriceBar(
                    security_id=security.id,
                    bar_date=date.fromisoformat(bar["bar_date"]),
                    open=Decimal(str(bar["open"])),
                    high=Decimal(str(bar["high"])),
                    low=Decimal(str(bar["low"])),
                    close=Decimal(str(bar["close"])),
                    volume=int(bar["volume"]),
                )
                for bar in _build_price_bars(self.inputs["price_bar"])
            ],
            engine=session.bind,
        )

        technical_feature = self.inputs.get("technical_feature")
        if technical_feature is not None:
            upsert_technical_feature(
                TechnicalFeature(
                    security_id=security.id,
                    as_of_date=date.fromisoformat(technical_feature["as_of_date"]),
                    rsi_14=_decimal_or_none(technical_feature.get("rsi_14")),
                    sma_50=_decimal_or_none(technical_feature.get("sma_50")),
                    sma_200=_decimal_or_none(technical_feature.get("sma_200")),
                    volume_trend=_decimal_or_none(technical_feature.get("volume_trend")),
                ),
                engine=session.bind,
            )

        fundamental = self.inputs.get("fundamental")
        if fundamental is not None:
            upsert_fundamental(
                Fundamental(
                    security_id=security.id,
                    as_of_date=date.fromisoformat(fundamental["as_of_date"]),
                    revenue_growth=_decimal_or_none(fundamental.get("revenue_growth")),
                    fcf=_decimal_or_none(fundamental.get("fcf")),
                    debt_to_equity=_decimal_or_none(fundamental.get("debt_to_equity")),
                    eps_trend=_decimal_or_none(fundamental.get("eps_trend")),
                    margins=_decimal_or_none(fundamental.get("margins")),
                    raw_payload={"source": "golden_case", "case": self.name},
                ),
                engine=session.bind,
            )

        earnings_event = self.inputs.get("earnings_event")
        if earnings_event is not None:
            upsert_earnings_event(
                EarningsEvent(
                    security_id=security.id,
                    report_date=date.fromisoformat(earnings_event["report_date"]),
                    confirmed=bool(earnings_event["confirmed"]),
                ),
                engine=session.bind,
            )

    def assert_matches(self, response: AnalyzeResponse) -> None:
        actual = deterministic_fields(response)
        expected = _normalized_expected(self.expected)
        mismatches = []
        for field_name in _DETERMINISTIC_FIELDS:
            if actual[field_name] != expected[field_name]:
                mismatches.append(
                    f"{field_name}: expected {expected[field_name]!r}, got {actual[field_name]!r}"
                )
        if mismatches:
            raise AssertionError(f"Golden case '{self.name}' diverged:\n" + "\n".join(mismatches))


def all_golden_case_names() -> list[str]:
    return sorted(path.name for path in _FIXTURE_DIR.iterdir() if (path / "case.json").is_file())


def load_golden_case(name: str) -> GoldenCase:
    case_dir = _FIXTURE_DIR / name
    case_payload = _load_json(case_dir / "case.json")
    inputs = _load_json(case_dir / "inputs.json")
    expected = _load_json(case_dir / "expected.json")
    cassette_dir = case_dir / "cassette"
    cassette_path = cassette_dir if cassette_dir.is_dir() else None
    return GoldenCase(
        name=name,
        ticker=str(case_payload["ticker"]),
        today=date.fromisoformat(case_payload["today"]),
        inputs=inputs,
        expected=expected,
        cassette_path=cassette_path,
    )


def deterministic_fields(response: AnalyzeResponse) -> dict[str, Any]:
    return {
        "decision": response.decision,
        "entry_range": list(response.entry_range) if response.entry_range is not None else None,
        "stop_loss": response.stop_loss,
        "take_profit": list(response.take_profit) if response.take_profit is not None else None,
        "risk_reward": response.risk_reward,
        "position_size_eur": response.position_size_eur,
        "confidence": response.confidence,
        "engine_version": response.engine_version,
        "warnings": sorted(set(response.warnings)),
        "reasons": list(response.reasons),
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _build_price_bars(payload: dict[str, Any]) -> list[dict[str, object]]:
    series_name = payload["series"]
    series_builders = {
        "trade_ready": build_trade_ready_series,
        "uptrend": build_uptrend_series,
    }
    builder = series_builders[series_name]
    builder_kwargs = {
        key: value
        for key, value in payload.items()
        if key not in {"series", "end_date"}
    }
    return builder(
        end_date=date.fromisoformat(payload["end_date"]),
        **builder_kwargs,
    )


def _normalized_expected(expected: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(expected)
    normalized["warnings"] = sorted(set(normalized.get("warnings") or []))
    normalized["reasons"] = list(normalized.get("reasons") or [])
    return normalized
