from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, Optional, Protocol, Sequence, TypeGuard

from app.db.models import DocumentChunk, SourceType
from app.rag.embedding import EmbeddingClient
from app.rag.retrieval import retrieve
from app.schemas.fundamentals import (
    EvidenceChunkInput,
    FundamentalAnalysisRequest,
    FundamentalAssessmentResponse,
    FundamentalAssessmentResult,
)
from app.schemas.technicals import (
    TechnicalAnalysisRequest,
    TechnicalAssessmentResponse,
    TechnicalAssessmentResult,
)
from app.schemas.tension import CrossDomainTension, TensionConflictType

logger = logging.getLogger(__name__)

TENSION_RETRIEVAL_QUESTION_ID = "cross_domain_tension"
DEFAULT_TENSION_DATE_WINDOW_DAYS = 90
MIN_TENSION_DATE_WINDOW_DAYS = 30
MAX_TENSION_DATE_WINDOW_DAYS = 180
MAX_TENSION_CHUNKS = 4
MAX_TENSION_CHARACTERS = 4_000
MAX_TENSION_RETRIEVAL_TOP_K = 8

_SIGNAL_POLARITY: dict[str, int] = {
    "strong_negative": -1,
    "negative": -1,
    "neutral": 0,
    "positive": 1,
    "strong_positive": 1,
}
_POSITIVE_SIGNALS = frozenset({"positive", "strong_positive"})
_PRIMARY_SOURCE_TYPES = frozenset(
    {
        SourceType.filing_business.value,
        SourceType.filing_risk.value,
        SourceType.filing_mda.value,
        SourceType.earnings_release.value,
    }
)


@dataclass(frozen=True)
class CrossDomainTensionResolution:
    tension: CrossDomainTension | None
    supplemental_chunks: tuple[EvidenceChunkInput, ...] = ()


class TensionRetriever(Protocol):
    def __call__(
        self,
        *,
        ticker: str,
        question: str,
        date_window_days: int,
        published_before: datetime,
        top_k: int,
        engine: object | None,
    ) -> list[DocumentChunk]:
        """Return candidate DocumentChunks for a cross-domain tension lookup."""


def detect_cross_domain_tension(
    *,
    ticker: str,
    fundamental_assessment: FundamentalAssessmentResult | None,
    technical_assessment: TechnicalAssessmentResult | None,
    technical_request: TechnicalAnalysisRequest | None = None,
    fundamental_request: FundamentalAnalysisRequest | None = None,
) -> CrossDomainTension | None:
    """Deterministically detect cross-domain tension between completed analyst assessments."""
    del fundamental_request
    if not _assessments_ready(fundamental_assessment, technical_assessment):
        return None
    assert fundamental_assessment is not None and technical_assessment is not None
    fund_resp = fundamental_assessment.response
    tech_resp = technical_assessment.response
    risk_flag_categories = _high_risk_flag_categories(tech_resp)
    conflict_type = _classify_conflict(
        fund_signal=fund_resp.overall_signal,
        tech_signal=tech_resp.overall_signal,
        has_high_risk_flag=bool(risk_flag_categories),
    )
    if conflict_type is None:
        return None

    normalized_ticker = ticker.strip().upper()
    date_window_days = _anchor_date_window_days(technical_request)
    retrieval_query = _build_retrieval_query(
        ticker=normalized_ticker,
        conflict_type=conflict_type,
        fund_resp=fund_resp,
        tech_resp=tech_resp,
        risk_flag_categories=risk_flag_categories,
    )
    summary = _build_tension_summary(
        ticker=normalized_ticker,
        conflict_type=conflict_type,
        fund_resp=fund_resp,
        tech_resp=tech_resp,
        risk_flag_categories=risk_flag_categories,
    )
    return CrossDomainTension(
        conflict_type=conflict_type,
        fundamental_signal=fund_resp.overall_signal,
        technical_signal=tech_resp.overall_signal,
        technical_trend_state=tech_resp.trend_state,
        technical_momentum_state=tech_resp.momentum_state,
        fundamental_metric_ids=_fundamental_metric_ids(fund_resp),
        fundamental_chunk_ids=_fundamental_chunk_ids(fund_resp),
        technical_metric_ids=_technical_metric_ids(tech_resp),
        technical_risk_flag_categories=risk_flag_categories,
        retrieval_query=retrieval_query,
        date_window_days=date_window_days,
        supplemental_chunk_ids=[],
        summary=summary,
    )


def resolve_cross_domain_tension(
    *,
    ticker: str,
    snapshot_at: datetime,
    fundamental_assessment: FundamentalAssessmentResult | None,
    technical_assessment: TechnicalAssessmentResult | None,
    fundamental_request: FundamentalAnalysisRequest | None = None,
    technical_request: TechnicalAnalysisRequest | None = None,
    embedding_client: Optional[EmbeddingClient] = None,
    engine: object | None = None,
    retriever: TensionRetriever | None = None,
) -> CrossDomainTensionResolution:
    """Detect tension and execute at most one point-in-time safe pgvector lookup."""
    tension = detect_cross_domain_tension(
        ticker=ticker,
        fundamental_assessment=fundamental_assessment,
        technical_assessment=technical_assessment,
        technical_request=technical_request,
        fundamental_request=fundamental_request,
    )
    if tension is None:
        return CrossDomainTensionResolution(tension=None, supplemental_chunks=())

    candidates = _retrieve_tension_candidates(
        ticker=ticker.strip().upper(),
        tension=tension,
        snapshot_at=snapshot_at,
        embedding_client=embedding_client,
        engine=engine,
        retriever=retriever,
    )
    existing_ids = (
        {chunk.chunk_id for chunk in fundamental_request.evidence_chunks}
        if fundamental_request is not None
        else set()
    )
    supplemental = _select_supplemental_chunks(
        candidates,
        snapshot_at=snapshot_at,
        existing_chunk_ids=existing_ids,
    )
    updated_tension = tension.model_copy(
        update={"supplemental_chunk_ids": [chunk.chunk_id for chunk in supplemental]}
    )
    return CrossDomainTensionResolution(
        tension=updated_tension,
        supplemental_chunks=tuple(supplemental),
    )


def _assessments_ready(
    fundamental_assessment: FundamentalAssessmentResult | None,
    technical_assessment: TechnicalAssessmentResult | None,
) -> bool:
    if fundamental_assessment is None or technical_assessment is None:
        return False
    if not fundamental_assessment.validation.accepted or not technical_assessment.validation.accepted:
        return False
    return (
        fundamental_assessment.response.status == "completed"
        and technical_assessment.response.status == "completed"
    )


def _high_risk_flag_categories(tech_resp: TechnicalAssessmentResponse) -> list[str]:
    return sorted(
        {
            flag.category
            for flag in tech_resp.risk_flags
            if flag.materiality == "high"
        }
    )


def _classify_conflict(
    *,
    fund_signal: str,
    tech_signal: str,
    has_high_risk_flag: bool,
) -> TensionConflictType | None:
    signals = {fund_signal, tech_signal}
    if "strong_negative" in signals and bool(signals & _POSITIVE_SIGNALS):
        return "signal_conflict"
    if _SIGNAL_POLARITY[fund_signal] * _SIGNAL_POLARITY[tech_signal] < 0:
        return "polarity_conflict"
    if fund_signal in _POSITIVE_SIGNALS and has_high_risk_flag:
        return "technical_risk_flag"
    return None


def _anchor_date_window_days(technical_request: TechnicalAnalysisRequest | None) -> int:
    if technical_request is None:
        return DEFAULT_TENSION_DATE_WINDOW_DAYS
    days_to_earnings = next(
        (
            fact.value
            for fact in technical_request.facts
            if fact.name == "days_to_earnings" and fact.value is not None
        ),
        None,
    )
    if days_to_earnings is None:
        return DEFAULT_TENSION_DATE_WINDOW_DAYS
    bounded = max(MIN_TENSION_DATE_WINDOW_DAYS, min(MAX_TENSION_DATE_WINDOW_DAYS, abs(int(days_to_earnings)) + 60))
    return bounded


def _build_retrieval_query(
    *,
    ticker: str,
    conflict_type: TensionConflictType,
    fund_resp: FundamentalAssessmentResponse,
    tech_resp: TechnicalAssessmentResponse,
    risk_flag_categories: Sequence[str],
) -> str:
    categories = sorted(
        {
            *(finding.category for finding in fund_resp.findings),
            *(finding.category for finding in fund_resp.contradictions),
            *(finding.category for finding in fund_resp.material_red_flags),
            *(finding.category for finding in tech_resp.findings),
            *(finding.category for finding in tech_resp.contradictions),
            *risk_flag_categories,
        }
    )
    category_clause = " ".join(categories[:6]) if categories else "risks catalysts guidance"
    if conflict_type == "technical_risk_flag":
        return (
            f"{ticker} {' '.join(risk_flag_categories)} risks catalysts guidance "
            f"{tech_resp.trend_state} {tech_resp.momentum_state} {category_clause}"
        ).strip()
    return (
        f"{ticker} {conflict_type.replace('_', ' ')} fundamental {fund_resp.overall_signal} "
        f"technical {tech_resp.overall_signal} {tech_resp.trend_state} {category_clause}"
    ).strip()


def _build_tension_summary(
    *,
    ticker: str,
    conflict_type: TensionConflictType,
    fund_resp: FundamentalAssessmentResponse,
    tech_resp: TechnicalAssessmentResponse,
    risk_flag_categories: Sequence[str],
) -> str:
    if conflict_type == "technical_risk_flag":
        categories = ", ".join(risk_flag_categories) or "technical_risk"
        return (
            f"{ticker}: fundamental_analyst is {fund_resp.overall_signal} while "
            f"technical_analyst flagged high-materiality {categories} risk "
            f"({tech_resp.trend_state}/{tech_resp.momentum_state})."
        )
    return (
        f"{ticker}: fundamental_analyst ({fund_resp.overall_signal}) conflicts with "
        f"technical_analyst ({tech_resp.overall_signal}, "
        f"{tech_resp.trend_state}/{tech_resp.momentum_state})."
    )


def _fundamental_metric_ids(fund_resp: FundamentalAssessmentResponse) -> list[str]:
    findings = (*fund_resp.findings, *fund_resp.contradictions, *fund_resp.material_red_flags)
    return sorted({metric_id for finding in findings for metric_id in finding.metric_ids})


def _fundamental_chunk_ids(fund_resp: FundamentalAssessmentResponse) -> list[int]:
    findings = (*fund_resp.findings, *fund_resp.contradictions, *fund_resp.material_red_flags)
    return sorted({chunk_id for finding in findings for chunk_id in finding.chunk_ids})


def _technical_metric_ids(tech_resp: TechnicalAssessmentResponse) -> list[str]:
    findings = (*tech_resp.findings, *tech_resp.contradictions, *tech_resp.risk_flags)
    return sorted({metric_id for finding in findings for metric_id in finding.metric_ids})


def _can_embed(embedding_client: object | None) -> TypeGuard[EmbeddingClient]:
    return embedding_client is not None and callable(getattr(embedding_client, "embed_texts", None))


def _retrieve_tension_candidates(
    *,
    ticker: str,
    tension: CrossDomainTension,
    snapshot_at: datetime,
    embedding_client: Optional[EmbeddingClient],
    engine: object | None,
    retriever: TensionRetriever | None,
) -> list[DocumentChunk]:
    if retriever is not None:
        try:
            return retriever(
                ticker=ticker,
                question=tension.retrieval_query,
                date_window_days=tension.date_window_days,
                published_before=snapshot_at,
                top_k=MAX_TENSION_RETRIEVAL_TOP_K,
                engine=engine,
            )
        except Exception as exc:
            logger.warning("cross_domain_tension_lookup_failed ticker=%s error=%s", ticker, exc)
            return []
    if not _can_embed(embedding_client):
        return []
    try:
        return retrieve(
            ticker=ticker,
            question=tension.retrieval_query,
            embedding_client=embedding_client,
            top_k=MAX_TENSION_RETRIEVAL_TOP_K,
            date_window_days=tension.date_window_days,
            published_before=snapshot_at,
            engine=engine,
        )
    except Exception as exc:
        logger.warning("cross_domain_tension_lookup_failed ticker=%s error=%s", ticker, exc)
        return []


def _select_supplemental_chunks(
    candidates: Sequence[DocumentChunk],
    *,
    snapshot_at: datetime,
    existing_chunk_ids: set[int],
) -> list[EvidenceChunkInput]:
    selected: list[EvidenceChunkInput] = []
    seen_ids = set(existing_chunk_ids)
    total_chars = 0
    for chunk in candidates:
        if len(selected) >= MAX_TENSION_CHUNKS:
            break
        chunk_id = chunk.id
        if chunk_id is None or chunk_id in seen_ids:
            continue
        if not _is_published_before_snapshot(chunk, snapshot_at):
            continue
        remaining_chars = MAX_TENSION_CHARACTERS - total_chars
        chunk_text = _bounded_chunk_text(chunk.text, remaining_chars)
        if not chunk_text:
            continue
        seen_ids.add(chunk_id)
        total_chars += len(chunk_text)
        selected.append(
            EvidenceChunkInput(
                chunk_id=chunk_id,
                retrieval_question_id=TENSION_RETRIEVAL_QUESTION_ID,
                source_type=_enum_value(chunk.source_type),
                source_url=chunk.source_url,
                source_hash=chunk.source_hash,
                chunk_index=chunk.chunk_index,
                published_at=chunk.published_at,
                quality_tier=_quality_tier(chunk.source_type),
                text=chunk_text,
            )
        )
    return selected


def _bounded_chunk_text(text: str, remaining_chars: int) -> str:
    if remaining_chars <= 0:
        return ""
    if len(text) <= remaining_chars:
        return text
    if remaining_chars <= 1:
        return ""
    return text[: remaining_chars - 1] + "…"


def _is_published_before_snapshot(chunk: DocumentChunk, snapshot_at: datetime) -> bool:
    if chunk.published_at is None:
        return True
    published_at = chunk.published_at
    if published_at.tzinfo is None and snapshot_at.tzinfo is not None:
        published_at = published_at.replace(tzinfo=snapshot_at.tzinfo)
    elif published_at.tzinfo is not None and snapshot_at.tzinfo is None:
        snapshot_at = snapshot_at.replace(tzinfo=published_at.tzinfo)
    return published_at <= snapshot_at


def _quality_tier(source_type: SourceType | str) -> Literal["primary", "secondary", "tertiary"]:
    value = _enum_value(source_type)
    if value in _PRIMARY_SOURCE_TYPES:
        return "primary"
    if value == SourceType.earnings_call_transcript.value:
        return "secondary"
    return "tertiary"


def _enum_value(value: SourceType | Enum | str) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
