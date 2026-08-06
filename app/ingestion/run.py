from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Callable

from app.ingestion.earnings import ingest_earnings
from app.ingestion.fundamentals import ingest_fundamentals
from app.ingestion.prices import ingest_prices
from app.ingestion.universe import seed_universe
from app.ingestion.vix import ingest_vix

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Forseti structured data ingestion")
    parser.add_argument(
        "--source",
        choices=["all", "prices", "vix", "fundamentals", "earnings"],
        default="all",
        help="Select which source to ingest",
    )
    parser.add_argument("--ticker", default=None, help="Optional ticker filter for per-security sources")
    return parser


def _run_prices(ticker: str | None) -> tuple[int, list[str]]:
    return ingest_prices(ticker=ticker)


def _run_vix(_: str | None) -> tuple[int, list[str]]:
    return ingest_vix(), []


def _run_fundamentals(ticker: str | None) -> tuple[int, list[str]]:
    return ingest_fundamentals(ticker=ticker)


def _run_earnings(ticker: str | None) -> tuple[int, list[str]]:
    return ingest_earnings(ticker=ticker)


def _source_handlers() -> dict[str, Callable[[str | None], tuple[int, list[str]]]]:
    return {
        "prices": _run_prices,
        "vix": _run_vix,
        "fundamentals": _run_fundamentals,
        "earnings": _run_earnings,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _build_parser().parse_args()

    start_time = time.monotonic()
    selected_sources = ["prices", "vix", "fundamentals", "earnings"]
    if args.source != "all":
        selected_sources = [args.source]

    inserted_securities = seed_universe()
    logger.info("seed_universe_done: inserted=%s", inserted_securities)

    rows_by_source: dict[str, int] = {}
    failed_tickers_by_source: dict[str, list[str]] = {}
    failed_sources: list[str] = []

    for source_name in selected_sources:
        handler = _source_handlers()[source_name]
        try:
            rows_upserted, failed_tickers = handler(args.ticker)
            rows_by_source[source_name] = rows_upserted
            failed_tickers_by_source[source_name] = failed_tickers
            if failed_tickers:
                failed_sources.append(source_name)
        except Exception:
            rows_by_source[source_name] = 0
            failed_tickers_by_source[source_name] = []
            failed_sources.append(source_name)
            logger.exception("source_ingestion_failed: source=%s", source_name)

    duration_seconds = round(time.monotonic() - start_time, 2)
    logger.info(
        "ingestion_summary: seed_inserted=%s rows=%s failed_tickers=%s failed_sources=%s duration_seconds=%s",
        inserted_securities,
        rows_by_source,
        failed_tickers_by_source,
        failed_sources,
        duration_seconds,
    )

    if failed_sources:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
