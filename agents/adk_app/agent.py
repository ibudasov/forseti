"""Agent module discovered by the ADK development UI."""
from agents.config import load_agent_config
from agents.orchestration.registry import build_agent_registry

root_agent = build_agent_registry(load_agent_config()).root_agent
