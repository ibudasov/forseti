from __future__ import annotations

import pytest

from agents.config import (
    AGENTIC_PIPELINE_MODE,
    LINEAR_PIPELINE_MODE,
    AgentWorkflowConfig,
)
from app.services.pipeline import (
    AgenticPipeline,
    LinearPipeline,
    PipelineOverrideNotAllowedError,
    override_warning,
    select_pipeline,
)


def _config(mode: str) -> AgentWorkflowConfig:
    return AgentWorkflowConfig(
        pipeline_mode=mode,
        model_name="test-model",
        temperature=0.2,
        timeout_seconds=30,
        max_retries=1,
    )


def test_default_mode_selects_configured_pipeline():
    assert isinstance(
        select_pipeline(None, _config(LINEAR_PIPELINE_MODE), override_allowed=False),
        LinearPipeline,
    )
    assert isinstance(
        select_pipeline(None, _config(AGENTIC_PIPELINE_MODE), override_allowed=False),
        AgenticPipeline,
    )


def test_allowed_override_selects_requested_pipeline():
    assert isinstance(
        select_pipeline(
            AGENTIC_PIPELINE_MODE,
            _config(LINEAR_PIPELINE_MODE),
            override_allowed=True,
        ),
        AgenticPipeline,
    )
    assert isinstance(
        select_pipeline(
            LINEAR_PIPELINE_MODE,
            _config(AGENTIC_PIPELINE_MODE),
            override_allowed=True,
        ),
        LinearPipeline,
    )


def test_disabled_override_is_rejected():
    with pytest.raises(PipelineOverrideNotAllowedError):
        select_pipeline(
            AGENTIC_PIPELINE_MODE,
            _config(LINEAR_PIPELINE_MODE),
            override_allowed=False,
        )


def test_override_warning_is_only_present_for_explicit_mode():
    assert override_warning(None) is None
    assert override_warning(AGENTIC_PIPELINE_MODE) == "pipeline_override:agentic"
