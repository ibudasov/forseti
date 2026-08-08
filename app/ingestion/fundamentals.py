from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Optional

import httpx

from app.db.models import Fundamental
from app.db.repository import list_active_securities, upsert_fundamental
from app.settings import get_settings

logger = logging.getLogger(__name__)

_CIK_LOOKUP_URL = "https://www.sec.gov/files/company_tickers.json"
_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
)
_DEBT_TAGS = ("LongTermDebtNoncurrent", "LongTermDebt", "DebtCurrent", "LongTermDebtCurrent")
_LIABILITIES_FALLBACK_TAG = "Liabilities"
_EQUITY_TAG = "StockholdersEquity"
_EPS_TAG = "EarningsPerShareDiluted"
_NET_INCOME_TAG = "NetIncomeLoss"
_CASH_FROM_OPERATIONS_TAG = "NetCashProvidedByUsedInOperatingActivities"
_CAPEX_TAG = "PaymentsToAcquirePropertyPlantAndEquipment"


@dataclass(frozen=True)
class AnnualFact:
    end_date: date
    value: Decimal


class EdgarClient:
    def __init__(self, user_agent: str, request_delay_seconds: float = 0.2):
        self._http_client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=30.0,
        )
        self._request_delay_seconds = request_delay_seconds
        self._has_fetched_company_facts = False

    def resolve_cik(self, tickers: list[str]) -> dict[str, str]:
        response = self._http_client.get(_CIK_LOOKUP_URL)
        self._raise_for_status(response, "ticker lookup", None)
        payload = response.json()

        requested_tickers = {ticker.upper() for ticker in tickers}
        cik_by_ticker: dict[str, str] = {}
        for row in payload.values():
            ticker = str(row.get("ticker", "")).upper()
            if ticker not in requested_tickers:
                continue
            cik_by_ticker[ticker] = str(row.get("cik_str", "")).zfill(10)

        return cik_by_ticker

    def fetch_company_facts(self, cik: str) -> dict[str, Any]:
        if self._has_fetched_company_facts:
            time.sleep(self._request_delay_seconds)
        self._has_fetched_company_facts = True

        response = self._http_client.get(_COMPANY_FACTS_URL.format(cik=cik))
        self._raise_for_status(response, "company facts", cik)
        return response.json()

    def close(self) -> None:
        self._http_client.close()

    def _raise_for_status(self, response: httpx.Response, endpoint_name: str, cik: Optional[str]) -> None:
        if response.status_code < 400:
            return

        cik_fragment = f" cik={cik}" if cik is not None else ""
        raise RuntimeError(
            f"SEC {endpoint_name} request failed{cik_fragment}: status={response.status_code}"
        )


def _parse_annual_facts(payload: dict[str, Any], tag: str) -> list[AnnualFact]:
    us_gaap = payload.get("facts", {}).get("us-gaap", {})
    units_payload = us_gaap.get(tag, {}).get("units", {})

    facts_by_end_date: dict[date, AnnualFact] = {}
    for unit_rows in units_payload.values():
        for row in unit_rows:
            if row.get("form") != "10-K" or row.get("fp") != "FY":
                continue
            end_value = row.get("end")
            metric_value = row.get("val")
            if end_value is None or metric_value is None:
                continue

            annual_fact = AnnualFact(
                end_date=date.fromisoformat(end_value),
                value=Decimal(str(metric_value)),
            )
            facts_by_end_date[annual_fact.end_date] = annual_fact

    return sorted(facts_by_end_date.values(), key=lambda fact: fact.end_date)


def _latest_annual_fact(payload: dict[str, Any], tags: tuple[str, ...]) -> Optional[AnnualFact]:
    for tag in tags:
        annual_facts = _parse_annual_facts(payload, tag)
        if annual_facts:
            return annual_facts[-1]
    return None


def _latest_two_annual_facts(payload: dict[str, Any], tags: tuple[str, ...]) -> list[AnnualFact]:
    for tag in tags:
        annual_facts = _parse_annual_facts(payload, tag)
        if len(annual_facts) >= 2:
            return annual_facts[-2:]
        if annual_facts:
            return annual_facts
    return []


def _safe_ratio(numerator: Optional[Decimal], denominator: Optional[Decimal]) -> Optional[Decimal]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _sum_latest(payload: dict[str, Any], tags: tuple[str, ...]) -> Optional[Decimal]:
    total: Optional[Decimal] = None
    for tag in tags:
        latest_fact = _latest_annual_fact(payload, (tag,))
        if latest_fact is None:
            continue
        if total is None:
            total = Decimal("0")
        total += latest_fact.value
    return total


def _compute_revenue_growth(payload: dict[str, Any]) -> Optional[Decimal]:
    revenue_facts = _latest_two_annual_facts(payload, _REVENUE_TAGS)
    if len(revenue_facts) < 2:
        return None

    previous_revenue = revenue_facts[0].value
    latest_revenue = revenue_facts[1].value
    if previous_revenue == 0:
        return None

    return (latest_revenue - previous_revenue) / previous_revenue


def _compute_fcf(payload: dict[str, Any]) -> Optional[Decimal]:
    cash_from_operations = _latest_annual_fact(payload, (_CASH_FROM_OPERATIONS_TAG,))
    capex = _latest_annual_fact(payload, (_CAPEX_TAG,))
    if cash_from_operations is None or capex is None:
        return None
    return cash_from_operations.value - capex.value


def _compute_debt_to_equity(payload: dict[str, Any]) -> Optional[Decimal]:
    equity = _latest_annual_fact(payload, (_EQUITY_TAG,))
    if equity is None or equity.value == 0:
        return None

    debt_value = _sum_latest(payload, _DEBT_TAGS)
    if debt_value is not None:
        return _safe_ratio(debt_value, equity.value)

    liabilities = _latest_annual_fact(payload, (_LIABILITIES_FALLBACK_TAG,))
    return _safe_ratio(liabilities.value if liabilities is not None else None, equity.value)


def _compute_eps_trend(payload: dict[str, Any]) -> Optional[Decimal]:
    eps_facts = _latest_two_annual_facts(payload, (_EPS_TAG,))
    if len(eps_facts) < 2:
        return None
    return eps_facts[1].value - eps_facts[0].value


def _compute_margins(payload: dict[str, Any]) -> Optional[Decimal]:
    net_income = _latest_annual_fact(payload, (_NET_INCOME_TAG,))
    revenue = _latest_annual_fact(payload, _REVENUE_TAGS)
    return _safe_ratio(
        net_income.value if net_income is not None else None,
        revenue.value if revenue is not None else None,
    )


def _resolve_as_of_date(payload: dict[str, Any]) -> Optional[date]:
    latest_revenue = _latest_annual_fact(payload, _REVENUE_TAGS)
    if latest_revenue is None:
        return None
    return latest_revenue.end_date


def to_fundamental(security_id: int, payload: dict[str, Any]) -> Optional[Fundamental]:
    as_of_date = _resolve_as_of_date(payload)
    if as_of_date is None:
        return None
    return Fundamental(
        security_id=security_id,
        as_of_date=as_of_date,
        revenue_growth=_compute_revenue_growth(payload),
        fcf=_compute_fcf(payload),
        debt_to_equity=_compute_debt_to_equity(payload),
        eps_trend=_compute_eps_trend(payload),
        margins=_compute_margins(payload),
        raw_payload=payload,
    )


def ingest_fundamentals(engine=None, ticker: Optional[str] = None) -> tuple[int, list[str]]:
    settings = get_settings()
    active_securities = list_active_securities(engine=engine)
    if ticker is not None:
        normalized_ticker = ticker.strip().upper()
        active_securities = [security for security in active_securities if security.ticker == normalized_ticker]

    client = EdgarClient(user_agent=settings.EDGAR_USER_AGENT)
    upserted_rows = 0
    failed_tickers: list[str] = []

    try:
        cik_by_ticker = client.resolve_cik([security.ticker for security in active_securities])
        for security in active_securities:
            cik = cik_by_ticker.get(security.ticker)
            if cik is None:
                failed_tickers.append(security.ticker)
                logger.error("fundamentals_cik_missing: ticker=%s", security.ticker)
                continue

            try:
                payload = client.fetch_company_facts(cik)
                fundamental = to_fundamental(security.id, payload)
                if fundamental is None:
                    logger.warning(
                        "fundamentals_skipped: ticker=%s cik=%s reason=missing_annual_revenue",
                        security.ticker,
                        cik,
                    )
                    continue
                upsert_fundamental(fundamental, engine=engine)
                upserted_rows += 1
                logger.info(
                    "fundamentals_ingested: ticker=%s as_of_date=%s",
                    security.ticker,
                    fundamental.as_of_date.isoformat(),
                )
            except Exception:
                failed_tickers.append(security.ticker)
                logger.exception("fundamentals_ingestion_failed: ticker=%s cik=%s", security.ticker, cik)
    finally:
        client.close()

    return upserted_rows, failed_tickers
