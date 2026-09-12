from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Callable

from app.ingestion.coverage import build_coverage_report
from app.ingestion.earnings import ingest_earnings
from app.ingestion.features import compute_technical_features
from app.ingestion.fundamentals import backfill_fundamental_observations, ingest_fundamentals
from app.ingestion.prices import ingest_prices
from app.ingestion.universe import seed_universe
from app.ingestion.vix import ingest_vix
from app.settings import get_settings

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Forseti structured data ingestion")
    parser.add_argument(
        "--source",
        choices=["all", "prices", "vix", "fundamentals", "fundamentals-backfill", "earnings", "features"],
        default="all",
        help="Select which source to ingest",
    )
    parser.add_argument("--ticker", default=None, help="Optional ticker filter for per-security sources")
    parser.add_argument(
        "--refetch-fundamentals",
        action="store_true",
        help="When backfilling fundamentals, refetch SEC payloads after replaying stored raw payloads",
    )
    return parser


def _run_prices(ticker: str | None) -> tuple[int, list[str]]:
    return ingest_prices(ticker=ticker)


def _run_vix(_: str | None) -> tuple[int, list[str]]:
    return ingest_vix(), []


def _run_fundamentals(ticker: str | None) -> tuple[int, list[str]]:
    return ingest_fundamentals(ticker=ticker)


def _run_fundamentals_backfill(ticker: str | None, *, refetch: bool) -> tuple[int, list[str]]:
    return backfill_fundamental_observations(ticker=ticker, refetch=refetch)


def _run_earnings(ticker: str | None) -> tuple[int, list[str]]:
    return ingest_earnings(ticker=ticker)


def _run_features(_: str | None) -> tuple[int, list[str]]:
    rows_upserted, failed_tickers = compute_technical_features()
    return rows_upserted, failed_tickers


def _source_handlers() -> dict[str, Callable[[str | None], tuple[int, list[str]]]]:
    return {
        "prices": _run_prices,
        "vix": _run_vix,
        "fundamentals": _run_fundamentals,
        "fundamentals-backfill": lambda ticker: _run_fundamentals_backfill(ticker, refetch=False),
        "earnings": _run_earnings,
        "features": _run_features,
    }


def _write_coverage_report() -> None:
    report = build_coverage_report()
    payload = {"sources": [source.as_dict() for source in report]}
    report_path = Path(get_settings().INGEST_REPORT_PATH)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _build_parser().parse_args()
    handlers = _source_handlers()
    if getattr(args, "refetch_fundamentals", False):
        handlers["fundamentals-backfill"] = lambda ticker: _run_fundamentals_backfill(
            ticker,
            refetch=True,
        )

    start_time = time.monotonic()
    selected_sources = list(handlers)
    if args.source != "all":
        selected_sources = [args.source]

    inserted_securities = seed_universe()
    logger.info("seed_universe_done: inserted=%s", inserted_securities)

    rows_by_source: dict[str, int] = {}
    failed_tickers_by_source: dict[str, list[str]] = {}
    failed_sources: list[str] = []

    for source_name in selected_sources:
        handler = handlers[source_name]
        try:
            rows_upserted, failed_tickers = handler(args.ticker)
            rows_by_source[source_name] = rows_upserted
            failed_tickers_by_source[source_name] = failed_tickers
        except Exception:
            rows_by_source[source_name] = 0
            failed_tickers_by_source[source_name] = []
            failed_sources.append(source_name)
            logger.exception("source_ingestion_failed: source=%s", source_name)

    if args.source == "all" and args.ticker is None:
        try:
            _write_coverage_report()
        except Exception:
            logger.exception("ingestion_coverage_report_failed")

    duration_seconds = round(time.monotonic() - start_time, 2)
    logger.info(
        "ingestion_summary: seed_inserted=%s rows=%s failed_tickers=%s failed_sources=%s duration_seconds=%s",
        inserted_securities,
        rows_by_source,
        failed_tickers_by_source,
        failed_sources,
        duration_seconds,
    )

    any_failed_tickers = any(tickers for tickers in failed_tickers_by_source.values())
    if failed_sources or any_failed_tickers:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
