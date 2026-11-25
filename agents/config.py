"""Configuration for the ADK-based agentic workflow.

Loads model names, timeouts, retries, and the `PIPELINE_MODE` feature flag
from `app.settings.Settings`. No network calls happen here; this module
only assembles plain configuration values.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from app.settings import Settings, get_settings

LINEAR_PIPELINE_MODE = "linear"
AGENTIC_PIPELINE_MODE = "agentic"
_VALID_PIPELINE_MODES = (LINEAR_PIPELINE_MODE, AGENTIC_PIPELINE_MODE)
_VALID_AGENT_MODES = ("off", "shadow", "enforced")
_VALID_SYNTHESIZER_CONTRACTS = ("legacy", "typed")

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
    debug_llm_io: bool = False
    debug_llm_io_dir: str = "/tmp/forseti-llm-io"
    fundamental_agent_mode: Literal["off", "shadow", "enforced"] = "off"
    technical_agent_mode: Literal["off", "shadow", "enforced"] = "off"
    critic_mode: Literal["off", "shadow", "enforced"] = "off"
    synthesizer_contract: Literal["legacy", "typed"] = "legacy"

    def is_agentic(self) -> bool:
        """Whether the agentic (ADK) pipeline should run instead of the linear one."""
        return self.pipeline_mode == AGENTIC_PIPELINE_MODE


def _validated_agent_mode(name: str, raw_value: str) -> Literal["off", "shadow", "enforced"]:
    normalized = raw_value.strip().lower()
    if normalized not in _VALID_AGENT_MODES:
        raise ValueError(f"{name} must be one of {_VALID_AGENT_MODES!r}, got {normalized!r}.")
    return cast(Literal["off", "shadow", "enforced"], normalized)


def _validated_synthesizer_contract(raw_value: str) -> Literal["legacy", "typed"]:
    normalized = raw_value.strip().lower()
    if normalized not in _VALID_SYNTHESIZER_CONTRACTS:
        raise ValueError(
            f"SYNTHESIZER_CONTRACT must be one of {_VALID_SYNTHESIZER_CONTRACTS!r}, got {normalized!r}."
        )
    return cast(Literal["legacy", "typed"], normalized)


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
        debug_llm_io=settings.DEBUG_LLM_IO,
        debug_llm_io_dir=settings.DEBUG_LLM_IO_DIR,
        fundamental_agent_mode=_validated_agent_mode("FUNDAMENTAL_AGENT_MODE", settings.FUNDAMENTAL_AGENT_MODE),
        technical_agent_mode=_validated_agent_mode("TECHNICAL_AGENT_MODE", settings.TECHNICAL_AGENT_MODE),
        critic_mode=_validated_agent_mode("CRITIC_MODE", settings.CRITIC_MODE),
        synthesizer_contract=_validated_synthesizer_contract(settings.SYNTHESIZER_CONTRACT),
    )
