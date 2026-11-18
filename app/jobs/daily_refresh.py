"""Scheduled daily refresh job: trading-calendar gate, structured ingestion, RAG ingestion, and report."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from app.db.repository import list_active_securities
from app.ingestion.run import StructuredIngestionResult, run_structured_ingestion
from app.ingestion.universe import seed_universe
from app.rag.cli import _build_embedding_client, _run_ticker
from app.settings import get_settings

logger = logging.getLogger(__name__)

ET_ZONE = ZoneInfo("America/New_York")
XNYS_CALENDAR = xcals.get_calendar("XNYS")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Forseti scheduled daily refresh")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run refresh even when XNYS is closed",
    )
    parser.add_argument(
        "--skip-rag",
        action="store_true",
        help="Skip RAG document ingestion after structured ingestion",
    )
    return parser


def is_trading_day(target_date: date) -> bool:
    """Return True when target_date is a scheduled XNYS trading session."""
    return bool(XNYS_CALENDAR.is_session(pd.Timestamp(target_date)))


def evaluate_refresh_exit_code(
    *,
    failed_sources: Sequence[str],
    failed_tickers_by_source: Mapping[str, Sequence[str]],
    attempted_tickers_by_source: Mapping[str, int],
    max_failed_ratio: float,
) -> tuple[int, float, str]:
    """Evaluate exit status (0 or 1), worst failed-ticker ratio, and reason."""
    if failed_sources:
        return 1, 1.0, f"failed_sources={list(failed_sources)}"

    worst_ratio = 0.0
    for source_name, failed_tickers in failed_tickers_by_source.items():
        if not failed_tickers:
            continue
        attempted = attempted_tickers_by_source.get(source_name, 0)
        if attempted <= 0:
            return 1, 1.0, f"source={source_name} reported failures with attempted=0"
        ratio = len(failed_tickers) / attempted
        worst_ratio = max(worst_ratio, ratio)
        if ratio > max_failed_ratio:
            return (
                1,
                worst_ratio,
                f"source={source_name} failed_ratio={ratio:.3f} exceeds threshold={max_failed_ratio:.3f}",
            )

    if worst_ratio > 0.0:
        return (
            0,
            worst_ratio,
            f"partial_ticker_failures within threshold={max_failed_ratio:.3f} (worst_ratio={worst_ratio:.3f})",
        )
    return 0, 0.0, "ok"


def _write_daily_report(report_dir: str, report_date: date, payload: dict[str, object]) -> Path:
    report_path = Path(report_dir) / "ingest" / f"{report_date.isoformat()}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def _emit_refresh_event(
    *,
    status: str,
    exit_status: int,
    market_closed: bool,
    duration_seconds: float,
    stored_chunks: int,
    failed_sources: Sequence[str],
) -> None:
    log_fn = logger.error if exit_status != 0 else logger.info
    log_fn(
        "daily_refresh_status status=%s exit_status=%s market_closed=%s "
        "duration_seconds=%s stored_chunks=%s failed_sources=%s",
        status,
        exit_status,
        market_closed,
        duration_seconds,
        stored_chunks,
        list(failed_sources),
        extra={
            "event": "daily_refresh_status",
            "status": status,
            "exit_status": exit_status,
            "market_closed": market_closed,
            "duration_seconds": duration_seconds,
            "stored_chunks": stored_chunks,
            "failed_sources": list(failed_sources),
        },
    )


def _run_rag_ingestion(securities) -> tuple[int, list[str], bool, float]:
    rag_start = time.monotonic()
    stored_chunks = 0
    failed_tickers: list[str] = []
    try:
        embedding_client = _build_embedding_client()
    except Exception:
        logger.exception("daily_refresh_rag_client_init_failed")
        return 0, [], True, round(time.monotonic() - rag_start, 2)

    for security in securities:
        try:
            stored_chunks += _run_ticker(security.ticker, security.sector_tag, embedding_client)
        except Exception:
            failed_tickers.append(security.ticker)
            logger.exception("daily_refresh_rag_ticker_failed: ticker=%s", security.ticker)

    return stored_chunks, failed_tickers, False, round(time.monotonic() - rag_start, 2)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    settings = get_settings()
    today_et = datetime.now(ET_ZONE).date()
    total_start = time.monotonic()

    if not args.force and not is_trading_day(today_et):
        logger.info("market_closed: date=%s", today_et.isoformat())
        duration_seconds = round(time.monotonic() - total_start, 2)
        _emit_refresh_event(
            status="market_closed",
            exit_status=0,
            market_closed=True,
            duration_seconds=duration_seconds,
            stored_chunks=0,
            failed_sources=(),
        )
        return 0

    seed_universe()
    securities = list_active_securities()
    active_tickers = [security.ticker for security in securities]

    structured: StructuredIngestionResult = run_structured_ingestion(
        source="all",
        active_tickers=active_tickers,
    )

    rows_by_source = dict(structured.rows_by_source)
    failed_tickers_by_source = {k: list(v) for k, v in structured.failed_tickers_by_source.items()}
    attempted_tickers_by_source = dict(structured.attempted_tickers_by_source)
    failed_sources = list(structured.failed_sources)

    stored_chunks = 0
    rag_duration_seconds = 0.0
    if not args.skip_rag:
        stored_chunks, rag_failed_tickers, rag_source_failed, rag_duration_seconds = _run_rag_ingestion(securities)
        attempted_tickers_by_source["rag"] = len(active_tickers)
        failed_tickers_by_source["rag"] = rag_failed_tickers
        if rag_source_failed:
            failed_sources.append("rag")

    exit_status, worst_ratio, policy_reason = evaluate_refresh_exit_code(
        failed_sources=failed_sources,
        failed_tickers_by_source=failed_tickers_by_source,
        attempted_tickers_by_source=attempted_tickers_by_source,
        max_failed_ratio=settings.INGEST_MAX_FAILED_TICKER_RATIO,
    )
    if exit_status == 0 and worst_ratio > 0.0:
        logger.warning("daily_refresh_partial_failures: %s", policy_reason)
    elif exit_status != 0:
        logger.error("daily_refresh_policy_failed: %s", policy_reason)

    total_duration_seconds = round(time.monotonic() - total_start, 2)
    status = "failed" if exit_status != 0 else "success"

    report_payload: dict[str, object] = {
        "date": today_et.isoformat(),
        "market_closed": False,
        "forced": bool(args.force),
        "skip_rag": bool(args.skip_rag),
        "rows_by_source": rows_by_source,
        "attempted_tickers_by_source": attempted_tickers_by_source,
        "failed_tickers_by_source": failed_tickers_by_source,
        "failed_sources": failed_sources,
        "stored_chunks": stored_chunks,
        "durations": {
            "structured_seconds": structured.duration_seconds,
            "rag_seconds": rag_duration_seconds,
            "total_seconds": total_duration_seconds,
        },
        "exit_status": exit_status,
        "status": status,
    }
    _write_daily_report(settings.REPORT_DIR, today_et, report_payload)

    _emit_refresh_event(
        status=status,
        exit_status=exit_status,
        market_closed=False,
        duration_seconds=total_duration_seconds,
        stored_chunks=stored_chunks,
        failed_sources=failed_sources,
    )
    return exit_status


if __name__ == "__main__":
    from app.runtime import run_job

    sys.exit(run_job(main))
