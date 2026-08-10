"""Agentic workflow package (Google ADK).

This package wires the existing deterministic services (structured data
collection, rules engine, risk engine, retrieval) into an ADK-based
multi-agent workflow. It never reimplements business logic: agents and
tools here only orchestrate and delegate to `app.services` / `app.rag`.

See `agents/config.py` for the `PIPELINE_MODE` feature flag that switches
between the legacy linear pipeline and this agentic workflow.
"""
