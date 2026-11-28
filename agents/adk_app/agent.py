"""Agent module discovered by the ADK development UI."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from google.adk.agents import BaseAgent

from agents.config import AgentWorkflowConfig, load_agent_config
from agents.orchestration.blackboard import BlackboardRuntime
from agents.orchestration.registry import build_agent_registry
from app.settings import Settings

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from app.rag.embedding import EmbeddingClient


def build_adk_root_agent(
    config: Optional[AgentWorkflowConfig] = None,
    *,
    engine: Optional["Engine"] = None,
    embedding_client: Optional["EmbeddingClient"] = None,
    settings: Optional[Settings] = None,
    blackboard_runtime: Optional[BlackboardRuntime] = None,
) -> Optional[BaseAgent]:
    """Build the root ADK agent for `adk web` / direct ADK Runner execution.

    Lazy `embedding_client` resolution inside `BlackboardRuntime` avoids
    import-time GCP/network calls during test collection or registry inspection.
    """
    resolved_config = config or load_agent_config(settings)
    return build_agent_registry(
        resolved_config,
        engine=engine,
        embedding_client=embedding_client,
        blackboard_runtime=blackboard_runtime,
    ).root_agent


root_agent = build_adk_root_agent()
