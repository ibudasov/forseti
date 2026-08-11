"""Builds the ADK agent registry: deterministic tools + LLM specialists +
the Trade Analyst Supervisor root agent.

Construction here never performs a network call: building `FunctionTool`
and `LlmAgent` objects only assembles configuration, it does not invoke a
model or an external service.
"""
