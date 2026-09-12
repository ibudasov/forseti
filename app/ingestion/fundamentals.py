from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal, Optional

import httpx

from app.db.models import Fundamental, FundamentalObservation
from app.db.repository import (
    list_active_securities,
    list_fundamentals,
    upsert_fundamental,
    upsert_fundamental_observations,
)
from app.settings import get_settings

logger = logging.getLogger(__name__)

_CIK_LOOKUP_URL = "https://www.sec.gov/files/company_tickers.json"
_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

_REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
)
_GROSS_PROFIT_TAGS = ("GrossProfit",)
_OPERATING_INCOME_TAGS = ("OperatingIncomeLoss",)
_NET_INCOME_TAGS = ("NetIncomeLoss",)
_OPERATING_CASH_FLOW_TAGS = ("NetCashProvidedByUsedInOperatingActivities",)
_CAPEX_TAGS = ("PaymentsToAcquirePropertyPlantAndEquipment",)
_DILUTED_EPS_TAGS = ("EarningsPerShareDiluted",)
_CASH_AND_EQUIVALENTS_TAGS = ("CashAndCashEquivalentsAtCarryingValue",)
_STOCKHOLDERS_EQUITY_TAGS = ("StockholdersEquity",)
_DILUTED_SHARES_TAGS = ("WeightedAverageNumberOfDilutedSharesOutstanding",)
_DEBT_COMPONENT_TAGS = (
    "LongTermDebtNoncurrent",
    "LongTermDebt",
    "DebtCurrent",
    "LongTermDebtCurrent",
)
_LIABILITIES_FALLBACK_TAGS = ("Liabilities",)

_DIRECT_DURATION_METRICS = (
    ("revenue", _REVENUE_TAGS),
    ("gross_profit", _GROSS_PROFIT_TAGS),
    ("operating_income", _OPERATING_INCOME_TAGS),
    ("net_income", _NET_INCOME_TAGS),
    ("operating_cash_flow", _OPERATING_CASH_FLOW_TAGS),
    ("capex", _CAPEX_TAGS),
    ("diluted_eps", _DILUTED_EPS_TAGS),
    ("diluted_shares_outstanding", _DILUTED_SHARES_TAGS),
)
_DIRECT_INSTANT_METRICS = (
    ("cash_and_equivalents", _CASH_AND_EQUIVALENTS_TAGS),
    ("stockholders_equity", _STOCKHOLDERS_EQUITY_TAGS),
)

_REQUIRED_CONTEXT_METRICS = (
    "revenue",
    "free_cash_flow",
    "debt_to_equity",
    "diluted_eps_growth_delta",
    "net_margin",
)


@dataclass(frozen=True)
class ObservationSeed:
    metric_name: str
    value: Decimal
    unit: str | None
    period_start: date
    period_end: date
    fiscal_year: int | None
    fiscal_period: str
    form_type: str
    filed_at: date | None
    accession_number: str | None
    source_concept: str
    source_url: str
    is_derived: bool
    derivation: str | None = None


@dataclass(frozen=True)
class FundamentalObservationCoverage:
    ticker: str
    latest_annual_period: date | None
    latest_quarterly_period: date | None
    annual_period_count: int
    quarterly_period_count: int
    missing_metrics: tuple[str, ...]


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


def to_fundamental(security_id: int, payload: dict[str, Any]) -> Optional[Fundamental]:
    observations = normalize_fundamental_observations(security_id, payload)
    return project_latest_fundamental(security_id, payload, observations)


def normalize_fundamental_observations(
    security_id: int,
    payload: dict[str, Any],
) -> list[FundamentalObservation]:
    cik = _resolve_cik(payload)
    reported_observations = _reported_observations(payload, cik)
    derived_observations = _derived_observations(reported_observations)
    authoritative_observations = _authoritative_seeds(reported_observations + derived_observations)
    return [
        FundamentalObservation(
            security_id=security_id,
            metric_name=observation.metric_name,
            value=observation.value,
            unit=observation.unit,
            period_start=observation.period_start,
            period_end=observation.period_end,
            fiscal_year=observation.fiscal_year,
            fiscal_period=observation.fiscal_period,
            form_type=observation.form_type,
            filed_at=observation.filed_at,
            accession_number=observation.accession_number,
            source_concept=observation.source_concept,
            source_url=observation.source_url,
            is_derived=observation.is_derived,
            derivation=observation.derivation,
        )
        for observation in authoritative_observations
    ]


def project_latest_fundamental(
    security_id: int,
    payload: dict[str, Any],
    observations: list[FundamentalObservation],
) -> Optional[Fundamental]:
    annual_observations_by_metric = _latest_annual_observations_by_metric(observations)
    if not annual_observations_by_metric:
        return None

    revenue = annual_observations_by_metric.get("revenue")
    if revenue is None:
        return None

    return Fundamental(
        security_id=security_id,
        as_of_date=revenue.period_end,
        revenue_growth=_observation_value(annual_observations_by_metric.get("revenue_growth_yoy")),
        fcf=_observation_value(annual_observations_by_metric.get("free_cash_flow")),
        debt_to_equity=_observation_value(annual_observations_by_metric.get("debt_to_equity")),
        eps_trend=_observation_value(annual_observations_by_metric.get("diluted_eps_growth_delta")),
        margins=_observation_value(annual_observations_by_metric.get("net_margin")),
        raw_payload=payload,
    )


def ingest_fundamentals(engine=None, ticker: Optional[str] = None) -> tuple[int, list[str]]:
    settings = get_settings()
    active_securities = list_active_securities(engine=engine)
    if ticker is not None:
        normalized_ticker = ticker.strip().upper()
        active_securities = [security for security in active_securities if security.ticker == normalized_ticker]

    client = EdgarClient(user_agent=settings.EDGAR_USER_AGENT)
    persisted_observations = 0
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
                observations = normalize_fundamental_observations(security.id, payload)
                if not observations:
                    logger.warning(
                        "fundamentals_skipped: ticker=%s cik=%s reason=no_observations",
                        security.ticker,
                        cik,
                    )
                    continue

                upsert_fundamental_observations(observations, engine=engine)
                projection = project_latest_fundamental(security.id, payload, observations)
                if projection is not None:
                    upsert_fundamental(projection, engine=engine)

                persisted_observations += len(observations)
                coverage = summarize_observation_coverage(security.ticker, observations)
                logger.info(
                    (
                        "fundamentals_ingested: ticker=%s cik=%s observations=%s "
                        "latest_annual=%s latest_quarterly=%s missing_metrics=%s"
                    ),
                    security.ticker,
                    cik,
                    len(observations),
                    coverage.latest_annual_period.isoformat() if coverage.latest_annual_period else None,
                    coverage.latest_quarterly_period.isoformat() if coverage.latest_quarterly_period else None,
                    list(coverage.missing_metrics),
                )
            except Exception:
                failed_tickers.append(security.ticker)
                logger.exception("fundamentals_ingestion_failed: ticker=%s cik=%s", security.ticker, cik)
    finally:
        client.close()

    return persisted_observations, failed_tickers


def backfill_fundamental_observations(
    engine=None,
    ticker: Optional[str] = None,
    *,
    refetch: bool = False,
) -> tuple[int, list[str]]:
    fundamentals = list_fundamentals(engine=engine, ticker=ticker)
    persisted_observations = 0
    failed_tickers: list[str] = []

    for fundamental in fundamentals:
        if not fundamental.raw_payload:
            continue
        try:
            observations = normalize_fundamental_observations(fundamental.security_id, fundamental.raw_payload)
            upsert_fundamental_observations(observations, engine=engine)
            projection = project_latest_fundamental(
                fundamental.security_id,
                fundamental.raw_payload,
                observations,
            )
            if projection is not None:
                upsert_fundamental(projection, engine=engine)
            persisted_observations += len(observations)
        except Exception:
            failed_tickers.append(str(fundamental.security_id))
            logger.exception("fundamentals_backfill_failed: security_id=%s", fundamental.security_id)

    if refetch:
        refetched_count, refetch_failures = ingest_fundamentals(engine=engine, ticker=ticker)
        persisted_observations += refetched_count
        failed_tickers.extend(refetch_failures)

    return persisted_observations, failed_tickers


def summarize_observation_coverage(
    ticker: str,
    observations: list[FundamentalObservation],
) -> FundamentalObservationCoverage:
    annual_observations = [observation for observation in observations if observation.fiscal_period == "FY"]
    quarterly_observations = [
        observation for observation in observations if observation.fiscal_period in {"Q1", "Q2", "Q3", "Q4"}
    ]

    annual_periods = {observation.period_end for observation in annual_observations}
    quarterly_periods = {observation.period_end for observation in quarterly_observations}
    latest_annual_period = max(annual_periods) if annual_periods else None
    latest_quarterly_period = max(quarterly_periods) if quarterly_periods else None
    latest_annual_metrics = {
        observation.metric_name
        for observation in annual_observations
        if latest_annual_period is not None and observation.period_end == latest_annual_period
    }
    missing_metrics = tuple(
        metric_name
        for metric_name in _REQUIRED_CONTEXT_METRICS
        if metric_name not in latest_annual_metrics
    )
    return FundamentalObservationCoverage(
        ticker=ticker,
        latest_annual_period=latest_annual_period,
        latest_quarterly_period=latest_quarterly_period,
        annual_period_count=len(annual_periods),
        quarterly_period_count=len(quarterly_periods),
        missing_metrics=missing_metrics,
    )


def _reported_observations(payload: dict[str, Any], cik: str | None) -> list[ObservationSeed]:
    observations: list[ObservationSeed] = []

    for metric_name, tags in _DIRECT_DURATION_METRICS:
        observations.extend(_reported_duration_metric_seeds(payload, cik, metric_name, tags))
    for metric_name, tags in _DIRECT_INSTANT_METRICS:
        observations.extend(_reported_instant_metric_seeds(payload, cik, metric_name, tags))

    observations.extend(_reported_total_debt_seeds(payload, cik))
    return observations


def _derived_observations(reported_observations: list[ObservationSeed]) -> list[ObservationSeed]:
    observations_by_metric = _group_by_metric(reported_observations)
    derived: list[ObservationSeed] = []
    derived.extend(_derive_growth_metric("revenue_growth_yoy", observations_by_metric.get("revenue", [])))
    derived.extend(
        _derive_margin_metric(
            "gross_margin",
            observations_by_metric.get("gross_profit", []),
            observations_by_metric.get("revenue", []),
        )
    )
    derived.extend(
        _derive_margin_metric(
            "operating_margin",
            observations_by_metric.get("operating_income", []),
            observations_by_metric.get("revenue", []),
        )
    )
    derived.extend(
        _derive_margin_metric(
            "net_margin",
            observations_by_metric.get("net_income", []),
            observations_by_metric.get("revenue", []),
        )
    )
    derived.extend(_derive_free_cash_flow(
        observations_by_metric.get("operating_cash_flow", []),
        observations_by_metric.get("capex", []),
    ))
    derived.extend(_derive_growth_delta(
        "diluted_eps_growth_delta",
        observations_by_metric.get("diluted_eps", []),
    ))
    derived.extend(_derive_ratio_metric(
        "debt_to_equity",
        observations_by_metric.get("total_debt", []),
        observations_by_metric.get("stockholders_equity", []),
    ))
    return derived


def _reported_duration_metric_seeds(
    payload: dict[str, Any],
    cik: str | None,
    metric_name: str,
    tags: tuple[str, ...],
) -> list[ObservationSeed]:
    rows = _authoritative_raw_rows(payload, tags)
    observations: list[ObservationSeed] = []

    annual_rows = [
        row for row in rows
        if row.fiscal_period == "FY" and _is_annual_form(row.form_type)
    ]
    observations.extend(
        _reported_rows_to_seeds(metric_name, annual_rows, is_derived=False, derivation=None)
    )

    quarterly_rows = [
        row for row in rows
        if row.fiscal_period in {"Q1", "Q2", "Q3"} and _is_quarterly_form(row.form_type)
    ]
    observations.extend(_derive_quarterly_duration_seeds(metric_name, annual_rows, quarterly_rows, cik))
    return observations


def _reported_instant_metric_seeds(
    payload: dict[str, Any],
    cik: str | None,
    metric_name: str,
    tags: tuple[str, ...],
) -> list[ObservationSeed]:
    rows = _authoritative_raw_rows(payload, tags)
    observations: list[ObservationSeed] = []

    annual_rows = [
        row for row in rows
        if row.fiscal_period == "FY" and _is_annual_form(row.form_type)
    ]
    observations.extend(
        _reported_rows_to_seeds(metric_name, annual_rows, is_derived=False, derivation=None)
    )

    quarterly_rows = [
        row for row in rows
        if row.fiscal_period in {"Q1", "Q2", "Q3"} and _is_quarterly_form(row.form_type)
    ]
    observations.extend(
        _reported_rows_to_seeds(metric_name, quarterly_rows, is_derived=False, derivation=None)
    )
    observations.extend(_derive_q4_instant_seeds(metric_name, annual_rows, cik))
    return observations


def _reported_total_debt_seeds(payload: dict[str, Any], cik: str | None) -> list[ObservationSeed]:
    debt_rows = []
    for tag in _DEBT_COMPONENT_TAGS:
        debt_rows.extend(_authoritative_raw_rows(payload, (tag,)))

    liabilities_rows = _authoritative_raw_rows(payload, _LIABILITIES_FALLBACK_TAGS)
    observations: list[ObservationSeed] = []
    observations.extend(_derive_total_debt_from_components(debt_rows, cik))
    observations.extend(_derive_total_debt_from_liabilities(liabilities_rows, debt_rows, cik))
    return observations


@dataclass(frozen=True)
class RawFactRow:
    value: Decimal
    unit: str | None
    period_start: date
    period_end: date
    fiscal_year: int | None
    fiscal_period: str
    form_type: str
    filed_at: date | None
    accession_number: str | None
    source_concept: str
    source_url: str


def _authoritative_raw_rows(payload: dict[str, Any], tags: tuple[str, ...]) -> list[RawFactRow]:
    for tag in tags:
        rows = _raw_rows_for_tag(payload, tag)
        if rows:
            return _select_authoritative_raw_rows(rows)
    return []


def _raw_rows_for_tag(payload: dict[str, Any], tag: str) -> list[RawFactRow]:
    us_gaap = payload.get("facts", {}).get("us-gaap", {})
    units_payload = us_gaap.get(tag, {}).get("units", {})
    cik = _resolve_cik(payload)
    rows: list[RawFactRow] = []
    for unit_name, unit_rows in units_payload.items():
        for row in unit_rows:
            raw_row = _to_raw_fact_row(tag, unit_name, row, cik)
            if raw_row is not None:
                rows.append(raw_row)
    return rows


def _to_raw_fact_row(
    tag: str,
    unit_name: str,
    row: dict[str, Any],
    cik: str | None,
) -> RawFactRow | None:
    form_type = str(row.get("form", ""))
    fiscal_period = _normalize_fiscal_period(row.get("fp"))
    end_value = row.get("end")
    metric_value = row.get("val")
    if not _is_supported_form(form_type) or fiscal_period is None or end_value is None or metric_value is None:
        return None

    period_end = date.fromisoformat(end_value)
    period_start = _row_period_start(row, period_end)
    filed_at = date.fromisoformat(row["filed"]) if row.get("filed") else None
    fiscal_year = int(row["fy"]) if row.get("fy") is not None else period_end.year
    return RawFactRow(
        value=Decimal(str(metric_value)),
        unit=unit_name,
        period_start=period_start,
        period_end=period_end,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        form_type=form_type,
        filed_at=filed_at,
        accession_number=str(row["accn"]) if row.get("accn") else "",
        source_concept=tag,
        source_url=_source_url(cik, tag),
    )


def _row_period_start(row: dict[str, Any], period_end: date) -> date:
    if row.get("start"):
        return date.fromisoformat(str(row["start"]))
    return period_end


def _source_url(cik: str | None, concept: str) -> str:
    if cik is None:
        return f"sec-companyfacts://us-gaap/{concept}"
    return f"{_COMPANY_FACTS_URL.format(cik=cik)}#us-gaap/{concept}"


def _normalize_fiscal_period(raw_value: Any) -> Literal["FY", "Q1", "Q2", "Q3", "Q4"] | None:
    if raw_value is None:
        return None
    normalized = str(raw_value).upper()
    if normalized in {"FY", "Q1", "Q2", "Q3", "Q4"}:
        return normalized
    return None


def _is_supported_form(form_type: str) -> bool:
    return form_type in {"10-K", "10-K/A", "10-Q", "10-Q/A"}


def _is_annual_form(form_type: str) -> bool:
    return form_type in {"10-K", "10-K/A"}


def _is_quarterly_form(form_type: str) -> bool:
    return form_type in {"10-Q", "10-Q/A"}


def _select_authoritative_raw_rows(rows: list[RawFactRow]) -> list[RawFactRow]:
    winners: dict[tuple[str | None, int | None, str, date], RawFactRow] = {}
    for row in rows:
        key = (row.unit, row.fiscal_year, row.fiscal_period, row.period_end)
        current = winners.get(key)
        if current is None or _raw_row_sort_key(row) > _raw_row_sort_key(current):
            winners[key] = row
    return list(winners.values())


def _raw_row_sort_key(row: RawFactRow) -> tuple[date, str, str]:
    return (
        row.filed_at or date.min,
        row.accession_number or "",
        row.source_url,
    )


def _reported_rows_to_seeds(
    metric_name: str,
    rows: list[RawFactRow],
    *,
    is_derived: bool,
    derivation: str | None,
) -> list[ObservationSeed]:
    return [
        ObservationSeed(
            metric_name=metric_name,
            value=row.value,
            unit=row.unit,
            period_start=row.period_start,
            period_end=row.period_end,
            fiscal_year=row.fiscal_year,
            fiscal_period=row.fiscal_period,
            form_type=row.form_type,
            filed_at=row.filed_at,
            accession_number=row.accession_number,
            source_concept=row.source_concept,
            source_url=row.source_url,
            is_derived=is_derived,
            derivation=derivation,
        )
        for row in rows
    ]


def _derive_quarterly_duration_seeds(
    metric_name: str,
    annual_rows: list[RawFactRow],
    quarterly_rows: list[RawFactRow],
    cik: str | None,
) -> list[ObservationSeed]:
    grouped_quarterly = _rows_by_fiscal_year(quarterly_rows)
    annual_by_year = {
        (row.fiscal_year, row.unit, row.source_concept): row
        for row in annual_rows
    }
    observations: list[ObservationSeed] = []

    for rows in grouped_quarterly.values():
        quarter_by_period = {row.fiscal_period: row for row in rows}
        q1 = quarter_by_period.get("Q1")
        q2 = quarter_by_period.get("Q2")
        q3 = quarter_by_period.get("Q3")
        if q1 is not None:
            observations.extend(_reported_rows_to_seeds(metric_name, [q1], is_derived=False, derivation=None))
        if q1 is not None and q2 is not None and _same_series(q1, q2):
            observations.append(_subtract_rows(metric_name, "Q2", q2, q1, cik))
        if q2 is not None and q3 is not None and _same_series(q2, q3):
            observations.append(_subtract_rows(metric_name, "Q3", q3, q2, cik))
        if q3 is not None:
            annual = annual_by_year.get((q3.fiscal_year, q3.unit, q3.source_concept))
            if annual is not None:
                observations.append(_subtract_rows(metric_name, "Q4", annual, q3, cik))
    return observations


def _derive_q4_instant_seeds(
    metric_name: str,
    annual_rows: list[RawFactRow],
    cik: str | None,
) -> list[ObservationSeed]:
    observations: list[ObservationSeed] = []
    for annual in annual_rows:
        observations.append(
            ObservationSeed(
                metric_name=metric_name,
                value=annual.value,
                unit=annual.unit,
                period_start=annual.period_end,
                period_end=annual.period_end,
                fiscal_year=annual.fiscal_year,
                fiscal_period="Q4",
                form_type=annual.form_type,
                filed_at=annual.filed_at,
                accession_number=annual.accession_number,
                source_concept=annual.source_concept,
                source_url=annual.source_url if cik is None else annual.source_url,
                is_derived=True,
                derivation=f"Q4 point-in-time derived from FY {annual.source_concept}",
            )
        )
    return observations


def _rows_by_fiscal_year(rows: list[RawFactRow]) -> dict[tuple[int | None, str | None, str], list[RawFactRow]]:
    grouped: dict[tuple[int | None, str | None, str], list[RawFactRow]] = {}
    for row in rows:
        key = (row.fiscal_year, row.unit, row.source_concept)
        grouped.setdefault(key, []).append(row)
    return grouped


def _same_series(left: RawFactRow, right: RawFactRow) -> bool:
    return (
        left.fiscal_year == right.fiscal_year
        and left.unit == right.unit
        and left.source_concept == right.source_concept
    )


def _subtract_rows(
    metric_name: str,
    fiscal_period: Literal["Q2", "Q3", "Q4"],
    current_row: RawFactRow,
    previous_row: RawFactRow,
    cik: str | None,
) -> ObservationSeed:
    return ObservationSeed(
        metric_name=metric_name,
        value=current_row.value - previous_row.value,
        unit=current_row.unit,
        period_start=previous_row.period_end + timedelta(days=1),
        period_end=current_row.period_end,
        fiscal_year=current_row.fiscal_year,
        fiscal_period=fiscal_period,
        form_type=current_row.form_type,
        filed_at=current_row.filed_at,
        accession_number=current_row.accession_number,
        source_concept=current_row.source_concept,
        source_url=current_row.source_url if cik is None else current_row.source_url,
        is_derived=True,
        derivation=(
            f"{metric_name} {fiscal_period} derived from "
            f"{_raw_row_key(current_row)} - {_raw_row_key(previous_row)}"
        ),
    )


def _derive_total_debt_from_components(
    debt_rows: list[RawFactRow],
    cik: str | None,
) -> list[ObservationSeed]:
    rows_by_period: dict[tuple[int | None, str, date, str | None], list[RawFactRow]] = {}
    for row in debt_rows:
        key = (row.fiscal_year, row.fiscal_period, row.period_end, row.unit)
        rows_by_period.setdefault(key, []).append(row)

    observations: list[ObservationSeed] = []
    for rows in rows_by_period.values():
        if not rows:
            continue
        latest_row = max(rows, key=_raw_row_sort_key)
        value = sum((row.value for row in rows), Decimal("0"))
        observations.append(
            ObservationSeed(
                metric_name="total_debt",
                value=value,
                unit=latest_row.unit,
                period_start=latest_row.period_start,
                period_end=latest_row.period_end,
                fiscal_year=latest_row.fiscal_year,
                fiscal_period=latest_row.fiscal_period,
                form_type=latest_row.form_type,
                filed_at=latest_row.filed_at,
                accession_number=latest_row.accession_number,
                source_concept="+".join(sorted({row.source_concept for row in rows})),
                source_url=latest_row.source_url if cik is None else latest_row.source_url,
                is_derived=True,
                derivation=(
                    "total_debt derived from "
                    + ", ".join(sorted(_raw_row_key(row) for row in rows))
                ),
            )
        )
    return observations


def _derive_total_debt_from_liabilities(
    liabilities_rows: list[RawFactRow],
    debt_rows: list[RawFactRow],
    cik: str | None,
) -> list[ObservationSeed]:
    debt_periods = {
        (row.fiscal_year, row.fiscal_period, row.period_end, row.unit)
        for row in debt_rows
    }
    observations: list[ObservationSeed] = []
    for row in liabilities_rows:
        key = (row.fiscal_year, row.fiscal_period, row.period_end, row.unit)
        if key in debt_periods:
            continue
        observations.append(
            ObservationSeed(
                metric_name="total_debt",
                value=row.value,
                unit=row.unit,
                period_start=row.period_start,
                period_end=row.period_end,
                fiscal_year=row.fiscal_year,
                fiscal_period=row.fiscal_period,
                form_type=row.form_type,
                filed_at=row.filed_at,
                accession_number=row.accession_number,
                source_concept=row.source_concept,
                source_url=row.source_url if cik is None else row.source_url,
                is_derived=True,
                derivation=f"total_debt fallback derived from {_raw_row_key(row)}",
            )
        )
    return observations


def _derive_growth_metric(
    metric_name: str,
    observations: list[ObservationSeed],
) -> list[ObservationSeed]:
    grouped = _authoritative_by_metric_period(observations)
    derived: list[ObservationSeed] = []
    for current in grouped.values():
        if current.fiscal_year is None:
            continue
        previous = grouped.get((current.metric_name, current.fiscal_period, current.fiscal_year - 1))
        if previous is None or previous.value == 0 or current.unit != previous.unit:
            continue
        derived.append(
            _derived_seed(
                metric_name=metric_name,
                value=(current.value - previous.value) / previous.value,
                unit="ratio",
                current=current,
                peers=(current, previous),
                derivation=f"{metric_name} derived from {_seed_key(current)} vs {_seed_key(previous)}",
            )
        )
    return derived


def _derive_growth_delta(
    metric_name: str,
    observations: list[ObservationSeed],
) -> list[ObservationSeed]:
    grouped = _authoritative_by_metric_period(observations)
    derived: list[ObservationSeed] = []
    for current in grouped.values():
        if current.fiscal_year is None:
            continue
        previous = grouped.get((current.metric_name, current.fiscal_period, current.fiscal_year - 1))
        if previous is None or current.unit != previous.unit:
            continue
        derived.append(
            _derived_seed(
                metric_name=metric_name,
                value=current.value - previous.value,
                unit=current.unit,
                current=current,
                peers=(current, previous),
                derivation=f"{metric_name} derived from {_seed_key(current)} - {_seed_key(previous)}",
            )
        )
    return derived


def _derive_margin_metric(
    metric_name: str,
    numerators: list[ObservationSeed],
    denominators: list[ObservationSeed],
) -> list[ObservationSeed]:
    numerator_by_period = _authoritative_by_period(numerators)
    denominator_by_period = _authoritative_by_period(denominators)
    derived: list[ObservationSeed] = []
    for key, numerator in numerator_by_period.items():
        denominator = denominator_by_period.get(key)
        if denominator is None or denominator.value == 0 or numerator.unit != denominator.unit:
            continue
        derived.append(
            _derived_seed(
                metric_name=metric_name,
                value=numerator.value / denominator.value,
                unit="ratio",
                current=numerator,
                peers=(numerator, denominator),
                derivation=f"{metric_name} derived from {_seed_key(numerator)} / {_seed_key(denominator)}",
            )
        )
    return derived


def _derive_free_cash_flow(
    operating_cash_flow: list[ObservationSeed],
    capex: list[ObservationSeed],
) -> list[ObservationSeed]:
    operating_by_period = _authoritative_by_period(operating_cash_flow)
    capex_by_period = _authoritative_by_period(capex)
    derived: list[ObservationSeed] = []
    for key, cash_flow in operating_by_period.items():
        capex = capex_by_period.get(key)
        if capex is None or cash_flow.unit != capex.unit:
            continue
        derived.append(
            _derived_seed(
                metric_name="free_cash_flow",
                value=cash_flow.value - capex.value,
                unit=cash_flow.unit,
                current=cash_flow,
                peers=(cash_flow, capex),
                derivation=f"free_cash_flow derived from {_seed_key(cash_flow)} - {_seed_key(capex)}",
            )
        )
    return derived


def _derive_ratio_metric(
    metric_name: str,
    numerators: list[ObservationSeed],
    denominators: list[ObservationSeed],
) -> list[ObservationSeed]:
    numerator_by_period = _authoritative_by_period(numerators)
    denominator_by_period = _authoritative_by_period(denominators)
    derived: list[ObservationSeed] = []
    for key, numerator in numerator_by_period.items():
        denominator = denominator_by_period.get(key)
        if denominator is None or denominator.value == 0 or numerator.unit != denominator.unit:
            continue
        derived.append(
            _derived_seed(
                metric_name=metric_name,
                value=numerator.value / denominator.value,
                unit="ratio",
                current=numerator,
                peers=(numerator, denominator),
                derivation=f"{metric_name} derived from {_seed_key(numerator)} / {_seed_key(denominator)}",
            )
        )
    return derived


def _authoritative_by_metric_period(
    observations: list[ObservationSeed],
) -> dict[tuple[str, str, int], ObservationSeed]:
    grouped: dict[tuple[str, str, int], ObservationSeed] = {}
    for observation in observations:
        if observation.fiscal_year is None:
            continue
        key = (observation.metric_name, observation.fiscal_period, observation.fiscal_year)
        current = grouped.get(key)
        if current is None or _seed_sort_key(observation) > _seed_sort_key(current):
            grouped[key] = observation
    return grouped


def _authoritative_by_period(
    observations: list[ObservationSeed],
) -> dict[tuple[str, date, int | None], ObservationSeed]:
    grouped: dict[tuple[str, date, int | None], ObservationSeed] = {}
    for observation in observations:
        key = (observation.fiscal_period, observation.period_end, observation.fiscal_year)
        current = grouped.get(key)
        if current is None or _seed_sort_key(observation) > _seed_sort_key(current):
            grouped[key] = observation
    return grouped


def _derived_seed(
    *,
    metric_name: str,
    value: Decimal,
    unit: str | None,
    current: ObservationSeed,
    peers: tuple[ObservationSeed, ...],
    derivation: str,
) -> ObservationSeed:
    latest_source = max(peers, key=_seed_sort_key)
    return ObservationSeed(
        metric_name=metric_name,
        value=value,
        unit=unit,
        period_start=current.period_start,
        period_end=current.period_end,
        fiscal_year=current.fiscal_year,
        fiscal_period=current.fiscal_period,
        form_type=current.form_type,
        filed_at=latest_source.filed_at,
        accession_number=latest_source.accession_number,
        source_concept=f"derived:{metric_name}",
        source_url=latest_source.source_url,
        is_derived=True,
        derivation=derivation,
    )


def _authoritative_seeds(observations: list[ObservationSeed]) -> list[ObservationSeed]:
    winners: dict[tuple[str, str, date], ObservationSeed] = {}
    for observation in observations:
        key = (observation.metric_name, observation.fiscal_period, observation.period_end)
        current = winners.get(key)
        if current is None or _seed_sort_key(observation) > _seed_sort_key(current):
            winners[key] = observation
    return sorted(
        winners.values(),
        key=lambda observation: (
            observation.metric_name,
            observation.fiscal_period,
            observation.period_end,
        ),
    )


def _seed_sort_key(observation: ObservationSeed) -> tuple[date, str, str]:
    return (
        observation.filed_at or date.min,
        observation.accession_number or "",
        observation.source_url,
    )


def _group_by_metric(
    observations: list[ObservationSeed],
) -> dict[str, list[ObservationSeed]]:
    grouped: dict[str, list[ObservationSeed]] = {}
    for observation in observations:
        grouped.setdefault(observation.metric_name, []).append(observation)
    return grouped


def _seed_key(observation: ObservationSeed) -> str:
    return (
        f"{observation.metric_name}:{observation.fiscal_period}:{observation.period_end.isoformat()}:"
        f"{observation.accession_number or 'na'}"
    )


def _raw_row_key(row: RawFactRow) -> str:
    return (
        f"{row.source_concept}:{row.fiscal_period}:{row.period_end.isoformat()}:"
        f"{row.accession_number or 'na'}"
    )


def _observation_value(observation: FundamentalObservation | None) -> Decimal | None:
    if observation is None:
        return None
    return observation.value


def _latest_annual_observations_by_metric(
    observations: list[FundamentalObservation],
) -> dict[str, FundamentalObservation]:
    winners: dict[str, FundamentalObservation] = {}
    for observation in observations:
        if observation.fiscal_period != "FY":
            continue
        current = winners.get(observation.metric_name)
        if current is None or _persisted_observation_sort_key(observation) > _persisted_observation_sort_key(current):
            winners[observation.metric_name] = observation
    return winners


def _persisted_observation_sort_key(
    observation: FundamentalObservation,
) -> tuple[date, date, str, str]:
    return (
        observation.period_end,
        observation.filed_at or date.min,
        observation.accession_number or "",
        observation.source_url,
    )


def _resolve_cik(payload: dict[str, Any]) -> str | None:
    cik = payload.get("cik")
    if cik is None:
        return None
    cik_text = str(cik)
    if cik_text.startswith("CIK"):
        cik_text = cik_text[3:]
    return cik_text.zfill(10)
