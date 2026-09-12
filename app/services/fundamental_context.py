from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone, timedelta
from enum import Enum
from typing import Callable, Optional, Sequence

from app.db.models import DocumentChunk, FundamentalObservation, SourceType
from app.db.repository import get_security, list_fundamental_observations, list_fundamentals
from app.domain.fundamentals import (
    FUNDAMENTAL_METRIC_DEFINITIONS,
    DeterministicFundamentalAnalysis,
    FundamentalSnapshotData,
    analyze_fundamentals,
)
from app.rag.embedding import EmbeddingClient, MockEmbeddingClient, VertexAIEmbeddingClient
from app.rag.retrieval import retrieve
from app.schemas.fundamentals import (
    EvidenceChunkInput,
    EvidenceQuestionCoverage,
    FundamentalAnalysisRequest,
    FundamentalContextCoverage,
    MetricSeries,
    MetricSeriesPoint,
)
from app.settings import get_settings

MAX_ANNUAL_PERIODS = 5
MAX_QUARTERLY_PERIODS = 8
MAX_CHUNKS_PER_QUESTION = 3
MAX_TOTAL_EVIDENCE_CHARACTERS = 12_000
EVIDENCE_STALE_DAYS = 180


@dataclass(frozen=True)
class EvidenceQuestion:
    question_id: str
    prompt: str
    source_types: tuple[SourceType, ...]


EVIDENCE_QUESTIONS: tuple[EvidenceQuestion, ...] = (
    EvidenceQuestion(
        question_id="growth_sustainability",
        prompt="What explains recent revenue growth or decline, and is it sustainable?",
        source_types=(SourceType.filing_business, SourceType.earnings_call, SourceType.company_news),
    ),
    EvidenceQuestion(
        question_id="cash_flow_and_margins",
        prompt="What explains cash-flow and margin movements?",
        source_types=(SourceType.filing_business, SourceType.earnings_call, SourceType.company_news),
    ),
    EvidenceQuestion(
        question_id="concentration_risks",
        prompt="Are there customer, supplier, geographic, regulatory, or product concentration risks?",
        source_types=(SourceType.filing_risk, SourceType.company_news, SourceType.sector_news),
    ),
    EvidenceQuestion(
        question_id="guidance_vs_history",
        prompt="What does management guidance imply relative to historical trends?",
        source_types=(SourceType.earnings_call, SourceType.company_news, SourceType.filing_business),
    ),
    EvidenceQuestion(
        question_id="one_offs_and_accounting",
        prompt="Are there one-off items or accounting effects that distort EPS or free cash flow?",
        source_types=(SourceType.filing_risk, SourceType.earnings_call, SourceType.company_news),
    ),
    EvidenceQuestion(
        question_id="liquidity_and_dilution",
        prompt="What liquidity, debt-maturity, dilution, or capex risks are material?",
        source_types=(SourceType.filing_risk, SourceType.filing_business, SourceType.company_news),
    ),
)

RetrieveEvidenceChunks = Callable[[str, str, Sequence[SourceType], int], list[DocumentChunk]]


def build_fundamental_analysis_request(
    ticker: str,
    run_id: str,
    as_of: date | None = None,
    *,
    engine=None,
    retriever: RetrieveEvidenceChunks | None = None,
    embedding_client: Optional[EmbeddingClient] = None,
    snapshot_at: datetime | None = None,
) -> FundamentalAnalysisRequest:
    normalized_ticker = ticker.strip().upper()
    as_of_date = as_of or datetime.now(timezone.utc).date()
    context_snapshot_at = snapshot_at or _snapshot_at(as_of_date)
    security = get_security(normalized_ticker, engine=engine)
    if security is None:
        raise ValueError(f"Unknown ticker: {normalized_ticker}")

    deterministic_result = _build_deterministic_result(
        normalized_ticker,
        as_of_date=as_of_date,
        currency=security.currency or "USD",
        engine=engine,
    )
    observations = _observations_as_of(normalized_ticker, as_of_date=as_of_date, engine=engine)
    metric_series, metric_series_truncated = _build_metric_series(observations)
    selected_chunks, question_coverage, evidence_truncated = _build_evidence_pack(
        normalized_ticker,
        snapshot_at=context_snapshot_at,
        engine=engine,
        retriever=retriever or _default_retriever(engine=engine, embedding_client=embedding_client),
    )
    coverage = _build_coverage(
        deterministic_result=deterministic_result,
        metric_series=metric_series,
        evidence_chunks=selected_chunks,
        question_coverage=question_coverage,
        metric_series_truncated=metric_series_truncated,
        evidence_truncated=evidence_truncated,
        snapshot_at=context_snapshot_at,
    )
    request = FundamentalAnalysisRequest(
        run_id=run_id,
        context_hash="pending",
        ticker=normalized_ticker,
        company_name=security.name,
        sector=_enum_value(security.sector_tag),
        currency=security.currency or "USD",
        snapshot_at=context_snapshot_at,
        as_of_date=as_of_date,
        deterministic_result=deterministic_result,
        metric_series=metric_series,
        evidence_chunks=selected_chunks,
        coverage=coverage,
    )
    return request.model_copy(update={"context_hash": _context_hash(request)})


def _build_deterministic_result(
    ticker: str,
    *,
    as_of_date: date,
    currency: str,
    engine,
) -> DeterministicFundamentalAnalysis:
    latest_fundamental = next(
        (
            row
            for row in list_fundamentals(engine=engine, ticker=ticker)
            if row.as_of_date <= as_of_date
        ),
        None,
    )
    snapshot = FundamentalSnapshotData(
        has_snapshot=latest_fundamental is not None,
        as_of_date=latest_fundamental.as_of_date if latest_fundamental is not None else None,
        revenue_growth=latest_fundamental.revenue_growth if latest_fundamental is not None else None,
        fcf=latest_fundamental.fcf if latest_fundamental is not None else None,
        debt_to_equity=latest_fundamental.debt_to_equity if latest_fundamental is not None else None,
        eps_trend=latest_fundamental.eps_trend if latest_fundamental is not None else None,
        margins=latest_fundamental.margins if latest_fundamental is not None else None,
        currency=currency,
    )
    return analyze_fundamentals(ticker, snapshot)


def _observations_as_of(
    ticker: str,
    *,
    as_of_date: date,
    engine,
) -> list[FundamentalObservation]:
    observations = list_fundamental_observations(
        ticker,
        authoritative_only=True,
        engine=engine,
    )
    return [
        observation
        for observation in observations
        if observation.period_end <= as_of_date
        and (observation.filed_at is None or observation.filed_at <= as_of_date)
    ]


def _build_metric_series(
    observations: list[FundamentalObservation],
) -> tuple[list[MetricSeries], bool]:
    observations_by_metric: dict[str, list[FundamentalObservation]] = {}
    for observation in observations:
        observations_by_metric.setdefault(observation.metric_name, []).append(observation)

    metric_series: list[MetricSeries] = []
    truncated = False
    for metric_name in sorted(observations_by_metric):
        annual_points = _series_points(observations_by_metric[metric_name], fiscal_periods={"FY"})
        quarterly_points = _series_points(
            observations_by_metric[metric_name],
            fiscal_periods={"Q1", "Q2", "Q3", "Q4"},
        )
        annual_selected, annual_truncated = _limit_points(annual_points, MAX_ANNUAL_PERIODS)
        quarterly_selected, quarterly_truncated = _limit_points(quarterly_points, MAX_QUARTERLY_PERIODS)
        truncated = truncated or annual_truncated or quarterly_truncated
        metric_series.append(
            MetricSeries(
                metric_name=metric_name,
                annual=annual_selected,
                quarterly=quarterly_selected,
            )
        )
    return metric_series, truncated


def _series_points(
    observations: list[FundamentalObservation],
    *,
    fiscal_periods: set[str],
) -> list[MetricSeriesPoint]:
    eligible = sorted(
        (
            observation
            for observation in observations
            if observation.fiscal_period in fiscal_periods
        ),
        key=lambda observation: (
            observation.period_end,
            observation.filed_at or date.min,
            observation.accession_number or "",
            observation.id or 0,
        ),
    )
    return [
        MetricSeriesPoint(
            metric_id=_observation_metric_id(observation),
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
        for observation in eligible
    ]


def _limit_points(
    points: list[MetricSeriesPoint],
    limit: int,
) -> tuple[list[MetricSeriesPoint], bool]:
    if len(points) <= limit:
        return points, False
    return points[-limit:], True


def _build_evidence_pack(
    ticker: str,
    *,
    snapshot_at: datetime,
    engine,
    retriever: RetrieveEvidenceChunks,
) -> tuple[list[EvidenceChunkInput], list[EvidenceQuestionCoverage], bool]:
    selected_chunks: list[EvidenceChunkInput] = []
    selected_chunk_ids: set[int] = set()
    selected_characters = 0
    question_coverage: list[EvidenceQuestionCoverage] = []
    truncated = False

    for question in EVIDENCE_QUESTIONS:
        candidates = retriever(ticker, question.prompt, question.source_types, MAX_CHUNKS_PER_QUESTION * 2)
        eligible = _rank_chunks(
            [
                chunk
                for chunk in candidates
                if chunk.id is not None and _chunk_published_before_snapshot(chunk, snapshot_at)
            ]
        )
        selected_for_question: list[EvidenceChunkInput] = []
        question_truncated = len(eligible) > MAX_CHUNKS_PER_QUESTION

        for chunk in _prefer_source_diversity(eligible):
            if len(selected_for_question) >= MAX_CHUNKS_PER_QUESTION:
                break
            chunk_id = chunk.id
            if chunk_id is None:
                continue
            if chunk_id in selected_chunk_ids:
                continue
            remaining_characters = MAX_TOTAL_EVIDENCE_CHARACTERS - selected_characters
            if remaining_characters <= 0:
                truncated = True
                question_truncated = True
                break
            chunk_text, was_truncated = _fit_chunk_text(chunk.text, remaining_characters)
            if not chunk_text:
                truncated = True
                question_truncated = True
                break
            selected_characters += len(chunk_text)
            truncated = truncated or was_truncated
            question_truncated = question_truncated or was_truncated
            selected_chunk_ids.add(chunk_id)
            evidence_chunk = EvidenceChunkInput(
                chunk_id=chunk_id,
                retrieval_question_id=question.question_id,
                source_type=_enum_value(chunk.source_type),
                source_url=chunk.source_url,
                source_hash=chunk.source_hash,
                chunk_index=chunk.chunk_index,
                published_at=chunk.published_at,
                quality_tier=_quality_tier(chunk.source_type),
                text=chunk_text,
            )
            selected_chunks.append(evidence_chunk)
            selected_for_question.append(evidence_chunk)

        question_coverage.append(
            EvidenceQuestionCoverage(
                question_id=question.question_id,
                chunk_ids=[chunk.chunk_id for chunk in selected_for_question],
                source_types=sorted({chunk.source_type for chunk in selected_for_question}),
                truncated=question_truncated,
            )
        )

    return selected_chunks, question_coverage, truncated


def _prefer_source_diversity(chunks: list[DocumentChunk]) -> list[DocumentChunk]:
    first_pass: list[DocumentChunk] = []
    remaining: list[DocumentChunk] = []
    seen_source_types: set[str] = set()
    for chunk in chunks:
        source_type = _enum_value(chunk.source_type)
        if source_type not in seen_source_types:
            first_pass.append(chunk)
            seen_source_types.add(source_type)
            continue
        remaining.append(chunk)
    return first_pass + remaining


def _rank_chunks(chunks: list[DocumentChunk]) -> list[DocumentChunk]:
    return sorted(
        chunks,
        key=lambda chunk: (
            _quality_rank(chunk.source_type),
            -(int(chunk.published_at.timestamp()) if chunk.published_at is not None else -1),
            chunk.id or 0,
        ),
    )


def _quality_rank(source_type: SourceType | str) -> int:
    value = _enum_value(source_type)
    return {
        SourceType.filing_business.value: 0,
        SourceType.filing_risk.value: 0,
        SourceType.earnings_call.value: 1,
        SourceType.company_news.value: 2,
        SourceType.sector_news.value: 3,
    }[value]


def _quality_tier(source_type: SourceType | str) -> str:
    if _quality_rank(source_type) == 0:
        return "primary"
    if _quality_rank(source_type) == 1:
        return "secondary"
    return "tertiary"


def _fit_chunk_text(text: str, remaining_characters: int) -> tuple[str, bool]:
    if remaining_characters <= 0:
        return "", False
    if len(text) <= remaining_characters:
        return text, False
    if remaining_characters <= 1:
        return "", True
    return text[: remaining_characters - 1] + "…", True


def _chunk_published_before_snapshot(chunk: DocumentChunk, snapshot_at: datetime) -> bool:
    return chunk.published_at is None or chunk.published_at <= snapshot_at


def _build_coverage(
    *,
    deterministic_result: DeterministicFundamentalAnalysis,
    metric_series: list[MetricSeries],
    evidence_chunks: list[EvidenceChunkInput],
    question_coverage: list[EvidenceQuestionCoverage],
    metric_series_truncated: bool,
    evidence_truncated: bool,
    snapshot_at: datetime,
) -> FundamentalContextCoverage:
    metrics_in_request = {series.metric_name for series in metric_series}
    required_metrics = list(FUNDAMENTAL_METRIC_DEFINITIONS)
    newest_published_at = max(
        (chunk.published_at for chunk in evidence_chunks if chunk.published_at is not None),
        default=None,
    )
    source_types_present = sorted({chunk.source_type for chunk in evidence_chunks})
    all_source_types = sorted(source_type.value for source_type in SourceType)
    selected_character_count = sum(len(chunk.text) for chunk in evidence_chunks)

    return FundamentalContextCoverage(
        required_metrics_present=[
            metric_name for metric_name in required_metrics if metric_name in metrics_in_request
        ],
        required_metrics_missing=[
            metric_name for metric_name in required_metrics if metric_name not in metrics_in_request
        ],
        annual_period_counts={
            series.metric_name: len(series.annual)
            for series in metric_series
        },
        quarterly_period_counts={
            series.metric_name: len(series.quarterly)
            for series in metric_series
        },
        source_types_present=source_types_present,
        source_types_missing=[
            source_type for source_type in all_source_types if source_type not in source_types_present
        ],
        newest_evidence_published_at=newest_published_at,
        evidence_stale=_is_stale(newest_published_at, snapshot_at=snapshot_at),
        evidence_truncated=evidence_truncated,
        metric_series_truncated=metric_series_truncated,
        selected_chunk_count=len(evidence_chunks),
        selected_character_count=selected_character_count,
        question_coverage=question_coverage,
    )


def _is_stale(newest_published_at: datetime | None, *, snapshot_at: datetime) -> bool:
    if newest_published_at is None:
        return True
    return newest_published_at < snapshot_at - timedelta(days=EVIDENCE_STALE_DAYS)


def _context_hash(request: FundamentalAnalysisRequest) -> str:
    payload = request.model_dump(
        mode="json",
        exclude={"context_hash", "run_id"},
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot_at(as_of_date: date) -> datetime:
    return datetime.combine(as_of_date, time.max, tzinfo=timezone.utc)


def _observation_metric_id(observation: FundamentalObservation) -> str:
    accession = observation.accession_number or "na"
    unit = observation.unit or "none"
    return (
        f"{observation.metric_name}:"
        f"{observation.fiscal_period}:"
        f"{observation.period_end.isoformat()}:"
        f"{unit}:"
        f"{accession}"
    )


def _default_retriever(
    *,
    engine,
    embedding_client: Optional[EmbeddingClient] = None,
) -> RetrieveEvidenceChunks:
    client = embedding_client or _build_embedding_client()

    def run(
        ticker: str,
        question: str,
        source_types: Sequence[SourceType],
        top_k: int,
    ) -> list[DocumentChunk]:
        return retrieve(
            ticker=ticker,
            question=question,
            embedding_client=client,
            top_k=top_k,
            source_types=list(source_types),
            engine=engine,
        )

    return run


def _build_embedding_client() -> EmbeddingClient:
    settings = get_settings()
    if settings.VERTEX_AI_PROJECT:
        return VertexAIEmbeddingClient(
            project=settings.VERTEX_AI_PROJECT,
            location=settings.VERTEX_AI_LOCATION,
            model=settings.EMBEDDING_MODEL,
            dimension=settings.EMBEDDING_DIM,
        )
    return MockEmbeddingClient(dimension=settings.EMBEDDING_DIM)


def _enum_value(value: SourceType | Enum | str) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
