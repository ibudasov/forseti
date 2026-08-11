"""Configuration for the ADK-based agentic workflow.

Loads model names, timeouts, retries, and the `PIPELINE_MODE` feature flag
from `app.settings.Settings`. No network calls happen here; this module
only assembles plain configuration values.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.settings import Settings, get_settings

LINEAR_PIPELINE_MODE = "linear"
AGENTIC_PIPELINE_MODE = "agentic"
_VALID_PIPELINE_MODES = (LINEAR_PIPELINE_MODE, AGENTIC_PIPELINE_MODE)

# Hard product rules every LLM agent instruction must restate. These are
# non-negotiable per the "Agentic workflow with Google ADK" execution plan:
# agents never compute or modify risk numbers, and may only downgrade
# confidence/decision, never upgrade them.
HARD_RULES_TEXT = (
    "Hard rules you must always follow:\n"
    "1. Entry range, stop-loss, take-profit, position size, and risk/reward "
    "are computed exclusively by the deterministic risk engine. You may only "
    "read these values; you must never compute, invent, or alter them.\n"
    "2. You may only downgrade confidence or downgrade the decision label "
    "(trade -> watchlist -> no_trade). You may never upgrade a decision "
    "beyond what the deterministic rules engine produced.\n"
    "3. Every claim you make must cite supporting evidence (a chunk id or a "
    "named metric). Unsupported claims must be flagged, not stated as fact."
)


@dataclass(frozen=True)
class AgentWorkflowConfig:
    """Immutable configuration for the agentic workflow."""

    pipeline_mode: str
    model_name: str
    temperature: float
    timeout_seconds: float
    max_retries: int

    def is_agentic(self) -> bool:
        """Whether the agentic (ADK) pipeline should run instead of the linear one."""
        return self.pipeline_mode == AGENTIC_PIPELINE_MODE


def load_agent_config(settings: Settings | None = None) -> AgentWorkflowConfig:
    """Build an `AgentWorkflowConfig` from application settings."""
    settings = settings or get_settings()
    pipeline_mode = settings.PIPELINE_MODE.strip().lower()
    if pipeline_mode not in _VALID_PIPELINE_MODES:
        raise ValueError(
            f"PIPELINE_MODE must be one of {_VALID_PIPELINE_MODES!r}, got {pipeline_mode!r}."
        )

    return AgentWorkflowConfig(
        pipeline_mode=pipeline_mode,
        model_name=settings.GEMINI_MODEL,
        temperature=settings.AGENT_MODEL_TEMPERATURE,
        timeout_seconds=settings.AGENT_TIMEOUT_SECONDS,
        max_retries=settings.AGENT_MAX_RETRIES,
    )
