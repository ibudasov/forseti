from __future__ import annotations

from app.ingestion.earnings import parse_earnings_calendar


class TestEarningsParsing:
    def test_parse_earnings_calendar_filters_active_tickers_and_sets_unconfirmed(self):
        csv_payload = "\n".join(
            [
                "symbol,name,reportDate,fiscalDateEnding,estimate,currency",
                "NVDA,NVIDIA Corporation,2026-09-01,2026-07-31,0.85,USD",
                "MSFT,Microsoft Corporation,2026-09-03,2026-06-30,2.25,USD",
                "UNKN,Unknown Co,2026-09-10,2026-06-30,1.00,USD",
            ]
        )

        events = parse_earnings_calendar(csv_payload, {"NVDA": 1, "MSFT": 2})

        assert len(events) == 2
        assert {event.security_id for event in events} == {1, 2}
        assert all(event.confirmed is False for event in events)
