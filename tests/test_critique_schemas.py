from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.analyze import AnalyzeResponse, AnalysisTrace, DecisionDiagnosis
from app.schemas.critique import (
    AnalystView,
    CriticEffect,
    CriticVersionStamp,
    CritiqueObjection,
    CritiqueRequest,
    CritiqueResponse,
    CritiqueResult,
    CritiqueValidation,
    DraftRecommendation,
)


def test_draft_recommendation_requires_trade_fields_for_trade_decisions():
    payload = _trade_draft_payload()
    DraftRecommendation.model_validate(payload)

    invalid_payload = dict(payload)
    invalid_payload["stop_loss"] = None

    with pytest.raises(ValidationError, match="Trade recommendations must include stop_loss"):
        DraftRecommendation.model_validate(invalid_payload)


def test_analyst_view_absent_status_requires_empty_neutral_payload():
    AnalystView.model_validate(_absent_analyst_view_payload())

    invalid_payload = _absent_analyst_view_payload()
    invalid_payload["findings"] = [_cited_finding_payload()]

    with pytest.raises(ValidationError, match="Absent analyst views must not include findings"):
        AnalystView.model_validate(invalid_payload)


def test_critique_request_rejects_empty_run_id():
    payload = _critique_request_payload()
    payload["run_id"] = "   "

    with pytest.raises(ValidationError, match="run_id must not be empty"):
        CritiqueRequest.model_validate(payload)


def test_critique_request_rejects_empty_context_hash():
    payload = _critique_request_payload()
    payload["context_hash"] = " "

    with pytest.raises(ValidationError, match="context_hash must not be empty"):
        CritiqueRequest.model_validate(payload)


def test_critique_request_requires_unique_chunk_ids():
    payload = _critique_request_payload()
    payload["evidence_chunks"] = [_evidence_chunk_payload(1), _evidence_chunk_payload(1)]

    with pytest.raises(ValidationError, match="Evidence chunk IDs must be unique"):
        CritiqueRequest.model_validate(payload)


def test_critique_request_allows_at_most_one_view_per_agent_name():
    payload = _critique_request_payload()
    payload["analyst_views"] = [
        _completed_analyst_view_payload(),
        _completed_analyst_view_payload(),
    ]

    with pytest.raises(ValidationError, match="Each analyst may appear at most once"):
        CritiqueRequest.model_validate(payload)


def test_critique_objection_requires_non_empty_claim():
    payload = _critique_objection_payload()
    payload["claim"] = "   "

    with pytest.raises(ValidationError, match="Objection claim must not be empty"):
        CritiqueObjection.model_validate(payload)


def test_critique_objection_requires_references_for_medium_and_high_severity():
    CritiqueObjection.model_validate(_critique_objection_payload())

    invalid_payload = _critique_objection_payload()
    invalid_payload["metric_ids"] = []
    invalid_payload["chunk_ids"] = []
    invalid_payload["warning_codes"] = []

    with pytest.raises(ValidationError, match="Medium/high-severity objections must include supporting references"):
        CritiqueObjection.model_validate(invalid_payload)


def test_critique_response_requires_unique_objection_ids():
    payload = _critique_response_payload()
    payload["objections"] = [
        _critique_objection_payload(objection_id="duplicate-id"),
        _critique_objection_payload(objection_id="duplicate-id", chunk_ids=[4], metric_ids=["metric-2"]),
    ]

    with pytest.raises(ValidationError, match="Objection IDs must be unique"):
        CritiqueResponse.model_validate(payload)


def test_critique_response_failed_and_insufficient_data_states_force_accept_defaults():
    valid_payload = _critique_response_payload(status="failed", verdict="accept", objections=[])
    valid_payload["proposed_confidence_penalty"] = 0.0
    valid_payload["proposed_decision"] = None
    CritiqueResponse.model_validate(valid_payload)

    invalid_payload = dict(valid_payload)
    invalid_payload["proposed_confidence_penalty"] = 0.1

    with pytest.raises(
        ValidationError,
        match="Failed and insufficient-data critiques must keep confidence penalty 0.0",
    ):
        CritiqueResponse.model_validate(invalid_payload)


def test_critique_response_revise_verdict_requires_revision_instructions():
    payload = _critique_response_payload(verdict="revise", revision_instructions="")

    with pytest.raises(ValidationError, match="Revision verdicts must include revision instructions"):
        CritiqueResponse.model_validate(payload)


def test_critique_response_reject_verdict_requires_high_severity_objection():
    payload = _critique_response_payload(
        verdict="reject",
        objections=[_critique_objection_payload(severity="low", metric_ids=[], chunk_ids=[], warning_codes=[])],
    )

    with pytest.raises(ValidationError, match="Reject verdicts must include at least one high-severity objection"):
        CritiqueResponse.model_validate(payload)


def test_critique_response_requires_non_empty_summary():
    payload = _critique_response_payload()
    payload["summary"] = " "

    with pytest.raises(ValidationError, match="Critique summary must not be empty"):
        CritiqueResponse.model_validate(payload)


def test_critique_request_round_trips_through_json_dump_and_validate():
    request = CritiqueRequest.model_validate(_critique_request_payload())

    round_tripped = CritiqueRequest.model_validate(request.model_dump(mode="json"))

    assert round_tripped.model_dump(mode="json") == request.model_dump(mode="json")


def test_critique_response_round_trips_through_json_dump_and_validate():
    response = CritiqueResponse.model_validate(_critique_response_payload())

    round_tripped = CritiqueResponse.model_validate(response.model_dump(mode="json"))

    assert round_tripped.model_dump(mode="json") == response.model_dump(mode="json")


def test_default_analyze_response_serializes_without_non_null_critic_effect():
    response = AnalyzeResponse(
        ticker="NVDA",
        decision="watchlist",
        confidence=0.4,
        reasons=["reason"],
        warnings=[],
        engine_version="1.0.0",
        trace_id="trace-123",
        trace=AnalysisTrace(run_id="run-123", ticker="NVDA"),
    )

    serialized = response.model_dump(mode="json")

    assert "critic_effect" not in serialized
    assert "critic_effect" not in serialized["trace"]


def _trade_draft_payload() -> dict:
    return {
        "decision": "trade",
        "confidence": 0.72,
        "reasons": ["Breakout confirmed"],
        "warnings": ["volatile"],
        "entry_range": (900.0, 910.0),
        "stop_loss": 875.0,
        "take_profit": (960.0, 980.0),
        "risk_reward": 2.5,
        "position_size_eur": 1500.0,
        "memo": "Draft memo",
        "source": "decision_synthesizer",
    }


def _watchlist_draft_payload() -> dict:
    return {
        "decision": "watchlist",
        "confidence": 0.35,
        "reasons": ["Needs confirmation"],
        "warnings": ["earnings soon"],
        "entry_range": None,
        "stop_loss": None,
        "take_profit": None,
        "risk_reward": None,
        "position_size_eur": None,
        "memo": "",
        "source": "deterministic",
    }


def _cited_finding_payload() -> dict:
    return {
        "finding_id": "finding-1",
        "category": "growth_quality",
        "direction": "positive",
        "materiality": "medium",
        "claim": "Revenue growth accelerated.",
        "metric_ids": ["revenue_growth:2026-06-30"],
        "chunk_ids": [1],
    }


def _completed_analyst_view_payload() -> dict:
    return {
        "agent_name": "fundamental_analyst",
        "status": "completed",
        "overall_signal": "positive",
        "findings": [_cited_finding_payload()],
        "summary": "Fundamentals are supportive.",
    }


def _absent_analyst_view_payload() -> dict:
    return {
        "agent_name": "technical_analyst",
        "status": "absent",
        "overall_signal": "neutral",
        "findings": [],
        "summary": "",
    }


def _evidence_chunk_payload(chunk_id: int) -> dict:
    return {
        "chunk_id": chunk_id,
        "retrieval_question_id": f"question-{chunk_id}",
        "source_type": "filing",
        "source_url": f"https://example.com/{chunk_id}",
        "source_hash": f"hash-{chunk_id}",
        "chunk_index": 0,
        "published_at": "2026-09-01T12:00:00Z",
        "quality_tier": "primary",
        "text": "Evidence text",
    }


def _decision_diagnosis_payload() -> dict:
    diagnosis = DecisionDiagnosis(
        stage="risk_math",
        rule_id="risk-reward",
        detail="Penalty due to elevated event risk.",
        checklist_score=8,
        checklist_max=11,
        missing_data=["options-flow"],
        debug_reason="needs risk adjustment",
    )
    return diagnosis.model_dump(mode="json")


def _critique_request_payload() -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "run-123",
        "context_hash": "context-abc",
        "ticker": "NVDA",
        "as_of_date": "2026-09-27",
        "snapshot_at": "2026-09-27T11:54:32Z",
        "draft": _trade_draft_payload(),
        "analyst_views": [_completed_analyst_view_payload(), _absent_analyst_view_payload()],
        "evidence_chunks": [_evidence_chunk_payload(1), _evidence_chunk_payload(2)],
        "deterministic_warnings": ["stale_market_data"],
        "deterministic_diagnosis": _decision_diagnosis_payload(),
        "allowed_actions": [
            "accept",
            "downgrade_confidence",
            "downgrade_decision",
            "force_no_trade",
            "request_revision",
        ],
        "max_confidence_penalty": 0.3,
    }


def _critique_objection_payload(
    *,
    objection_id: str = "obj-1",
    severity: str = "medium",
    metric_ids: list[str] | None = None,
    chunk_ids: list[int] | None = None,
    warning_codes: list[str] | None = None,
) -> dict:
    return {
        "objection_id": objection_id,
        "category": "risk_number_mismatch",
        "severity": severity,
        "source": "deterministic",
        "claim": "Risk budget does not match the stated entry and stop.",
        "metric_ids": ["risk_reward"] if metric_ids is None else metric_ids,
        "chunk_ids": [1] if chunk_ids is None else chunk_ids,
        "warning_codes": ["risk_budget_warning"] if warning_codes is None else warning_codes,
    }


def _critique_response_payload(
    *,
    status: str = "completed",
    verdict: str = "accept",
    objections: list[dict] | None = None,
    revision_instructions: str = "Tighten the stop-loss justification.",
) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "run-123",
        "context_hash": "context-abc",
        "agent_name": "critic_guardrail",
        "status": status,
        "verdict": verdict,
        "objections": [_critique_objection_payload()] if objections is None else objections,
        "proposed_decision": "watchlist",
        "proposed_confidence_penalty": 0.15,
        "revision_instructions": revision_instructions,
        "summary": "The draft needs a risk framing adjustment.",
    }


@pytest.mark.parametrize(
    ("model_class", "payload_factory"),
    [
        (DraftRecommendation, _watchlist_draft_payload),
        (AnalystView, _completed_analyst_view_payload),
        (CritiqueRequest, _critique_request_payload),
        (CritiqueObjection, _critique_objection_payload),
        (CritiqueResponse, _critique_response_payload),
        (CritiqueValidation, lambda: {"accepted": True, "reason_codes": ["accepted"]}),
        (
            CritiqueResult,
            lambda: {
                "response": _critique_response_payload(),
                "validation": {"accepted": True, "reason_codes": ["accepted"]},
                "model_name": "gpt-test",
                "prompt_version": "critic-v1",
            },
        ),
        (
            CriticVersionStamp,
            lambda: {
                "request_schema_version": "1.0",
                "critique_schema_version": "1.0",
                "prompt_version": "critic-v1",
                "model_name": "gpt-test",
                "policy_version": "policy-v1",
            },
        ),
        (
            CriticEffect,
            lambda: {
                "mode": "shadow",
                "accepted": True,
                "status": "completed",
                "verdict": "accept",
                "reason_codes": ["accepted"],
                "objection_counts": {"low": 1},
                "deterministic_objection_ids": ["det-1"],
                "model_objection_ids": ["mod-1"],
                "baseline_decision": "watchlist",
                "proposed_decision": None,
                "final_decision": "watchlist",
                "decision_changed": False,
                "baseline_confidence": 0.6,
                "applied_confidence_penalty": 0.1,
                "final_confidence": 0.5,
                "revisions_requested": 1,
                "revisions_performed": 0,
                "versions": {
                    "request_schema_version": "1.0",
                    "critique_schema_version": "1.0",
                    "prompt_version": "critic-v1",
                    "model_name": "gpt-test",
                    "policy_version": "policy-v1",
                },
            },
        ),
    ],
)
def test_all_critique_models_forbid_unknown_fields(model_class, payload_factory):
    payload = payload_factory()
    invalid_payload = dict(payload)
    invalid_payload["unexpected"] = "value"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model_class.model_validate(invalid_payload)
