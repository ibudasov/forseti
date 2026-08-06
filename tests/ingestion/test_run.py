from __future__ import annotations

from argparse import Namespace

from app.ingestion.earnings import ingest_earnings
from app.ingestion import run


class TestIngestionRun:
    def test_ingest_earnings_without_api_key_skips_successfully(self, monkeypatch):
        monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "")
        monkeypatch.setattr("app.ingestion.earnings.get_settings", lambda: Namespace(ALPHA_VANTAGE_API_KEY=""))

        rows, failures = ingest_earnings()

        assert rows == 0
        assert failures == []

    def test_run_continues_after_source_failure_and_returns_non_zero(self, monkeypatch):
        execution_order: list[str] = []

        class ParserStub:
            def parse_args(self):
                return Namespace(source="all", ticker=None)

        monkeypatch.setattr(run, "_build_parser", lambda: ParserStub())
        monkeypatch.setattr(run, "seed_universe", lambda: 35)

        def prices_handler(_):
            execution_order.append("prices")
            return 10, ["NVDA"]

        def vix_handler(_):
            execution_order.append("vix")
            return 20, []

        def fundamentals_handler(_):
            execution_order.append("fundamentals")
            return 5, []

        def earnings_handler(_):
            execution_order.append("earnings")
            return 7, []

        monkeypatch.setattr(
            run,
            "_source_handlers",
            lambda: {
                "prices": prices_handler,
                "vix": vix_handler,
                "fundamentals": fundamentals_handler,
                "earnings": earnings_handler,
            },
        )

        exit_code = run.main()

        assert execution_order == ["prices", "vix", "fundamentals", "earnings"]
        assert exit_code == 1

    def test_run_returns_zero_when_selected_source_succeeds(self, monkeypatch):
        class ParserStub:
            def parse_args(self):
                return Namespace(source="vix", ticker=None)

        monkeypatch.setattr(run, "_build_parser", lambda: ParserStub())
        monkeypatch.setattr(run, "seed_universe", lambda: 0)
        monkeypatch.setattr(run, "_source_handlers", lambda: {"vix": lambda _: (12, [])})

        exit_code = run.main()

        assert exit_code == 0
