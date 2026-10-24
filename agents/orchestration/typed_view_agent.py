"""Deterministic ADK agent that renders the TA-4 typed technical assessment.

`TypedTechnicalViewAgent` replaces the narrative `technical_analyst` `LlmAgent`
once `TECHNICAL_AGENT_MODE != off`. It never calls a model or a tool: the
validated `TechnicalAssessmentResult` computed earlier in the workflow
(`AgenticAnalysisWorkflow._apply_technical_policy_if_enabled`) is the single
source of technical interpretation, so this agent only serialises that result
into the `technical_view` session-state key the Decision Synthesizer and
Critic prompts already read. It still emits exactly one event so the trace
and `observed_agents` continue to record a `technical_analyst` step.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, AsyncGenerator, Optional

from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from pydantic import ConfigDict

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


class TypedTechnicalViewAgent(BaseAgent):
    """Writes the validated technical assessment to session state.

    Makes no model or tool call. `name` must stay `technical_analyst` so the
    agent slots into `SPECIALIST_ORDER` unchanged.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    assessment: Optional[TechnicalAssessmentResult] = None
    output_key: str = "technical_view"

    async def _run_async_impl(self, ctx: "InvocationContext") -> AsyncGenerator[Event, None]:
        payload = render_technical_view(self.assessment)
        payload_text = json.dumps(payload, sort_keys=True)
        yield Event(
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=payload_text)]),
            actions=EventActions(state_delta={self.output_key: payload}),
        )


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
            _render_finding(finding)
            for finding in (*response.findings, *response.contradictions, *response.risk_flags)
        ],
        "summary": response.summary,
    }


def _render_finding(finding: TechnicalFinding) -> dict[str, Any]:
    return {
        "finding_id": finding.finding_id,
        "category": finding.category,
        "direction": finding.direction,
        "materiality": finding.materiality,
        "claim": finding.claim,
        "metric_ids": list(finding.metric_ids),
    }
