"""Small helpers that turn readable fixture JSON into Technical Analyst objects.

Mirrors the spirit of ``tests/fixtures/critic/loader.py``'s payload builders:
fixtures stay small and declarative, while this module owns the boilerplate
(parsing dates/Decimals via Pydantic, recomputing ``context_hash``, and
stamping ``run_id``/``context_hash`` tokens into canned model output).
"""

from __future__ import annotations

from typing import Any

from app.schemas.analyze import AnalyzeResponse, DecisionDiagnosis
from app.schemas.critique import AnalystView
from app.schemas.technicals import (
    TechnicalAnalysisRequest,
    TechnicalAssessmentResponse,
    TechnicalAssessmentResult,
    TechnicalAssessmentValidation,
)
from app.services.context_hash import compute_context_hash

RUN_ID_TOKEN = "__RUN_ID__"
CONTEXT_HASH_TOKEN = "__CONTEXT_HASH__"
_HASH_EXCLUDED_FIELDS = {"run_id", "context_hash", "snapshot_at"}


def build_request(payload: dict[str, Any]) -> TechnicalAnalysisRequest:
    """Build a ``TechnicalAnalysisRequest`` and recompute its ``context_hash``.

    Mirrors ``build_technical_analysis_request`` in
    ``app.services.technical_context``: validate with a placeholder hash,
    then recompute it over the same excluded fields so no fixture ever hand-
    writes a hash value.
    """
    provisional = TechnicalAnalysisRequest.model_validate({**payload, "context_hash": "pending"})
    hash_payload = provisional.model_dump(mode="json", exclude=_HASH_EXCLUDED_FIELDS)
    context_hash = compute_context_hash(hash_payload)
    return provisional.model_copy(update={"context_hash": context_hash})


def stamp_model_output(template: str, request: TechnicalAnalysisRequest) -> str:
    """Replace the run_id/context_hash tokens in a canned raw model response."""
    return template.replace(RUN_ID_TOKEN, request.run_id).replace(CONTEXT_HASH_TOKEN, request.context_hash)


def build_assessment_result(
    request: TechnicalAnalysisRequest,
    payload: dict[str, Any],
) -> TechnicalAssessmentResult:
    """Build a ``TechnicalAssessmentResult`` directly, for policy/workflow fixtures
    that do not need to exercise ``TechnicalAnalyst``/model-port parsing.
    """
    response_payload = {
        "run_id": request.run_id,
        "context_hash": request.context_hash,
        **payload.get("response", {}),
    }
    response = TechnicalAssessmentResponse.model_validate(response_payload)
    validation_payload = payload.get("validation", {"accepted": True, "reason_codes": []})
    validation = TechnicalAssessmentValidation.model_validate(validation_payload)
    return TechnicalAssessmentResult(
        response=response,
        validation=validation,
        raw_output=str(payload.get("raw_output", "")),
        latency_ms=float(payload.get("latency_ms", 0.0)),
        token_usage=dict(payload.get("token_usage", {})),
        model_name=str(payload.get("model_name", "fixture-technical")),
        prompt_version=str(payload.get("prompt_version", "technical-analyst.v1")),
    )


def build_analyze_response(payload: dict[str, Any]) -> AnalyzeResponse:
    """Build the baseline/post-fundamental ``AnalyzeResponse`` for policy and
    workflow fixtures, filling in the risk fields consistently for a trade
    decision the way ``app.services.analyzer`` would.
    """
    decision = payload["decision"]
    confidence = float(payload["confidence"])
    checklist_score = int(payload.get("checklist_score", 0))
    is_trade = decision == "trade"
    return AnalyzeResponse(
        ticker=str(payload.get("ticker", "NVDA")),
        decision=decision,
        confidence=confidence,
        reasons=list(payload.get("reasons", ["deterministic"])),
        warnings=list(payload.get("warnings", [])),
        engine_version=str(payload.get("engine_version", "v1.rules.0")),
        trace_id=str(payload.get("trace_id", "trace-1")),
        entry_range=(100.0, 101.0) if is_trade else None,
        stop_loss=95.0 if is_trade else None,
        take_profit=(110.0, 115.0) if is_trade else None,
        risk_reward=2.0 if is_trade else None,
        position_size_eur=500.0 if is_trade else None,
        diagnosis=DecisionDiagnosis(
            stage="checklist",
            rule_id="checklist_passed" if checklist_score >= 8 else "score_below_trade",
            detail="baseline",
            checklist_score=checklist_score,
            debug_reason="baseline",
        ),
    )


def risk_fields_present(response: AnalyzeResponse) -> bool:
    return any(
        value is not None
        for value in (
            response.entry_range,
            response.stop_loss,
            response.take_profit,
            response.risk_reward,
            response.position_size_eur,
        )
    )


def technical_view_is_absent(view: AnalystView) -> bool:
    return view.status == "absent"
