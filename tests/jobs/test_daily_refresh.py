from __future__ import annotations

import json
import logging
from argparse import Namespace
from datetime import datetime
from types import SimpleNamespace

from app.ingestion.run import StructuredIngestionResult
from app.jobs import daily_refresh
from app.observability.logging import JsonFormatter


class TestFailurePolicy:
    def test_threshold_equality_exits_zero_with_warning_status(self):
        code, ratio, _ = daily_refresh.evaluate_refresh_exit_code(
            failed_sources=[],
            failed_tickers_by_source={"prices": ["NVDA"]},
            attempted_tickers_by_source={"prices": 10},
            max_failed_ratio=0.1,
        )
        assert code == 0
        assert ratio == 0.1

    def test_above_threshold_exits_nonzero(self):
        code, ratio, _ = daily_refresh.evaluate_refresh_exit_code(
            failed_sources=[],
            failed_tickers_by_source={"prices": ["NVDA", "AMD"]},
            attempted_tickers_by_source={"prices": 10},
            max_failed_ratio=0.1,
        )
        assert code == 1
        assert ratio == 0.2

    def test_source_level_failure_always_exits_nonzero(self):
        code, _, reason = daily_refresh.evaluate_refresh_exit_code(
            failed_sources=["vix"],
            failed_tickers_by_source={},
            attempted_tickers_by_source={"vix": 1},
            max_failed_ratio=0.1,
        )
        assert code == 1
        assert "vix" in reason

    def test_zero_attempted_with_failures_exits_nonzero_without_zerodivision(self):
        code, ratio, _ = daily_refresh.evaluate_refresh_exit_code(
            failed_sources=[],
            failed_tickers_by_source={"prices": ["NVDA"]},
            attempted_tickers_by_source={"prices": 0},
            max_failed_ratio=0.1,
        )
        assert code == 1
        assert ratio == 1.0


class TestDailyRefreshJob:
    def test_market_closed_skips_ingestion_and_exits_zero(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(daily_refresh, "is_trading_day", lambda _d: False)
        monkeypatch.setattr(
            daily_refresh,
            "get_settings",
            lambda: Namespace(REPORT_DIR=str(tmp_path), INGEST_MAX_FAILED_TICKER_RATIO=0.1),
        )
        called = False

        def fail_if_called(**_kwargs):
            nonlocal called
            called = True
            raise AssertionError("ingestion must not run when market is closed")

        monkeypatch.setattr(daily_refresh, "seed_universe", fail_if_called)

        with caplog.at_level(logging.INFO):
            exit_code = daily_refresh.main([])

        assert exit_code == 0
        assert not called
        assert "market_closed" in caplog.text
        assert list((tmp_path / "ingest").glob("*.json")) == []

    def test_force_runs_when_market_closed_and_skip_rag_skips_rag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(daily_refresh, "is_trading_day", lambda _d: False)
        monkeypatch.setattr(
            daily_refresh,
            "get_settings",
            lambda: Namespace(REPORT_DIR=str(tmp_path), INGEST_MAX_FAILED_TICKER_RATIO=0.1),
        )
        monkeypatch.setattr(daily_refresh, "seed_universe", lambda: 0)
        monkeypatch.setattr(
            daily_refresh,
            "list_active_securities",
            lambda: [SimpleNamespace(ticker="NVDA", sector_tag="ai")],
        )
        monkeypatch.setattr(
            daily_refresh,
            "run_structured_ingestion",
            lambda **_kwargs: StructuredIngestionResult(
                inserted_securities=0,
                rows_by_source={"prices": 10, "vix": 1},
                failed_tickers_by_source={"prices": [], "vix": []},
                attempted_tickers_by_source={"prices": 1, "vix": 1},
                failed_sources=[],
                duration_seconds=0.5,
                coverage_report_written=True,
            ),
        )
        monkeypatch.setattr(
            daily_refresh,
            "_run_rag_ingestion",
            lambda *_args: (_ for _ in ()).throw(AssertionError("RAG must be skipped")),
        )

        exit_code = daily_refresh.main(["--force", "--skip-rag"])

        assert exit_code == 0
        reports = list((tmp_path / "ingest").glob("*.json"))
        assert len(reports) == 1
        data = json.loads(reports[0].read_text(encoding="utf-8"))
        assert data["forced"] is True
        assert data["skip_rag"] is True
        assert data["rows_by_source"] == {"prices": 10, "vix": 1}
        assert data["stored_chunks"] == 0
        assert data["exit_status"] == 0

    def test_full_run_writes_report_and_structured_json_event(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(daily_refresh, "is_trading_day", lambda _d: True)
        monkeypatch.setattr(
            daily_refresh,
            "get_settings",
            lambda: Namespace(REPORT_DIR=str(tmp_path), INGEST_MAX_FAILED_TICKER_RATIO=0.1),
        )
        monkeypatch.setattr(daily_refresh, "seed_universe", lambda: 2)
        monkeypatch.setattr(
            daily_refresh,
            "list_active_securities",
            lambda: [
                SimpleNamespace(ticker="NVDA", sector_tag="ai"),
                SimpleNamespace(ticker="AMD", sector_tag="ai"),
            ],
        )
        monkeypatch.setattr(
            daily_refresh,
            "run_structured_ingestion",
            lambda **_kwargs: StructuredIngestionResult(
                inserted_securities=2,
                rows_by_source={"prices": 20},
                failed_tickers_by_source={"prices": []},
                attempted_tickers_by_source={"prices": 2},
                failed_sources=[],
                duration_seconds=1.2,
                coverage_report_written=True,
            ),
        )
        monkeypatch.setattr(daily_refresh, "_build_embedding_client", lambda: object())
        monkeypatch.setattr(daily_refresh, "_run_ticker", lambda *_args: 5)

        with caplog.at_level(logging.INFO):
            exit_code = daily_refresh.main([])

        assert exit_code == 0
        reports = list((tmp_path / "ingest").glob("*.json"))
        assert len(reports) == 1
        data = json.loads(reports[0].read_text(encoding="utf-8"))
        assert data["stored_chunks"] == 10
        assert data["status"] == "success"

        event_records = [r for r in caplog.records if getattr(r, "event", None) == "daily_refresh_status"]
        assert len(event_records) == 1
        formatted = json.loads(JsonFormatter().format(event_records[0]))
        assert formatted["event"] == "daily_refresh_status"
        assert formatted["status"] == "success"
        assert formatted["stored_chunks"] == 10
        assert formatted["exit_status"] == 0

    def test_is_trading_day_respects_xnys_calendar(self):
        # Saturday 2026-11-21 is closed; Wednesday 2026-11-18 is open.
        assert daily_refresh.is_trading_day(datetime(2026, 11, 21).date()) is False
        assert daily_refresh.is_trading_day(datetime(2026, 11, 18).date()) is True
