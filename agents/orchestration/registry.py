"""Agent registry: wires deterministic tools and LLM specialists into the
Trade Analyst Supervisor root agent described in the agentic workflow plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, Optional

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from agents.config import HARD_RULES_TEXT, AgentWorkflowConfig
from agents.tools.data_collector import build_structured_data_collector_tool
from agents.tools.retrieval import build_retriever_tool
from agents.tools.risk_engine import build_risk_manager_tool
from agents.tools.rules_engine import build_rules_engine_tool
from agents.tools.ticker_resolver import resolve_ticker
from app.services.risk import RiskConfig
from app.settings import get_settings

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from app.rag.embedding import EmbeddingClient

ROOT_AGENT_NAME = "trade_analyst_supervisor"

INPUT_RESOLVER_TOOL_NAME = "input_resolver"
STRUCTURED_DATA_COLLECTOR_TOOL_NAME = "structured_data_collector"
RETRIEVER_TOOL_NAME = "retriever"
RISK_MANAGER_TOOL_NAME = "risk_manager"

FUNDAMENTAL_ANALYST_NAME = "fundamental_analyst"
TECHNICAL_ANALYST_NAME = "technical_analyst"
DECISION_SYNTHESIZER_NAME = "decision_synthesizer"
CRITIC_NAME = "critic_guardrail"

# Specialists have no tools of their own; without this the model invents function names
# and the ADK run fails with "tool not found".
NO_TOOLS_TEXT = (
    "You have no tools available. Never emit a function call: answer in plain "
    "text using only the information already present in the conversation."
)


@dataclass(frozen=True)
class AgentRegistry:
    """All constructed agents and tools for a single agentic workflow run."""

    tools: Dict[str, FunctionTool] = field(default_factory=dict)
    specialists: Dict[str, LlmAgent] = field(default_factory=dict)
    root_agent: Optional[LlmAgent] = None


def _default_risk_config() -> RiskConfig:
    settings = get_settings()
    return RiskConfig(
        capital_eur=Decimal(str(settings.ACCOUNT_CAPITAL_EUR)),
        risk_per_trade_pct=Decimal(str(settings.RISK_PER_TRADE_PCT)),
    )


def build_deterministic_tools(
    engine: Optional["Engine"] = None,
    embedding_client: Optional["EmbeddingClient"] = None,
    risk_config: Optional[RiskConfig] = None,
    today: Optional[date] = None,
) -> Dict[str, FunctionTool]:
    """Build the deterministic tool-agents (Input Resolver, Structured Data
    Collector, Retriever, Risk Manager) as ADK `FunctionTool`s.

    `embedding_client` is optional so the registry can be constructed (e.g.
    for tests or the linear pipeline) without a live Vertex AI connection;
    the retriever tool is simply omitted in that case.
    """
    tools: Dict[str, FunctionTool] = {
        INPUT_RESOLVER_TOOL_NAME: FunctionTool(resolve_ticker),
        STRUCTURED_DATA_COLLECTOR_TOOL_NAME: FunctionTool(
            build_structured_data_collector_tool(engine=engine, today=today)
        ),
        RISK_MANAGER_TOOL_NAME: FunctionTool(
            build_risk_manager_tool(risk_config or _default_risk_config(), engine=engine)
        ),
    }

    if embedding_client is not None:
        tools[RETRIEVER_TOOL_NAME] = FunctionTool(
            build_retriever_tool(embedding_client, engine=engine)
        )

    # Rules engine backs the Decision Synthesizer/Critic rather than being a
    # standalone agent row, but it is exposed as a tool for reuse.
    tools["rules_engine"] = FunctionTool(build_rules_engine_tool(engine=engine, today=today))

    return tools


def build_specialist_agents(
    config: AgentWorkflowConfig,
    tools: Dict[str, FunctionTool],
) -> Dict[str, LlmAgent]:
    """Build the LLM specialist agents (Fundamental Analyst, Technical
    Analyst, Decision Synthesizer, Critic/Guardrail).

    Constructing an `LlmAgent` only validates configuration; it does not
    call the model until the agent is actually run.
    """
    generation_config = {"temperature": config.temperature}

    fundamental_analyst = LlmAgent(
        name=FUNDAMENTAL_ANALYST_NAME,
        model=config.model_name,
        description="Assesses growth, cash flow, debt, and quality from retrieved evidence and fundamentals.",
        instruction=(
            f"{HARD_RULES_TEXT}\n\n"
            "You are the Fundamental Analyst. Given fundamentals data and retrieved "
            "evidence chunks, describe growth, cash flow, debt, and quality. Cite the "
            "chunk id or metric name backing every claim.\n"
            f"{NO_TOOLS_TEXT}"
        ),
        generate_content_config=generation_config,
    )

    technical_analyst = LlmAgent(
        name=TECHNICAL_ANALYST_NAME,
        model=config.model_name,
        description="Interprets trend, RSI, support/resistance, and momentum from deterministic indicators.",
        instruction=(
            f"{HARD_RULES_TEXT}\n\n"
            "You are the Technical Analyst. Given the deterministic technical "
            "indicators, describe trend, momentum, and support/resistance. Do not "
            "compute new indicator values; only interpret the ones provided.\n"
            f"{NO_TOOLS_TEXT}"
        ),
        generate_content_config=generation_config,
    )

    decision_synthesizer = LlmAgent(
        name=DECISION_SYNTHESIZER_NAME,
        model=config.model_name,
        description="Combines the rules-engine output and analyst views into the final recommendation memo.",
        instruction=(
            f"{HARD_RULES_TEXT}\n\n"
            "You are the Decision Synthesizer. Combine the rules engine decision, "
            "the risk manager's trade levels, and the analyst views into a single "
            "human-readable memo. Reuse the risk manager's numbers verbatim; never "
            "recompute them.\n"
            "The only function you may call is `calculate_risk`. Write your memo as "
            "plain text; never invent any other function name."
        ),
        tools=[tools[RISK_MANAGER_TOOL_NAME]] if RISK_MANAGER_TOOL_NAME in tools else [],
        generate_content_config=generation_config,
    )

    critic = LlmAgent(
        name=CRITIC_NAME,
        model=config.model_name,
        description="Finds contradictions, unsupported claims, or violated hard rules and can force no_trade.",
        instruction=(
            f"{HARD_RULES_TEXT}\n\n"
            "You are the Critic/Guardrail. Review the draft recommendation for "
            "contradictions between the fundamental and technical views, claims "
            "without cited evidence, stale or incomplete data, and violated hard "
            "rules. You may only downgrade confidence or the decision label, "
            "never upgrade them.\n"
            f"{NO_TOOLS_TEXT}"
        ),
        generate_content_config=generation_config,
    )

    return {
        FUNDAMENTAL_ANALYST_NAME: fundamental_analyst,
        TECHNICAL_ANALYST_NAME: technical_analyst,
        DECISION_SYNTHESIZER_NAME: decision_synthesizer,
        CRITIC_NAME: critic,
    }


def build_root_agent(
    config: AgentWorkflowConfig,
    tools: Dict[str, FunctionTool],
    specialists: Dict[str, LlmAgent],
) -> LlmAgent:
    """Build the Trade Analyst Supervisor root agent orchestrating the
    deterministic tools and LLM specialists."""
    return LlmAgent(
        name=ROOT_AGENT_NAME,
        model=config.model_name,
        description="Orchestrates the agentic trade analysis workflow end-to-end.",
        instruction=(
            f"{HARD_RULES_TEXT}\n\n"
            "You are the Trade Analyst Supervisor. Resolve the ticker, collect "
            "structured data and evidence, delegate to the Fundamental and "
            "Technical Analysts, call the Risk Manager, delegate to the Decision "
            "Synthesizer, then have the Critic/Guardrail review the draft before "
            "returning the final recommendation.\n"
            "Call only the functions that are registered for you. To reach a "
            f"specialist ({FUNDAMENTAL_ANALYST_NAME}, {TECHNICAL_ANALYST_NAME}, "
            f"{DECISION_SYNTHESIZER_NAME}, {CRITIC_NAME}) you must use "
            "`transfer_to_agent` with its name as the argument; a specialist name "
            "is never itself a callable function."
        ),
        tools=list(tools.values()),
        sub_agents=list(specialists.values()),
        generate_content_config={"temperature": config.temperature},
    )


def build_agent_registry(
    config: AgentWorkflowConfig,
    engine: Optional["Engine"] = None,
    embedding_client: Optional["EmbeddingClient"] = None,
    risk_config: Optional[RiskConfig] = None,
    today: Optional[date] = None,
) -> AgentRegistry:
    """Build the full agent registry for a single agentic workflow run.

    Pure configuration/object construction: no network call is made.
    """
    tools = build_deterministic_tools(
        engine=engine, embedding_client=embedding_client, risk_config=risk_config, today=today
    )
    specialists = build_specialist_agents(config, tools)
    root_agent = build_root_agent(config, tools, specialists)
    return AgentRegistry(tools=tools, specialists=specialists, root_agent=root_agent)
