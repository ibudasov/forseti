from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.ingestion.fundamentals import to_fundamental


def _load_sample_payload() -> dict:
    fixture_path = Path(__file__).parent.parent / "fixtures" / "edgar_companyfacts_sample.json"
    with open(fixture_path, "r", encoding="utf-8") as file:
        return json.load(file)


class TestFundamentalMapping:
    def test_to_fundamental_derives_metrics_from_payload(self):
        payload = _load_sample_payload()

        result = to_fundamental(11, payload)

        assert result.security_id == 11
        assert result.as_of_date == date(2024, 12, 31)
        assert result.revenue_growth == Decimal("0.2")
        assert result.fcf == Decimal("300")
        assert result.debt_to_equity == Decimal("0.5")
        assert result.eps_trend == Decimal("0.5")
        assert result.margins == Decimal("0.2")
        assert result.raw_payload == payload

    def test_to_fundamental_uses_revenue_fallback_tags(self):
        payload = _load_sample_payload()
        us_gaap = payload["facts"]["us-gaap"]
        us_gaap.pop("Revenues")
        us_gaap["SalesRevenueNet"] = {
            "units": {
                "USD": [
                    {"end": "2023-12-31", "val": 900, "form": "10-K", "fp": "FY"},
                    {"end": "2024-12-31", "val": 990, "form": "10-K", "fp": "FY"},
                ]
            }
        }

        result = to_fundamental(13, payload)

        assert result.as_of_date == date(2024, 12, 31)
        assert result.revenue_growth == Decimal("0.1")

    def test_to_fundamental_uses_liabilities_fallback_for_debt_ratio(self):
        payload = _load_sample_payload()
        us_gaap = payload["facts"]["us-gaap"]
        us_gaap.pop("LongTermDebtNoncurrent")
        us_gaap.pop("DebtCurrent")

        result = to_fundamental(17, payload)

        assert result.debt_to_equity == Decimal("1.285714285714285714285714286")

    def test_to_fundamental_returns_none_for_missing_optional_metrics(self):
        payload = _load_sample_payload()
        stripped_payload = deepcopy(payload)
        us_gaap = stripped_payload["facts"]["us-gaap"]
        us_gaap.pop("NetCashProvidedByUsedInOperatingActivities")
        us_gaap.pop("PaymentsToAcquirePropertyPlantAndEquipment")
        us_gaap.pop("EarningsPerShareDiluted")
        us_gaap.pop("NetIncomeLoss")
        us_gaap["Revenues"]["units"]["USD"] = [
            {"end": "2024-12-31", "val": 1200, "form": "10-K", "fp": "FY"}
        ]

        result = to_fundamental(21, stripped_payload)

        assert result.revenue_growth is None
        assert result.fcf is None
        assert result.eps_trend is None
        assert result.margins is None
