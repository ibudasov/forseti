#!/usr/bin/env python3
"""Thin entrypoint for `make scorecard`.

Seeds the frozen fixture universe into a disposable database, runs the
existing screening pipeline against a frozen `today`, builds a `Scorecard`,
prints it, and (optionally) compares it against a committed baseline.

All business logic lives in `app.services.scorecard` and
`app.services.screening`; this script only wires fixture I/O to those
services and formats the process exit code.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

import os

from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

from app.db.models import (
    EarningsEvent,
    Fundamental,
    MacroDaily,
    PriceBar,
    Security,
    TechnicalFeature,
)
from app.db.session import get_session
from app.services.scorecard import Scorecard, build_scorecard, compare
from app.services.screening import run_screening

DEFAULT_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "scorecard" / "universe.json"


def _default_database_url() -> str:
    user = os.environ.get("POSTGRES_USER", "user")
    password = os.environ.get("POSTGRES_PASSWORD", "password")
    return f"postgresql://{user}:{password}@postgresql:5432/forseti_scorecard_test"


DISPOSABLE_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_]+_test$")


def _assert_database_is_disposable(database_url: str) -> None:
    """Refuse to run against anything that isn't an obviously-scratch database.

    Mirrors the guard in `tests/conftest.py`: the scorecard drops and
    recreates every table on each run, so it must never point at the
    application database.
    """
    database_name = database_url.rsplit("/", 1)[-1]
    if not DISPOSABLE_DATABASE_NAME.match(database_name):
        raise RuntimeError(
            f"Refusing to seed database '{database_name}': the scorecard database URL "
            "must point at a database named '<name>_test'."
        )


def _create_database_if_missing(database_url: str) -> None:
    database_name = database_url.rsplit("/", 1)[-1]
    maintenance_url = database_url.rsplit("/", 1)[0] + "/postgres"
    maintenance_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT", future=True)
    try:
        with maintenance_engine.connect() as connection:
            already_exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database_name}
            ).scalar()
            if not already_exists:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        maintenance_engine.dispose()


def _build_engine(database_url: str):
    _assert_database_is_disposable(database_url)
    _create_database_if_missing(database_url)
    engine = create_engine(database_url, echo=False, future=True)
    with engine.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.commit()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    return engine


def load_fixture(fixture_path: Path) -> dict:
    return json.loads(fixture_path.read_text())


def seed_fixture(engine, fixture: dict) -> None:
    """Insert every security, price bar, and derived-data row from the fixture."""
    with get_session(engine) as session:
        for security_payload in fixture["securities"]:
            security = Security(
                ticker=security_payload["ticker"],
                name=security_payload["name"],
                exchange=security_payload["exchange"],
                sector_tag=security_payload["sector_tag"],
            )
            session.add(security)
            session.commit()
            session.refresh(security)

            for bar in security_payload["price_bars"]:
                session.add(
                    PriceBar(
                        security_id=security.id,
                        bar_date=date.fromisoformat(bar["bar_date"]),
                        open=Decimal(bar["open"]),
                        high=Decimal(bar["high"]),
                        low=Decimal(bar["low"]),
                        close=Decimal(bar["close"]),
                        volume=bar["volume"],
                    )
                )

            technical_feature = security_payload.get("technical_feature")
            if technical_feature is not None:
                session.add(
                    TechnicalFeature(
                        security_id=security.id,
                        as_of_date=date.fromisoformat(technical_feature["as_of_date"]),
                        rsi_14=Decimal(technical_feature["rsi_14"]),
                        sma_50=Decimal(technical_feature["sma_50"]),
                        sma_200=Decimal(technical_feature["sma_200"]),
                        volume_trend=Decimal(technical_feature["volume_trend"]),
                    )
                )

            fundamental = security_payload.get("fundamental")
            if fundamental is not None:
                session.add(
                    Fundamental(
                        security_id=security.id,
                        as_of_date=date.fromisoformat(fundamental["as_of_date"]),
                        revenue_growth=Decimal(fundamental["revenue_growth"]),
                        fcf=Decimal(fundamental["fcf"]),
                        debt_to_equity=Decimal(fundamental["debt_to_equity"]),
                        eps_trend=Decimal(fundamental["eps_trend"]),
                        margins=Decimal(fundamental["margins"]),
                        raw_payload=fundamental["raw_payload"],
                    )
                )

            earnings_event = security_payload.get("earnings_event")
            if earnings_event is not None:
                session.add(
                    EarningsEvent(
                        security_id=security.id,
                        report_date=date.fromisoformat(earnings_event["report_date"]),
                        confirmed=earnings_event["confirmed"],
                    )
                )

            session.commit()

        for macro_row in fixture.get("macro_daily", []):
            session.add(MacroDaily(obs_date=date.fromisoformat(macro_row["obs_date"]), vix=Decimal(macro_row["vix"])))
        session.commit()


def load_baseline(baseline_path: Path) -> Scorecard:
    payload = json.loads(baseline_path.read_text())
    return Scorecard(
        engine_version=payload["engine_version"],
        universe_size=payload["universe_size"],
        analyzed_count=payload["analyzed_count"],
        failed_count=payload["failed_count"],
        trade_count=payload["trade_count"],
        watchlist_count=payload["watchlist_count"],
        no_trade_count=payload["no_trade_count"],
        zero_confidence_count=payload["zero_confidence_count"],
        warning_counts=dict(payload["warning_counts"]),
    )


def compute_scorecard(fixture_path: Path, database_url: str) -> Scorecard:
    fixture = load_fixture(fixture_path)
    engine = _build_engine(database_url)
    seed_fixture(engine, fixture)
    today = date.fromisoformat(fixture["today"])
    response = run_screening(engine=engine, today=today)
    return build_scorecard(response)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--database-url", default=_default_database_url())
    parser.add_argument("--json", action="store_true", help="Print the scorecard as JSON.")
    parser.add_argument("--markdown", action="store_true", help="Print the scorecard as a Markdown table.")
    parser.add_argument("--baseline", type=Path, default=None, help="Compare against this committed baseline.")
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 1 if any LOWER/HIGHER-IS-BETTER metric regressed against --baseline.",
    )
    parser.add_argument(
        "--write-baseline",
        type=Path,
        default=None,
        help="Write the current scorecard's JSON to this path instead of comparing.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    scorecard = compute_scorecard(args.fixture, args.database_url)

    if args.write_baseline is not None:
        args.write_baseline.write_text(scorecard.as_json())
        print(f"Wrote baseline to {args.write_baseline}")
        return 0

    if args.json:
        print(scorecard.as_json())
    if args.markdown:
        print(scorecard.as_markdown())

    if args.baseline is None:
        return 0

    baseline = load_baseline(args.baseline)
    delta = compare(baseline, scorecard)

    if delta.has_regressions:
        print("Scorecard regressions detected:", file=sys.stderr)
        print(delta.regression_report(), file=sys.stderr)
        if args.fail_on_regression:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
