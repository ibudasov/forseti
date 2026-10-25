"""Deterministic ADK agents that render validated analyst assessments."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, AsyncGenerator, Optional, Union

from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from pydantic import ConfigDict

from app.schemas.fundamentals import CitedFinding, FundamentalAssessmentResult
from app.schemas.technicals import TechnicalAssessmentResult, TechnicalFinding

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext

NEUTRAL_TECHNICAL_VIEW = {
    "status": "absent",
    "overall_signal": "neutral",
    "trend_state": "unknown",
    "momentum_state": "unknown",
    "proposed_score_adjustment": 0,
    "findings": [],
    "summary": "",
}
NEUTRAL_FUNDAMENTAL_VIEW = {
    "status": "absent",
    "overall_signal": "neutral",
    "proposed_score_adjustment": 0,
    "evidence_coverage": 0,
    "findings": [],
    "missing_information": [],
    "summary": "",
}


class TypedAnalystViewAgent(BaseAgent):
    """Writes a validated analyst assessment to session state without a model call."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    state_key: str
    assessment: Optional[Union[FundamentalAssessmentResult, TechnicalAssessmentResult]] = None

    async def _run_async_impl(self, ctx: "InvocationContext") -> AsyncGenerator[Event, None]:
        payload = render_analyst_view(self.state_key, self.assessment)
        payload_text = json.dumps(payload, sort_keys=True)
        yield Event(
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=payload_text)]),
            actions=EventActions(state_delta={self.state_key: payload}),
        )


class TypedDecisionMemoAgent(BaseAgent):
    """Writes the already-validated synthesizer memo to session state without a model call."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    state_key: str
    rendered_memo: str

    async def _run_async_impl(self, ctx: "InvocationContext") -> AsyncGenerator[Event, None]:
        yield Event(
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=self.rendered_memo)]),
            actions=EventActions(state_delta={self.state_key: self.rendered_memo}),
        )


class TypedTechnicalViewAgent(TypedAnalystViewAgent):
    """Compatibility wrapper for the original typed technical-view agent."""

    def __init__(
        self,
        *,
        name: str,
        assessment: Optional[TechnicalAssessmentResult] = None,
        output_key: str = "technical_view",
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, state_key=output_key, assessment=assessment, **kwargs)

    @property
    def output_key(self) -> str:
        return self.state_key


def render_analyst_view(
    state_key: str,
    assessment: Optional[Union[FundamentalAssessmentResult, TechnicalAssessmentResult]],
) -> dict[str, Any]:
    if state_key == "fundamental_view":
        return render_fundamental_view(assessment if isinstance(assessment, FundamentalAssessmentResult) else None)
    return render_technical_view(assessment if isinstance(assessment, TechnicalAssessmentResult) else None)


def render_fundamental_view(
    assessment: Optional[FundamentalAssessmentResult],
) -> dict[str, Any]:
    """Render a validated fundamental assessment with its citations."""
    if assessment is None:
        return dict(NEUTRAL_FUNDAMENTAL_VIEW)

    response = assessment.response
    if response.status != "completed":
        return {**NEUTRAL_FUNDAMENTAL_VIEW, "status": response.status}

    findings = (*response.findings, *response.contradictions, *response.material_red_flags)
    return {
        "status": response.status,
        "overall_signal": response.overall_signal,
        "proposed_score_adjustment": response.proposed_score_adjustment,
        "evidence_coverage": response.evidence_coverage,
        "findings": [_render_fundamental_finding(finding) for finding in findings],
        "missing_information": list(response.missing_information),
        "summary": response.summary,
    }


def render_technical_view(assessment: Optional[TechnicalAssessmentResult]) -> dict[str, Any]:
    """Render a `TechnicalAssessmentResult` into the compact JSON shape the
    Decision Synthesizer and Critic prompts interpolate as `{technical_view}`.
    """
    if assessment is None:
        return dict(NEUTRAL_TECHNICAL_VIEW)

    response = assessment.response
    if response.status != "completed":
        return {**NEUTRAL_TECHNICAL_VIEW, "status": response.status}

    return {
        "status": response.status,
        "overall_signal": response.overall_signal,
        "trend_state": response.trend_state,
        "momentum_state": response.momentum_state,
        "proposed_score_adjustment": response.proposed_score_adjustment,
        "findings": [
            _render_technical_finding(finding)
            for finding in (*response.findings, *response.contradictions, *response.risk_flags)
        ],
        "summary": response.summary,
    }


def _render_fundamental_finding(finding: CitedFinding) -> dict[str, Any]:
    return {
        "finding_id": finding.finding_id,
        "category": finding.category,
        "direction": finding.direction,
        "materiality": finding.materiality,
        "claim": finding.claim,
        "metric_ids": list(finding.metric_ids),
        "chunk_ids": list(finding.chunk_ids),
    }


def _render_technical_finding(finding: TechnicalFinding) -> dict[str, Any]:
    return {
        "finding_id": finding.finding_id,
        "category": finding.category,
        "direction": finding.direction,
        "materiality": finding.materiality,
        "claim": finding.claim,
        "metric_ids": list(finding.metric_ids),
    }
