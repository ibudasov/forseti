#!/usr/bin/env python
"""`make scorecard` entrypoint: seed the frozen fixture universe, run the
deterministic screening engine against it, and print the product scorecard.

Thin entrypoint (codestyle §35): all metric logic lives in
`app/services/scorecard.py`. This script only wires I/O — reading the
fixture, seeding the database, calling `run_screening`, and printing/
comparing the result. No network access, no API keys: everything it touches
is either the fixture JSON or the test database.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

from app.db.models import EarningsEvent, Fundamental, MacroDaily, PriceBar, Security, TechnicalFeature
from app.db.repository import (
    upsert_earnings_event,
    upsert_fundamental,
    upsert_macro_daily_rows,
    upsert_price_bars,
    upsert_technical_feature,
)
from app.services.scorecard import Scorecard, build_scorecard, compare
from app.services.screening import run_screening

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "scorecard" / "universe.json"

# The scorecard drops and recreates every table, exactly like the test
# fixtures in tests/conftest.py, so it must never be pointed at a database
# that isn't explicitly a disposable test database.
_DISPOSABLE_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_]+_test$")


def _database_name(database_url: str) -> str:
    return database_url.rsplit("/", 1)[-1]


def _prepare_engine(database_url: str):
    database_name = _database_name(database_url)
    if not _DISPOSABLE_DATABASE_NAME.match(database_name):
        raise RuntimeError(
            f"Refusing to seed database '{database_name}': the scorecard requires a "
            "disposable database named '<name>_test' (see TEST_DATABASE_URL)."
        )
    engine = create_engine(database_url, echo=False, future=True)
    with engine.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.commit()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    return engine


def _decimal_or_none(value) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value))


def _seed_security(engine, payload: dict) -> None:
    security = Security(
        ticker=payload["ticker"],
        name=payload["name"],
        exchange=payload["exchange"],
        sector_tag=payload["sector_tag"],
    )
    with Session(engine) as session:
        session.add(security)
        session.commit()
        session.refresh(security)
        security_id = security.id

    bars = [
        PriceBar(
            security_id=security_id,
            bar_date=date.fromisoformat(bar["bar_date"]),
            open=Decimal(str(bar["open"])),
            high=Decimal(str(bar["high"])),
            low=Decimal(str(bar["low"])),
            close=Decimal(str(bar["close"])),
            volume=bar["volume"],
        )
        for bar in payload["price_bars"]
    ]
    upsert_price_bars(bars, engine=engine)

    technical_feature = payload.get("technical_feature")
    if technical_feature is not None:
        upsert_technical_feature(
            TechnicalFeature(
                security_id=security_id,
                as_of_date=date.fromisoformat(technical_feature["as_of_date"]),
                rsi_14=_decimal_or_none(technical_feature["rsi_14"]),
                sma_50=_decimal_or_none(technical_feature["sma_50"]),
                sma_200=_decimal_or_none(technical_feature["sma_200"]),
                volume_trend=_decimal_or_none(technical_feature["volume_trend"]),
            ),
            engine=engine,
        )

    fundamental = payload.get("fundamental")
    if fundamental is not None:
        upsert_fundamental(
            Fundamental(
                security_id=security_id,
                as_of_date=date.fromisoformat(fundamental["as_of_date"]),
                revenue_growth=_decimal_or_none(fundamental["revenue_growth"]),
                fcf=_decimal_or_none(fundamental["fcf"]),
                debt_to_equity=_decimal_or_none(fundamental["debt_to_equity"]),
                eps_trend=_decimal_or_none(fundamental["eps_trend"]),
                margins=_decimal_or_none(fundamental["margins"]),
                raw_payload={},
            ),
            engine=engine,
        )

    earnings_event = payload.get("earnings_event")
    if earnings_event is not None:
        upsert_earnings_event(
            EarningsEvent(
                security_id=security_id,
                report_date=date.fromisoformat(earnings_event["report_date"]),
                confirmed=earnings_event["confirmed"],
            ),
            engine=engine,
        )


def seed_fixture(engine, fixture: dict) -> date:
    """Seed every security in the fixture. Returns the frozen `today` date."""
    macro_daily = fixture.get("macro_daily")
    if macro_daily is not None:
        upsert_macro_daily_rows(
            [MacroDaily(obs_date=date.fromisoformat(macro_daily["obs_date"]), vix=_decimal_or_none(macro_daily["vix"]))],
            engine=engine,
        )

    for security_payload in fixture["securities"]:
        _seed_security(engine, security_payload)

    return date.fromisoformat(fixture["today"])


def _scorecard_from_json(path: Path) -> Scorecard:
    payload = json.loads(path.read_text())
    return Scorecard(
        engine_version=payload["engine_version"],
        universe_size=payload["universe_size"],
        analyzed_count=payload["analyzed_count"],
        failed_count=payload["failed_count"],
        trade_count=payload["trade_count"],
        watchlist_count=payload["watchlist_count"],
        no_trade_count=payload["no_trade_count"],
        zero_confidence_count=payload["zero_confidence_count"],
        warning_counts=payload["warning_counts"],
    )


def _require_test_database_url() -> str:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TEST_DATABASE_URL must be set to a disposable '<name>_test' database.")
    return database_url


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="Path to the fixture universe JSON.")
    parser.add_argument("--json", action="store_true", help="Print the scorecard as JSON.")
    parser.add_argument("--markdown", action="store_true", help="Print the scorecard as Markdown.")
    parser.add_argument("--baseline", type=Path, default=None, help="Path to a committed baseline scorecard JSON.")
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 1 if any metric regressed relative to --baseline.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    fixture = json.loads(args.fixture.read_text())
    database_url = _require_test_database_url()
    engine = _prepare_engine(database_url)
    today = seed_fixture(engine, fixture)

    response = run_screening(engine=engine, today=today)
    current = build_scorecard(response)

    if args.json:
        print(current.as_json(), end="")
    if args.markdown or not args.json:
        print(current.as_markdown(), end="")

    if args.baseline is None:
        return 0

    baseline = _scorecard_from_json(args.baseline)
    delta = compare(baseline, current)
    print(delta.as_markdown(), end="")

    if args.fail_on_regression and delta.has_regressions:
        for regression in delta.regressions:
            print(f"REGRESSION: {regression.failure_message()}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
