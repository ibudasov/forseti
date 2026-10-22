from __future__ import annotations

from datetime import date
from typing import Literal, Sequence

from app.schemas.analyze import AnalyzeResponse, DecisionDiagnosis
from app.schemas.critique import AnalystFinding, AnalystView, CritiqueRequest, DraftRecommendation
from app.schemas.fundamentals import EvidenceChunkInput, FundamentalAssessmentResult
from app.schemas.technicals import TechnicalAssessmentResult
from app.services.context_hash import compute_context_hash
from app.services.fundamental_context import build_fundamental_analysis_request

MAX_EVIDENCE_CHUNKS = 24
MAX_EVIDENCE_CHARACTERS = 60_000
DEFAULT_ALLOWED_ACTIONS = (
    "accept",
    "downgrade_confidence",
    "downgrade_decision",
    "force_no_trade",
    "request_revision",
)


def build_draft_from_response(
    response: AnalyzeResponse,
    *,
    memo: str = "",
    source: Literal["deterministic", "decision_synthesizer"] = "deterministic",
) -> DraftRecommendation:
    return DraftRecommendation(
        decision=response.decision,
        confidence=response.confidence,
        reasons=list(response.reasons),
        warnings=list(response.warnings),
        entry_range=response.entry_range,
        stop_loss=response.stop_loss,
        take_profit=response.take_profit,
        risk_reward=response.risk_reward,
        position_size_eur=response.position_size_eur,
        memo=memo,
        source=source,
    )


def build_analyst_views(
    *,
    fundamental: FundamentalAssessmentResult | None = None,
    technical: TechnicalAssessmentResult | None = None,
) -> list[AnalystView]:
    views = [
        _build_fundamental_view(fundamental),
        _build_technical_view(technical),
    ]
    return sorted(views, key=lambda view: view.agent_name)


def build_critique_request(
    ticker: str,
    run_id: str,
    *,
    draft: DraftRecommendation,
    analyst_views: Sequence[AnalystView],
    deterministic_warnings: Sequence[str],
    deterministic_diagnosis: DecisionDiagnosis | None = None,
    as_of: date | None = None,
    engine: object | None = None,
) -> CritiqueRequest:
    context = build_fundamental_analysis_request(
        ticker=ticker,
        run_id=run_id,
        as_of=as_of,
        engine=engine,
    )
    request = CritiqueRequest(
        run_id=run_id,
        context_hash="pending",
        ticker=context.ticker,
        as_of_date=context.as_of_date,
        snapshot_at=context.snapshot_at,
        draft=draft,
        analyst_views=sorted(list(analyst_views), key=lambda view: view.agent_name),
        evidence_chunks=_cap_evidence_chunks(context.evidence_chunks),
        deterministic_warnings=_deduplicated(deterministic_warnings),
        deterministic_diagnosis=deterministic_diagnosis,
        allowed_actions=list(DEFAULT_ALLOWED_ACTIONS),
    )
    payload = request.model_dump(exclude={"context_hash", "run_id", "snapshot_at"})
    return request.model_copy(update={"context_hash": compute_context_hash(payload)})


def _build_fundamental_view(
    fundamental: FundamentalAssessmentResult | None,
) -> AnalystView:
    if fundamental is None:
        return _absent_view("fundamental_analyst")
    response = fundamental.response
    return AnalystView(
        agent_name=response.agent_name,
        status=response.status,
        overall_signal=response.overall_signal,
        findings=[
            *_cited_findings(response.findings, origin="finding"),
            *_cited_findings(response.contradictions, origin="contradiction"),
            *_cited_findings(response.material_red_flags, origin="risk_flag"),
        ],
        summary=response.summary,
    )


def _cited_findings(
    findings: Sequence,
    *,
    origin: Literal["finding", "contradiction", "risk_flag"],
) -> list[AnalystFinding]:
    return [
        AnalystFinding.from_cited_finding(finding, source_agent="fundamental_analyst", origin=origin)
        for finding in findings
    ]


def _build_technical_view(technical: TechnicalAssessmentResult | None) -> AnalystView:
    if technical is None:
        return _absent_view("technical_analyst")
    response = technical.response
    return AnalystView(
        agent_name="technical_analyst",
        status=response.status,
        overall_signal=response.overall_signal,
        findings=[
            *_technical_findings(response.findings, origin="finding"),
            *_technical_findings(response.contradictions, origin="contradiction"),
            *_technical_findings(response.risk_flags, origin="risk_flag"),
        ],
        summary=response.summary,
    )


def _technical_findings(
    findings: Sequence,
    *,
    origin: Literal["finding", "contradiction", "risk_flag"],
) -> list[AnalystFinding]:
    return [AnalystFinding.from_technical_finding(finding, origin=origin) for finding in findings]


def _absent_view(agent_name: Literal["fundamental_analyst", "technical_analyst"]) -> AnalystView:
    return AnalystView(
        agent_name=agent_name,
        status="absent",
        overall_signal="neutral",
        findings=[],
        summary="",
    )


def _cap_evidence_chunks(chunks: Sequence[EvidenceChunkInput]) -> list[EvidenceChunkInput]:
    sorted_chunks = sorted(chunks, key=_evidence_sort_key)[:MAX_EVIDENCE_CHUNKS]
    selected_chunks: list[EvidenceChunkInput] = []
    selected_characters = 0
    for chunk in sorted_chunks:
        next_total = selected_characters + len(chunk.text)
        if next_total > MAX_EVIDENCE_CHARACTERS:
            break
        selected_chunks.append(chunk)
        selected_characters = next_total
    return selected_chunks


def _evidence_sort_key(chunk: EvidenceChunkInput) -> tuple[str, str, str, str, int, int]:
    return (
        chunk.published_at.isoformat() if chunk.published_at is not None else "",
        chunk.retrieval_question_id,
        chunk.source_type,
        chunk.source_hash,
        chunk.chunk_index,
        chunk.chunk_id,
    )


def _deduplicated(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))
