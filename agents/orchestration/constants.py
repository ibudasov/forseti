"""Shared agent names, ordering, and blackboard session.state key constants."""
from __future__ import annotations

ROOT_AGENT_NAME = "trade_analyst_supervisor"

FUNDAMENTAL_ANALYST_NAME = "fundamental_analyst"
TECHNICAL_ANALYST_NAME = "technical_analyst"
DECISION_SYNTHESIZER_NAME = "decision_synthesizer"
CRITIC_NAME = "critic_guardrail"
SPECIALIST_ORDER = (
    FUNDAMENTAL_ANALYST_NAME,
    TECHNICAL_ANALYST_NAME,
    DECISION_SYNTHESIZER_NAME,
    CRITIC_NAME,
)
SPECIALIST_FAN_OUT_NAME = "specialist_fan_out"
SPECIALIST_POLICY_FOLD_NAME = "specialist_policy_fold"
CROSS_DOMAIN_TENSION_NAME = "cross_domain_tension"

FUNDAMENTAL_VIEW_OUTPUT_KEY = "fundamental_view"
TECHNICAL_VIEW_OUTPUT_KEY = "technical_view"
DRAFT_RECOMMENDATION_OUTPUT_KEY = "draft_recommendation"
CRITIQUE_OUTPUT_KEY = "critique"

CROSS_DOMAIN_TENSION_KEY = "cross_domain_tension"
SUPPLEMENTAL_EVIDENCE_CHUNKS_KEY = "supplemental_evidence_chunks"
FUNDAMENTAL_REQUEST_KEY = "fundamental_request"
FUNDAMENTAL_ASSESSMENT_KEY = "fundamental_assessment"
FUNDAMENTAL_EFFECT_KEY = "fundamental_effect"
TECHNICAL_REQUEST_KEY = "technical_request"
TECHNICAL_ASSESSMENT_KEY = "technical_assessment"
TECHNICAL_EFFECT_KEY = "technical_effect"
DECISION_SYNTHESIS_REQUEST_KEY = "decision_synthesis_request"
DECISION_MEMO_RESULT_KEY = "decision_memo_result"
SYNTHESIZER_EFFECT_KEY = "synthesizer_effect"
CRITIQUE_REQUEST_KEY = "critique_request"
CRITIQUE_RESULT_KEY = "critique_result"
CRITIC_EFFECT_KEY = "critic_effect"
DETERMINISTIC_OBJECTIONS_KEY = "deterministic_objections"
POST_REVISION_OBJECTIONS_KEY = "post_revision_deterministic_objections"
CRITIC_REVISION_COUNT_KEY = "critic_revision_count"
DETERMINISTIC_RESPONSE_KEY = "deterministic_response"
ANALYSIS_RESPONSE_KEY = "analysis_response"
BASELINE_DRAFT_KEY = "baseline_draft"

TICKER_KEY = "ticker"
RUN_ID_KEY = "run_id"
EFFECTIVE_AS_OF_KEY = "effective_as_of"
SNAPSHOT_AT_KEY = "snapshot_at"
FUNDAMENTAL_CONTEXT_LATENCY_MS_KEY = "fundamental_context_latency_ms"
FUNDAMENTAL_ASSESSMENT_LATENCY_MS_KEY = "fundamental_assessment_latency_ms"
TECHNICAL_CONTEXT_LATENCY_MS_KEY = "technical_context_latency_ms"
TECHNICAL_ASSESSMENT_LATENCY_MS_KEY = "technical_assessment_latency_ms"
TENSION_LATENCY_MS_KEY = "tension_latency_ms"
WORKFLOW_WARNINGS_KEY = "workflow_warnings"
CRITIC_STEP_META_KEY = "critic_step_meta"
SYNTHESIS_REVISION_REQUEST_KEY = "synthesis_revision_request"
SYNTHESIS_REVISION_RESULT_KEY = "synthesis_revision_result"
