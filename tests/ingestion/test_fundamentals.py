from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.ingestion.fundamentals import (
    EdgarClient,
    normalize_fundamental_observations,
    summarize_observation_coverage,
    to_fundamental,
)


def _load_sample_payload() -> dict:
    fixture_path = Path(__file__).parent.parent / "fixtures" / "edgar_companyfacts_sample.json"
    with open(fixture_path, "r", encoding="utf-8") as file:
        return json.load(file)


def _quarterly_payload() -> dict:
    return {
        "cik": "1234567890",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-03-31",
                                "val": 100,
                                "form": "10-Q",
                                "fp": "Q1",
                                "fy": 2024,
                                "filed": "2024-04-25",
                                "accn": "0001",
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-06-30",
                                "val": 250,
                                "form": "10-Q",
                                "fp": "Q2",
                                "fy": 2024,
                                "filed": "2024-07-25",
                                "accn": "0002",
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-09-30",
                                "val": 390,
                                "form": "10-Q",
                                "fp": "Q3",
                                "fy": 2024,
                                "filed": "2024-10-25",
                                "accn": "0003",
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 560,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2024,
                                "filed": "2025-02-10",
                                "accn": "0004",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-03-31",
                                "val": 130,
                                "form": "10-Q",
                                "fp": "Q1",
                                "fy": 2025,
                                "filed": "2025-04-20",
                                "accn": "1001",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-06-30",
                                "val": 300,
                                "form": "10-Q",
                                "fp": "Q2",
                                "fy": 2025,
                                "filed": "2025-07-20",
                                "accn": "1002",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-09-30",
                                "val": 480,
                                "form": "10-Q",
                                "fp": "Q3",
                                "fy": 2025,
                                "filed": "2025-10-20",
                                "accn": "1003",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 690,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2025,
                                "filed": "2026-02-10",
                                "accn": "1004",
                            },
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-03-31",
                                "val": 20,
                                "form": "10-Q",
                                "fp": "Q1",
                                "fy": 2024,
                                "filed": "2024-04-25",
                                "accn": "0001",
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-06-30",
                                "val": 55,
                                "form": "10-Q",
                                "fp": "Q2",
                                "fy": 2024,
                                "filed": "2024-07-25",
                                "accn": "0002",
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-09-30",
                                "val": 90,
                                "form": "10-Q",
                                "fp": "Q3",
                                "fy": 2024,
                                "filed": "2024-10-25",
                                "accn": "0003",
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 120,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2024,
                                "filed": "2025-02-10",
                                "accn": "0004",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-03-31",
                                "val": 35,
                                "form": "10-Q",
                                "fp": "Q1",
                                "fy": 2025,
                                "filed": "2025-04-20",
                                "accn": "1001",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-06-30",
                                "val": 80,
                                "form": "10-Q",
                                "fp": "Q2",
                                "fy": 2025,
                                "filed": "2025-07-20",
                                "accn": "1002",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-09-30",
                                "val": 135,
                                "form": "10-Q",
                                "fp": "Q3",
                                "fy": 2025,
                                "filed": "2025-10-20",
                                "accn": "1003",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 190,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2025,
                                "filed": "2026-02-10",
                                "accn": "1004",
                            },
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 200,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2024,
                                "filed": "2025-02-10",
                                "accn": "0004",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 260,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2025,
                                "filed": "2026-02-10",
                                "accn": "1004",
                            },
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 50,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2024,
                                "filed": "2025-02-10",
                                "accn": "0004",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 70,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2025,
                                "filed": "2026-02-10",
                                "accn": "1004",
                            },
                        ]
                    }
                },
                "StockholdersEquity": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-12-31",
                                "val": 300,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2024,
                                "filed": "2025-02-10",
                                "accn": "0004",
                            },
                            {
                                "end": "2025-12-31",
                                "val": 320,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2025,
                                "filed": "2026-02-10",
                                "accn": "1004",
                            },
                        ]
                    }
                },
                "LongTermDebtNoncurrent": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-12-31",
                                "val": 80,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2024,
                                "filed": "2025-02-10",
                                "accn": "0004",
                            },
                            {
                                "end": "2025-12-31",
                                "val": 100,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2025,
                                "filed": "2026-02-10",
                                "accn": "1004",
                            },
                        ]
                    }
                },
                "DebtCurrent": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-12-31",
                                "val": 20,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2024,
                                "filed": "2025-02-10",
                                "accn": "0004",
                            },
                            {
                                "end": "2025-12-31",
                                "val": 30,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2025,
                                "filed": "2026-02-10",
                                "accn": "1004",
                            },
                        ]
                    }
                },
                "EarningsPerShareDiluted": {
                    "units": {
                        "USD/shares": [
                            {
                                "end": "2024-12-31",
                                "val": 1.2,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2024,
                                "filed": "2025-02-10",
                                "accn": "0004",
                            },
                            {
                                "end": "2025-12-31",
                                "val": 1.6,
                                "form": "10-K",
                                "fp": "FY",
                                "fy": 2025,
                                "filed": "2026-02-10",
                                "accn": "1004",
                            },
                        ]
                    }
                },
            }
        },
    }


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

    def test_to_fundamental_projects_only_metrics_from_latest_annual_period(self):
        payload = _load_sample_payload()
        payload["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"] = [
            {"end": "2023-12-31", "val": 400, "form": "10-K", "fp": "FY"},
        ]

        result = to_fundamental(22, payload)

        assert result.as_of_date == date(2024, 12, 31)
        assert result.fcf is None

    def test_to_fundamental_keeps_latest_snapshot_without_revenue_row(self):
        payload = _load_sample_payload()
        payload["facts"]["us-gaap"].pop("Revenues")

        result = to_fundamental(23, payload)

        assert result is not None
        assert result.as_of_date == date(2024, 12, 31)
        assert result.fcf == Decimal("300")


class TestObservationNormalization:
    def test_normalize_fundamental_observations_derives_standalone_quarters_and_provenance(self):
        observations = normalize_fundamental_observations(11, _quarterly_payload())

        revenue_observations = {
            (observation.fiscal_year, observation.fiscal_period): observation
            for observation in observations
            if observation.metric_name == "revenue"
        }

        assert revenue_observations[(2025, "Q1")].value == Decimal("130")
        assert revenue_observations[(2025, "Q2")].value == Decimal("170")
        assert revenue_observations[(2025, "Q3")].value == Decimal("180")
        assert revenue_observations[(2025, "Q4")].value == Decimal("210")
        assert revenue_observations[(2025, "Q2")].is_derived is True
        assert "derived from" in revenue_observations[(2025, "Q2")].derivation
        assert revenue_observations[(2025, "Q1")].source_concept == (
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        )

    def test_normalize_fundamental_observations_prefers_amended_filings(self):
        payload = _quarterly_payload()
        revenue_rows = payload["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
        revenue_rows.append(
            {
                "start": "2025-01-01",
                "end": "2025-06-30",
                "val": 310,
                "form": "10-Q/A",
                "fp": "Q2",
                "fy": 2025,
                "filed": "2025-08-01",
                "accn": "1002A",
            }
        )

        observations = normalize_fundamental_observations(11, payload)
        revenue_observations = {
            (observation.fiscal_year, observation.fiscal_period): observation
            for observation in observations
            if observation.metric_name == "revenue"
        }

        assert revenue_observations[(2025, "Q2")].value == Decimal("180")
        assert revenue_observations[(2025, "Q2")].accession_number == "1002A"

    def test_normalize_fundamental_observations_avoids_overlapping_debt_double_count(self):
        payload = _quarterly_payload()
        payload["facts"]["us-gaap"]["LongTermDebt"] = {
            "units": {
                "USD": [
                    {
                        "end": "2025-12-31",
                        "val": 100,
                        "form": "10-K",
                        "fp": "FY",
                        "fy": 2025,
                        "filed": "2026-02-10",
                        "accn": "1004",
                    }
                ]
            }
        }

        observations = normalize_fundamental_observations(11, payload)
        debt_observation = next(
            observation
            for observation in observations
            if observation.metric_name == "total_debt"
            and observation.fiscal_year == 2025
            and observation.fiscal_period == "FY"
        )

        assert debt_observation.value == Decimal("130")

    def test_summarize_observation_coverage_reports_latest_periods(self):
        observations = normalize_fundamental_observations(11, _quarterly_payload())

        coverage = summarize_observation_coverage("TEST", observations)

        assert coverage.latest_annual_period == date(2025, 12, 31)
        assert coverage.latest_quarterly_period == date(2025, 12, 31)
        assert coverage.annual_period_count == 2
        assert coverage.quarterly_period_count == 8
        assert coverage.missing_metrics == ()


def test_edgar_client_uses_configured_user_agent():
    client = EdgarClient("Forseti/0.1 (gazer-flair9o@icloud.com)")

    assert client._http_client.headers["User-Agent"] == (
        "Forseti/0.1 (gazer-flair9o@icloud.com)"
    )
    client._http_client.close()
