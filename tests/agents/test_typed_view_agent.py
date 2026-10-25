"""Unit tests for the deterministic typed-view technical agent."""
from __future__ import annotations

import asyncio
import json

from app.schemas.technicals import (
    TechnicalAssessmentResponse,
    TechnicalAssessmentResult,
    TechnicalAssessmentValidation,
    TechnicalFinding,
)
from app.schemas.fundamentals import (
    CitedFinding,
    FundamentalAssessmentResponse,
    FundamentalAssessmentResult,
    FundamentalAssessmentValidation,
)
from agents.orchestration.adk_events import event_author, event_text, event_token_usage
from agents.orchestration.typed_view_agent import (
    TypedAnalystViewAgent,
    NEUTRAL_TECHNICAL_VIEW,
    TypedTechnicalViewAgent,
    render_fundamental_view,
    render_technical_view,
)


def _finding(finding_id: str = "f1") -> TechnicalFinding:
    return TechnicalFinding(
        finding_id=finding_id,
        category="trend",
        direction="positive",
        materiality="high",
        claim="Price above the 50-day moving average.",
        metric_ids=["trend_50dma:2026-09-08"],
    )


def _assessment(
    *, status: str = "completed", findings: list[TechnicalFinding] | None = None
) -> TechnicalAssessmentResult:
    response = TechnicalAssessmentResponse(
        run_id="run-1",
        context_hash="ctx-1",
        status=status,
        overall_signal="positive",
        trend_state="uptrend",
        momentum_state="strengthening",
        proposed_score_adjustment=0,
        findings=findings or [],
        contradictions=[],
        risk_flags=[],
        missing_information=[],
        summary="Technical summary.",
    )
    return TechnicalAssessmentResult(
        response=response,
        validation=TechnicalAssessmentValidation(accepted=True, reason_codes=[]),
        raw_output="{}",
        latency_ms=4.0,
        token_usage={"total_token_count": 5},
        model_name="fake-technical",
        prompt_version="technical-analyst.v1",
    )


def _fundamental_assessment() -> FundamentalAssessmentResult:
    response = FundamentalAssessmentResponse(
        run_id="run-1",
        context_hash="ctx-1",
        status="completed",
        overall_signal="positive",
        proposed_score_adjustment=0,
        findings=[
            CitedFinding(
                finding_id="fundamental-1",
                category="growth_quality",
                direction="positive",
                materiality="high",
                claim="Revenue growth is supported by cited evidence.",
                metric_ids=["revenue_growth:2026-06-30"],
                chunk_ids=[10],
            )
        ],
        contradictions=[],
        material_red_flags=[],
        evidence_coverage=0.75,
        missing_information=["cash-flow outlook"],
        summary="Fundamental summary.",
    )
    return FundamentalAssessmentResult(
        response=response,
        validation=FundamentalAssessmentValidation(accepted=True, reason_codes=[]),
        model_name="fake-fundamental",
        prompt_version="fundamental-analyst.v2",
    )


def _run_agent(agent: TypedTechnicalViewAgent):
    """Run the agent through real ADK session/runner machinery (no model call)."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part

    session_service = InMemorySessionService()
    session = asyncio.run(session_service.create_session(app_name="forseti-test", user_id="NVDA"))
    runner = Runner(app_name="forseti-test", agent=agent, session_service=session_service)
    events = list(
        runner.run(
            user_id="NVDA",
            session_id=session.id,
            new_message=Content(role="user", parts=[Part(text="Analyze ticker NVDA")]),
        )
    )
    final_session = asyncio.run(
        session_service.get_session(app_name="forseti-test", user_id="NVDA", session_id=session.id)
    )
    return events, final_session


def test_render_technical_view_merges_findings_contradictions_and_risk_flags():
    finding = _finding("f1")
    contradiction = _finding("f2")
    risk_flag = _finding("f3")
    response = TechnicalAssessmentResponse(
        run_id="run-1",
        context_hash="ctx-1",
        status="completed",
        overall_signal="positive",
        trend_state="uptrend",
        momentum_state="strengthening",
        proposed_score_adjustment=0,
        findings=[finding],
        contradictions=[contradiction],
        risk_flags=[risk_flag],
        missing_information=[],
        summary="Technical summary.",
    )
    assessment = TechnicalAssessmentResult(
        response=response,
        validation=TechnicalAssessmentValidation(accepted=True, reason_codes=[]),
        raw_output="{}",
        latency_ms=1.0,
        token_usage={},
        model_name="fake-technical",
        prompt_version="technical-analyst.v1",
    )

    rendered = render_technical_view(assessment)

    assert rendered["status"] == "completed"
    assert rendered["overall_signal"] == "positive"
    assert [item["finding_id"] for item in rendered["findings"]] == ["f1", "f2", "f3"]
    assert rendered["summary"] == "Technical summary."


def test_render_technical_view_returns_neutral_json_when_assessment_is_absent():
    assert render_technical_view(None) == NEUTRAL_TECHNICAL_VIEW


def test_render_technical_view_returns_neutral_status_when_assessment_failed():
    rendered = render_technical_view(_assessment(status="failed"))
    assert rendered["status"] == "failed"
    assert rendered["findings"] == []


def test_agent_emits_one_event_authored_as_technical_analyst_with_zero_tokens():
    assessment = _assessment(findings=[_finding()])
    agent = TypedTechnicalViewAgent(name="technical_analyst", assessment=assessment)

    events, _ = _run_agent(agent)

    assert len(events) == 1
    event = events[0]
    assert event_author(event) == "technical_analyst"
    assert event_token_usage(event) == {}


def test_agent_writes_rendered_assessment_to_session_state():
    assessment = _assessment(findings=[_finding()])
    agent = TypedTechnicalViewAgent(name="technical_analyst", assessment=assessment)

    events, session = _run_agent(agent)

    expected = render_technical_view(assessment)
    assert session.state["technical_view"] == expected
    assert json.loads(event_text(events[0])) == expected


def test_agent_writes_neutral_view_when_assessment_is_absent():
    agent = TypedTechnicalViewAgent(name="technical_analyst", assessment=None)

    _, session = _run_agent(agent)

    assert session.state["technical_view"] == NEUTRAL_TECHNICAL_VIEW


def test_fundamental_typed_agent_emits_one_zero_token_event_and_writes_cited_view():
    assessment = _fundamental_assessment()
    agent = TypedAnalystViewAgent(
        name="fundamental_analyst",
        state_key="fundamental_view",
        assessment=assessment,
    )

    events, session = _run_agent(agent)

    expected = render_fundamental_view(assessment)
    assert len(events) == 1
    assert event_author(events[0]) == "fundamental_analyst"
    assert event_token_usage(events[0]) == {}
    assert session.state["fundamental_view"] == expected
    assert json.loads(event_text(events[0])) == expected
    assert expected["findings"][0]["metric_ids"] == ["revenue_growth:2026-06-30"]
    assert expected["findings"][0]["chunk_ids"] == [10]
