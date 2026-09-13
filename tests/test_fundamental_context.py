from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlmodel import Session

from app.db.models import (
    DocumentChunk,
    Fundamental,
    FundamentalObservation,
    Security,
    SourceQualityTier,
    SourceType,
)
from app.db.repository import upsert_fundamental, upsert_fundamental_observations
from app.services.fundamental_context import build_fundamental_analysis_request


def test_build_request_is_deterministic_for_same_snapshot(db_engine):
    _seed_security_with_fundamentals(db_engine)
    snapshot_at = datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc)
    retriever = _fake_retriever(
        [
            _chunk(
                10,
                SourceType.filing_business,
                "Primary growth evidence",
                published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            ),
            _chunk(
                11,
                SourceType.company_news,
                "Secondary risk evidence",
                published_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
            ),
        ]
    )

    first = build_fundamental_analysis_request(
        "ctx1",
        "run-a",
        as_of=date(2026, 9, 8),
        engine=db_engine,
        retriever=retriever,
        snapshot_at=snapshot_at,
    )
    second = build_fundamental_analysis_request(
        "CTX1",
        "run-b",
        as_of=date(2026, 9, 8),
        engine=db_engine,
        retriever=retriever,
        snapshot_at=snapshot_at,
    )

    assert first.context_hash == second.context_hash
    assert first.model_dump(exclude={"run_id"}, mode="json") == second.model_dump(
        exclude={"run_id"},
        mode="json",
    )
    revenue_series = next(series for series in first.metric_series if series.metric_name == "revenue")
    assert [point.fiscal_period for point in revenue_series.annual] == ["FY"]
    assert len(revenue_series.quarterly) == 8
    assert revenue_series.quarterly[0].period_end == date(2024, 6, 30)
    assert revenue_series.quarterly[-1].period_end == date(2026, 3, 31)
    assert first.coverage.metric_series_truncated is True
    assert first.coverage.required_metrics_present == [
        "revenue_growth",
        "fcf",
        "debt_to_equity",
        "eps_trend",
        "margins",
    ]


def test_build_request_filters_future_evidence_and_deduplicates_chunks(db_engine):
    _seed_security_with_fundamentals(db_engine)
    shared_chunk = _chunk(
        20,
        SourceType.filing_risk,
        "Shared risk evidence",
        published_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    future_chunk = _chunk(
        21,
        SourceType.company_news,
        "Future evidence that must be excluded",
        published_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    retriever = _fake_retriever([shared_chunk, future_chunk])

    request = build_fundamental_analysis_request(
        "CTX1",
        "run-risk",
        as_of=date(2026, 9, 8),
        engine=db_engine,
        retriever=retriever,
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
    )

    assert [chunk.chunk_id for chunk in request.evidence_chunks] == [20]
    assert request.coverage.selected_chunk_count == 1
    assert all(chunk.chunk_id != 21 for chunk in request.evidence_chunks)
    question_coverage = [entry.chunk_ids for entry in request.coverage.question_coverage]
    assert sum(chunk_ids.count(20) for chunk_ids in question_coverage) == 1


def test_build_request_reports_missing_data_without_leaking_payloads(db_engine):
    with Session(db_engine) as session:
        session.add(Security(ticker="MISS1", name="Missing One", exchange="NYSE", sector_tag="ai"))
        session.commit()

    request = build_fundamental_analysis_request(
        "MISS1",
        "run-missing",
        as_of=date(2026, 9, 8),
        engine=db_engine,
        retriever=_fake_retriever([]),
        snapshot_at=datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
    )

    assert request.deterministic_result.warnings == [
        "no_fundamental_snapshot",
        "missing_metric:revenue_growth",
        "missing_metric:fcf",
        "missing_metric:debt_to_equity",
        "missing_metric:eps_trend",
    ]
    assert request.coverage.required_metrics_missing == [
        "revenue_growth",
        "fcf",
        "debt_to_equity",
        "eps_trend",
        "margins",
    ]
    assert request.coverage.selected_chunk_count == 0
    assert request.coverage.evidence_stale is True

    payload = json.dumps(request.model_dump(mode="json"))
    assert "raw_payload" not in payload
    assert "embedding" not in payload


def _seed_security_with_fundamentals(db_engine) -> None:
    with Session(db_engine) as session:
        security = Security(ticker="CTX1", name="Context One", exchange="NASDAQ", sector_tag="ai")
        session.add(security)
        session.commit()
        session.refresh(security)

    upsert_fundamental(
        Fundamental(
            security_id=security.id,
            as_of_date=date(2026, 6, 30),
            revenue_growth=Decimal("0.21"),
            fcf=Decimal("1200"),
            debt_to_equity=Decimal("0.40"),
            eps_trend=Decimal("0.08"),
            margins=Decimal("0.32"),
            raw_payload={"secret": "ignore me"},
        ),
        engine=db_engine,
    )
    upsert_fundamental_observations(_observations(security.id), engine=db_engine)


def _observations(security_id: int) -> list[FundamentalObservation]:
    observations: list[FundamentalObservation] = []
    for quarter_index in range(1, 10):
        year = 2024 + ((quarter_index - 1) // 4)
        quarter = ((quarter_index - 1) % 4) + 1
        observations.append(
            FundamentalObservation(
                security_id=security_id,
                metric_name="revenue",
                value=Decimal(str(100 + quarter_index)),
                unit="USD",
                period_start=date(year, 1, 1),
                period_end=_quarter_end(year, quarter),
                fiscal_year=year,
                fiscal_period=f"Q{quarter}",
                form_type="10-Q",
                filed_at=_quarter_end(year, quarter),
                accession_number=f"rev-{quarter_index}",
                source_concept="Revenue",
                source_url=f"sec://revenue/{quarter_index}",
                is_derived=False,
            )
        )
    observations.extend(
        [
            FundamentalObservation(
                security_id=security_id,
                metric_name="revenue_growth",
                value=Decimal("0.21"),
                unit="ratio",
                period_start=date(2026, 1, 1),
                period_end=date(2026, 6, 30),
                fiscal_year=2026,
                fiscal_period="Q2",
                form_type="10-Q",
                filed_at=date(2026, 7, 25),
                accession_number="growth-1",
                source_concept="RevenueGrowth",
                source_url="sec://growth/1",
                is_derived=True,
                derivation="yoy_growth",
            ),
            FundamentalObservation(
                security_id=security_id,
                metric_name="fcf",
                value=Decimal("1200"),
                unit="USD",
                period_start=date(2026, 1, 1),
                period_end=date(2026, 6, 30),
                fiscal_year=2026,
                fiscal_period="Q2",
                form_type="10-Q",
                filed_at=date(2026, 7, 25),
                accession_number="fcf-1",
                source_concept="FreeCashFlow",
                source_url="sec://fcf/1",
                is_derived=True,
                derivation="operating_cash_flow-capex",
            ),
            FundamentalObservation(
                security_id=security_id,
                metric_name="debt_to_equity",
                value=Decimal("0.40"),
                unit="ratio",
                period_start=date(2026, 1, 1),
                period_end=date(2026, 6, 30),
                fiscal_year=2026,
                fiscal_period="Q2",
                form_type="10-Q",
                filed_at=date(2026, 7, 25),
                accession_number="de-1",
                source_concept="DebtToEquity",
                source_url="sec://de/1",
                is_derived=True,
                derivation="debt/equity",
            ),
            FundamentalObservation(
                security_id=security_id,
                metric_name="eps_trend",
                value=Decimal("0.08"),
                unit="currency_per_share_delta",
                period_start=date(2026, 1, 1),
                period_end=date(2026, 6, 30),
                fiscal_year=2026,
                fiscal_period="Q2",
                form_type="10-Q",
                filed_at=date(2026, 7, 25),
                accession_number="eps-1",
                source_concept="EpsTrend",
                source_url="sec://eps/1",
                is_derived=True,
                derivation="eps_yoy_delta",
            ),
            FundamentalObservation(
                security_id=security_id,
                metric_name="margins",
                value=Decimal("0.32"),
                unit="ratio",
                period_start=date(2026, 1, 1),
                period_end=date(2026, 6, 30),
                fiscal_year=2026,
                fiscal_period="Q2",
                form_type="10-Q",
                filed_at=date(2026, 7, 25),
                accession_number="margin-1",
                source_concept="Margins",
                source_url="sec://margin/1",
                is_derived=True,
                derivation="profit/revenue",
            ),
            FundamentalObservation(
                security_id=security_id,
                metric_name="revenue",
                value=Decimal("480"),
                unit="USD",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                fiscal_year=2025,
                fiscal_period="FY",
                form_type="10-K",
                filed_at=date(2026, 2, 15),
                accession_number="rev-fy-1",
                source_concept="Revenue",
                source_url="sec://revenue/fy-1",
                is_derived=False,
            ),
        ]
    )
    return observations


def _quarter_end(year: int, quarter: int) -> date:
    return {
        1: date(year, 3, 31),
        2: date(year, 6, 30),
        3: date(year, 9, 30),
        4: date(year, 12, 31),
    }[quarter]


def _chunk(
    chunk_id: int,
    source_type: SourceType,
    text: str,
    *,
    published_at: datetime,
) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        ticker="CTX1",
        source_type=source_type,
        document_id=f"doc-{chunk_id}",
        source_url=f"https://example.com/{chunk_id}",
        publisher="Example Publisher",
        title=f"Chunk {chunk_id}",
        source_quality_tier=SourceQualityTier.primary_regulatory,
        source_hash=f"hash-{chunk_id}",
        published_at=published_at,
        chunk_index=0,
        text=text,
        embedding=[0.0, 0.0],
    )


def _fake_retriever(chunks: list[DocumentChunk]):
    def run(ticker: str, question: str, source_types, top_k: int) -> list[DocumentChunk]:
        del ticker, question, source_types, top_k
        return list(chunks)

    return run
