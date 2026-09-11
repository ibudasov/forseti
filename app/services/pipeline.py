from __future__ import annotations

import logging
from typing import Callable, Optional, Protocol

from agents.config import AGENTIC_PIPELINE_MODE, LINEAR_PIPELINE_MODE, AgentWorkflowConfig
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse
from app.services.analyzer import analyze_request

logger = logging.getLogger(__name__)


class PipelineOverrideNotAllowedError(PermissionError):
    """Raised when a local-only pipeline override is disabled."""


class AnalysisPipeline(Protocol):
    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        """Analyze one request."""


class LinearPipeline:
    def __init__(self, engine=None) -> None:
        self.engine = engine

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        return analyze_request(request, engine=self.engine)


class AgenticPipeline:
    def __init__(self, config: AgentWorkflowConfig, engine=None) -> None:
        self.config = config
        self.engine = engine

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        from agents.orchestration.workflow import AgenticAnalysisWorkflow

        return AgenticAnalysisWorkflow(self.config, engine=self.engine).analyze(
            request.ticker,
            request=request,
        )


PipelineFactory = Callable[[AgentWorkflowConfig, object], AnalysisPipeline]


def _build_linear_pipeline(_: AgentWorkflowConfig, engine: object) -> AnalysisPipeline:
    return LinearPipeline(engine=engine)


def _build_agentic_pipeline(config: AgentWorkflowConfig, engine: object) -> AnalysisPipeline:
    return AgenticPipeline(config=config, engine=engine)


PIPELINE_FACTORIES: dict[str, PipelineFactory] = {
    LINEAR_PIPELINE_MODE: _build_linear_pipeline,
    AGENTIC_PIPELINE_MODE: _build_agentic_pipeline,
}


def select_pipeline(
    requested_mode: Optional[str],
    config: AgentWorkflowConfig,
    *,
    override_allowed: bool,
    engine=None,
) -> AnalysisPipeline:
    if requested_mode is not None and not override_allowed:
        raise PipelineOverrideNotAllowedError

    mode = requested_mode or config.pipeline_mode
    return PIPELINE_FACTORIES[mode](config, engine)


def override_warning(requested_mode: Optional[str]) -> Optional[str]:
    if requested_mode is None:
        return None
    return f"pipeline_override:{requested_mode}"


def log_pipeline_override(ticker: str, requested_mode: Optional[str]) -> None:
    if requested_mode is not None:
        logger.info("pipeline_override_used", extra={"ticker": ticker, "mode": requested_mode})
