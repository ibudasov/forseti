from __future__ import annotations

from typing import Optional

from app.db.models import FundamentalObservation
from app.db.repository import list_fundamental_observations
from app.schemas.fundamentals import (
    FundamentalHistorySeries,
    FundamentalHistorySnapshot,
    FundamentalObservationPoint,
)


def build_fundamental_history(
    ticker: str,
    engine=None,
    metric_names: Optional[list[str]] = None,
) -> FundamentalHistorySnapshot:
    observations = list_fundamental_observations(
        ticker,
        authoritative_only=True,
        engine=engine,
    )
    series_by_metric: dict[str, list[FundamentalObservation]] = {}
    for observation in observations:
        if metric_names is not None and observation.metric_name not in metric_names:
            continue
        series_by_metric.setdefault(observation.metric_name, []).append(observation)

    return FundamentalHistorySnapshot(
        ticker=ticker.strip().upper(),
        series=[
            FundamentalHistorySeries(
                metric_name=metric_name,
                annual=[
                    _to_point(observation)
                    for observation in _sorted_history(metric_observations, fiscal_periods={"FY"})
                    if observation.fiscal_period == "FY"
                ],
                quarterly=[
                    _to_point(observation)
                    for observation in _sorted_history(
                        metric_observations,
                        fiscal_periods={"Q1", "Q2", "Q3", "Q4"},
                    )
                    if observation.fiscal_period in {"Q1", "Q2", "Q3", "Q4"}
                ],
            )
            for metric_name, metric_observations in sorted(series_by_metric.items())
        ],
    )


def _to_point(observation: FundamentalObservation) -> FundamentalObservationPoint:
    return FundamentalObservationPoint(
        metric_name=observation.metric_name,
        value=float(observation.value),
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


def _sorted_history(
    observations: list[FundamentalObservation],
    *,
    fiscal_periods: set[str],
) -> list[FundamentalObservation]:
    return sorted(
        [observation for observation in observations if observation.fiscal_period in fiscal_periods],
        key=lambda observation: (
            observation.period_end,
            observation.filed_at,
            observation.accession_number or "",
            observation.id or 0,
        ),
    )
