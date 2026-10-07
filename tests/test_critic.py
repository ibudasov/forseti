from __future__ import annotations

import json
from datetime import date, datetime, timezone

from app.schemas.critique import AnalystView, CritiqueObjection, CritiqueRequest, CritiqueResponse, DraftRecommendation
from app.schemas.fundamentals import CitedFinding, EvidenceChunkInput
from app.services.critic import (
    MAX_RAW_OUTPUT_CHARS,
    Critic,
    ModelOutput,
    RetryableModelError,
    _gemini_response_schema,
    build_critic_prompt,
    validate_critique,
)
from agents.config import HARD_RULES_TEXT


class _FakeModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.model_name = "fake-critic"
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> ModelOutput:
        self.prompts.append(prompt)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return ModelOutput(text=output, token_usage={"total_token_count": 9})


def test_valid_critique_is_accepted():
    request = _request()
    critic = Critic(model_port=_FakeModel([_response_json(request)]), max_retries=0)

    result = critic.review(request)

    assert result.validation.accepted is True
    assert result.response.verdict == "revise"
    assert result.response.objections[0].chunk_ids == [10]
    assert result.response.objections[0].metric_ids == ["revenue_growth:2026-06-30"]


def test_clean_draft_accepts_without_objections():
    request = _request()
    payload = _response_payload(request)
    payload.update(
        verdict="accept",
        objections=[],
        proposed_decision=None,
        proposed_confidence_penalty=0.0,
        revision_instructions="",
    )
    critic = Critic(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = critic.review(request)

    assert result.validation.accepted is True
    assert result.response.verdict == "accept"
    assert result.response.objections == []
    assert result.response.proposed_confidence_penalty == 0.0


def test_schema_validation_failure_reports_only_field_and_error_type():
    request = _request()
    payload = _response_payload(request)
    payload["verdict"] = "approve"
    critic = Critic(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = critic.review(request)

    assert result.validation.reason_codes == ["schema_validation_failed"]
    assert result.validation_diagnostics == ["verdict:literal_error"]
    assert "approve" not in " ".join(result.validation_diagnostics)


def test_gemini_response_schema_is_accepted_by_vertex_sdk():
    from vertexai.generative_models import GenerationConfig

    generation_config = GenerationConfig.from_dict(
        {
            "response_mime_type": "application/json",
            "response_schema": _gemini_response_schema(),
        }
    )

    assert generation_config.to_dict()["response_schema"]["type"] == "OBJECT"


def test_critic_prompt_echoes_request_ids_and_describes_cross_field_rules():
    request = _request()

    prompt = build_critic_prompt(request)

    assert f'"run_id": "{request.run_id}"' in prompt
    assert f'"context_hash": "{request.context_hash}"' in prompt
    assert "A revise verdict requires non-empty revision_instructions." in prompt
    assert "A reject verdict requires at least one high-severity objection." in prompt


def test_unparsable_output_falls_back_to_neutral_failure():
    request = _request()
    critic = Critic(model_port=_FakeModel(["not json"]), max_retries=1)

    result = critic.review(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["malformed_json"]
    assert result.response.status == "failed"
    assert result.response.verdict == "accept"
    assert result.response.objections == []


def test_provider_error_falls_back_with_reason_code():
    request = _request()
    critic = Critic(model_port=_FakeModel([RetryableModelError("timeout")]), max_retries=0)

    result = critic.review(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["provider_error:timeout"]
    assert result.response.status == "failed"


def test_retryable_model_error_retries_once_then_succeeds():
    request = _request()
    model = _FakeModel([RetryableModelError("timeout"), _response_json(request)])
    critic = Critic(model_port=model, max_retries=1)

    result = critic.review(request)

    assert result.validation.accepted is True
    assert len(model.prompts) == 2
    assert result.response.verdict == "revise"


def test_retries_exhausted_fall_back():
    request = _request()
    model = _FakeModel([RetryableModelError("timeout"), RetryableModelError("timeout")])
    critic = Critic(model_port=model, max_retries=1)

    result = critic.review(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["provider_error:timeout"]
    assert result.response.status == "failed"


def test_run_id_mismatch_is_rejected():
    validation = validate_critique(_response(run_id="other-run"), _request())

    assert validation.accepted is False
    assert validation.reason_codes == ["run_id_mismatch"]


def test_context_hash_mismatch_is_rejected():
    validation = validate_critique(_response(context_hash="other-hash"), _request())

    assert validation.accepted is False
    assert validation.reason_codes == ["context_hash_mismatch"]


def test_unknown_chunk_citation_is_rejected():
    objection = _objection(chunk_ids=[999])
    validation = validate_critique(_response(objections=[objection]), _request())

    assert validation.accepted is False
    assert validation.reason_codes == ["unknown_chunk_citation"]


def test_unknown_metric_citation_is_rejected():
    objection = _objection(metric_ids=["unknown-metric"])
    validation = validate_critique(_response(objections=[objection]), _request())

    assert validation.accepted is False
    assert validation.reason_codes == ["unknown_metric_citation"]


def test_unknown_warning_code_is_rejected():
    objection = _objection(warning_codes=["unknown-warning"])
    validation = validate_critique(_response(objections=[objection]), _request())

    assert validation.accepted is False
    assert validation.reason_codes == ["unknown_warning_code"]


def test_penalty_out_of_bounds_is_rejected():
    validation = validate_critique(
        _response(proposed_confidence_penalty=0.4),
        _request(max_confidence_penalty=0.3),
    )

    assert validation.accepted is False
    assert validation.reason_codes == ["penalty_out_of_bounds"]


def test_proposed_decision_upgrade_is_rejected_without_clamping():
    request = _request(decision="watchlist")
    critic = Critic(model_port=_FakeModel([_response_json(request, proposed_decision="trade")]), max_retries=0)

    result = critic.review(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["proposed_decision_upgrade"]
    assert result.response.proposed_decision == "trade"


def test_uncited_material_objection_is_rejected():
    objection = CritiqueObjection.model_construct(
        objection_id="objection-1",
        category="unsupported_claim",
        severity="high",
        source="model",
        claim="Unsupported material claim.",
        metric_ids=[],
        chunk_ids=[],
        warning_codes=[],
    )
    validation = validate_critique(_response(objections=[objection]), _request())

    assert validation.accepted is False
    assert validation.reason_codes == ["uncited_material_objection"]


def test_duplicate_objection_id_is_rejected():
    objection = _objection(objection_id="duplicate-id")
    duplicate = _objection(objection_id="duplicate-id", chunk_ids=[11], metric_ids=["margin:2026-06-30"])
    response = _response(objections=[objection, duplicate])

    validation = validate_critique(response, _request())

    assert validation.accepted is False
    assert validation.reason_codes == ["duplicate_objection_id"]


def test_forbidden_field_present_is_rejected_before_schema_validation():
    request = _request()
    payload = _response_payload(request)
    payload["stop_loss"] = 1.0
    critic = Critic(model_port=_FakeModel([json.dumps(payload)]), max_retries=0)

    result = critic.review(request)

    assert result.validation.accepted is False
    assert result.validation.reason_codes == ["forbidden_field_present"]


def test_raw_output_is_truncated_in_result_trace():
    request = _request()
    critic = Critic(
        model_port=_FakeModel([_response_json(request, summary="S" * (MAX_RAW_OUTPUT_CHARS + 200))]),
        max_retries=0,
    )

    result = critic.review(request)

    assert result.validation.accepted is True
    assert len(result.raw_output) == MAX_RAW_OUTPUT_CHARS + 1
    assert result.raw_output.endswith("…")


def test_prompt_contains_hard_rules_chunk_ids_allowed_actions_and_is_deterministic():
    request = _request()

    first = build_critic_prompt(request)
    second = build_critic_prompt(request)

    assert first == second
    assert HARD_RULES_TEXT in first
    for chunk in request.evidence_chunks:
        assert f"chunk_id={chunk.chunk_id}" in first
    for action in request.allowed_actions:
        assert action in first


def test_prompt_does_not_advertise_forbidden_money_field_output_keys():
    prompt = build_critic_prompt(_request())
    output_schema = prompt.split("Required JSON output schema:\n", maxsplit=1)[1]
    output_schema = output_schema.split("Emit JSON only.", maxsplit=1)[0]

    assert '"entry_range"' not in output_schema
    assert '"stop_loss"' not in output_schema
    assert '"take_profit"' not in output_schema
    assert '"risk_reward"' not in output_schema
    assert '"position_size_eur"' not in output_schema
    assert '"time_stop_at"' not in output_schema
    assert '"proposed_confidence"' not in output_schema


def _request(
    *,
    decision: str = "trade",
    max_confidence_penalty: float = 0.3,
) -> CritiqueRequest:
    return CritiqueRequest(
        run_id="run-1",
        context_hash="ctx-hash",
        ticker="NVDA",
        as_of_date=date(2026, 9, 27),
        snapshot_at=datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
        draft=_draft(decision=decision),
        analyst_views=[
            _view(
                agent_name="fundamental_analyst",
                findings=[
                    _finding(
                        finding_id="fundamental-finding-1",
                        metric_ids=["revenue_growth:2026-06-30"],
                        chunk_ids=[10],
                    ),
                    _finding(
                        finding_id="fundamental-finding-2",
                        metric_ids=["margin:2026-06-30"],
                        chunk_ids=[11],
                    ),
                ],
            ),
            _view(agent_name="technical_analyst", finding_id="technical-finding-1", metric_ids=["rsi:2026-09-27"]),
        ],
        evidence_chunks=[
            _chunk(10, "Primary evidence"),
            _chunk(11, "Secondary evidence"),
        ],
        deterministic_warnings=["warn:stale_data"],
        allowed_actions=[
            "accept",
            "downgrade_confidence",
            "downgrade_decision",
            "force_no_trade",
            "request_revision",
        ],
        max_confidence_penalty=max_confidence_penalty,
    )


def _draft(*, decision: str) -> DraftRecommendation:
    trade_fields = {
        "entry_range": (100.0, 101.0),
        "stop_loss": 95.0,
        "take_profit": (108.0, 110.0),
        "risk_reward": 2.0,
        "position_size_eur": 1000.0,
    }
    if decision != "trade":
        trade_fields = {
            "entry_range": None,
            "stop_loss": None,
            "take_profit": None,
            "risk_reward": None,
            "position_size_eur": None,
        }
    return DraftRecommendation(
        decision=decision,
        confidence=0.72,
        reasons=["Reason"],
        warnings=["Warning"],
        memo="Draft memo",
        source="decision_synthesizer",
        **trade_fields,
    )


def _view(
    *,
    agent_name: str,
    findings: list[CitedFinding] | None = None,
    finding_id: str = "finding-1",
    metric_ids: list[str] | None = None,
) -> AnalystView:
    return AnalystView(
        agent_name=agent_name,
        status="completed",
        overall_signal="positive",
        findings=findings or [_finding(finding_id=finding_id, metric_ids=metric_ids or ["revenue_growth:2026-06-30"])],
        summary="Analyst summary.",
    )


def _finding(
    *,
    finding_id: str = "finding-1",
    metric_ids: list[str] | None = None,
    chunk_ids: list[int] | None = None,
) -> CitedFinding:
    return CitedFinding(
        finding_id=finding_id,
        category="growth_quality",
        direction="positive",
        materiality="high",
        claim="Claim",
        metric_ids=metric_ids or ["revenue_growth:2026-06-30"],
        chunk_ids=chunk_ids or [10],
    )


def _chunk(chunk_id: int, text: str) -> EvidenceChunkInput:
    return EvidenceChunkInput(
        chunk_id=chunk_id,
        retrieval_question_id=f"question-{chunk_id}",
        source_type="filing",
        source_url=f"https://example.com/{chunk_id}",
        source_hash=f"hash-{chunk_id}",
        chunk_index=0,
        published_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        quality_tier="primary",
        text=text,
    )


def _objection(
    *,
    objection_id: str = "objection-1",
    metric_ids: list[str] | None = None,
    chunk_ids: list[int] | None = None,
    warning_codes: list[str] | None = None,
) -> CritiqueObjection:
    return CritiqueObjection(
        objection_id=objection_id,
        category="unsupported_claim",
        severity="high",
        source="model",
        claim="Claim",
        metric_ids=metric_ids or ["revenue_growth:2026-06-30"],
        chunk_ids=chunk_ids or [10],
        warning_codes=warning_codes or ["warn:stale_data"],
    )


def _response_json(
    request: CritiqueRequest,
    *,
    proposed_decision: str | None = "watchlist",
    summary: str = "Material objection found.",
) -> str:
    return json.dumps(
        _response_payload(
            request,
            proposed_decision=proposed_decision,
            summary=summary,
        )
    )


def _response_payload(
    request: CritiqueRequest,
    *,
    proposed_decision: str | None = "watchlist",
    summary: str = "Material objection found.",
) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": request.run_id,
        "context_hash": request.context_hash,
        "agent_name": "critic_guardrail",
        "status": "completed",
        "verdict": "revise",
        "objections": [
            {
                "objection_id": "objection-1",
                "category": "unsupported_claim",
                "severity": "high",
                "source": "model",
                "claim": "Evidence does not support the confidence level.",
                "metric_ids": ["revenue_growth:2026-06-30"],
                "chunk_ids": [10],
                "warning_codes": ["warn:stale_data"],
            }
        ],
        "proposed_decision": proposed_decision,
        "proposed_confidence_penalty": 0.2,
        "revision_instructions": "Cite stronger evidence or reduce confidence.",
        "summary": summary,
    }


def _response(
    *,
    run_id: str = "run-1",
    context_hash: str = "ctx-hash",
    objections: list[CritiqueObjection] | None = None,
    proposed_decision: str | None = "watchlist",
    proposed_confidence_penalty: float = 0.2,
) -> CritiqueResponse:
    return CritiqueResponse.model_construct(
        schema_version="1.0",
        run_id=run_id,
        context_hash=context_hash,
        agent_name="critic_guardrail",
        status="completed",
        verdict="revise",
        objections=objections or [_objection()],
        proposed_decision=proposed_decision,
        proposed_confidence_penalty=proposed_confidence_penalty,
        revision_instructions="Revise the draft.",
        summary="Summary",
    )
