from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.schemas.analyze import AnalysisTrace, AnalyzeResponse

logger = logging.getLogger(__name__)


def log_analysis_completed(
    response: AnalyzeResponse,
    *,
    pipeline: str,
    agent_modes: dict[str, str],
) -> None:
    trace = response.trace
    effects = _agent_effects(trace)
    step_latencies = _agent_latencies(trace)
    degraded_reasons = list(response.warnings)
    if trace is not None:
        degraded_reasons.extend(
            f"{step.agent_name}:{step.output.get('reason', step.status)}"
            for step in trace.steps
            if step.status == "degraded" and step.output is not None
        )
    degraded_reasons = list(dict.fromkeys(degraded_reasons))
    run_id = trace.run_id if trace is not None else response.trace_id

    logger.info(
        "analysis_completed",
        extra={
            "event": "analysis_completed",
            "run_id": run_id,
            "ticker": response.ticker,
            "decision": response.decision,
            "pipeline": pipeline,
            "confidence": response.confidence,
            "total_latency_ms": trace.total_latency_ms if trace is not None else 0,
            "agent_latency_ms": step_latencies,
            "token_usage": trace.token_usage if trace is not None else {},
            "cost_usd": _persisted_cost_usd(trace),
            "cost_status": trace.cost_status if trace is not None else "unpriced",
            "pricing_version": trace.pricing_version if trace is not None else None,
            "degraded_reasons": degraded_reasons,
            "degraded": bool(degraded_reasons),
            "agent_modes": agent_modes,
            "agent_effects": effects,
        },
    )
    _log_agent_effects(trace, run_id, response.ticker, effects)


def _agent_effects(trace: AnalysisTrace | None) -> dict[str, dict[str, Any]]:
    effects = {
        "fundamental_analyst": trace.fundamental_agent_effect if trace is not None else None,
        "technical_analyst": trace.technical_agent_effect if trace is not None else None,
        "critic_guardrail": trace.critic_effect if trace is not None else None,
        "decision_synthesizer": trace.synthesizer_effect if trace is not None else None,
    }
    result = {agent: {"observed": False, "downgraded": False} for agent in effects}
    for agent, effect in effects.items():
        if effect is None:
            continue
        effect_data = effect.model_dump(mode="json")
        effect_data["downgraded"] = _was_downgraded(agent, effect_data)
        effect_data["observed"] = True
        result[agent] = effect_data
    return result


def _persisted_cost_usd(trace: AnalysisTrace | None) -> float | None:
    if trace is None or trace.estimated_cost_usd is None:
        return None
    persisted_cost = Decimal(str(trace.estimated_cost_usd)).quantize(
        Decimal("0.000001"),
        rounding=ROUND_HALF_UP,
    )
    return float(persisted_cost)


def _was_downgraded(agent: str, effect: dict[str, Any]) -> bool:
    if agent in {"fundamental_analyst", "technical_analyst"}:
        return effect.get("applied_adjustment", 0) < 0
    if agent == "critic_guardrail":
        decision_rank = {"trade": 2, "watchlist": 1, "no_trade": 0}
        final_decision = effect.get("final_decision")
        baseline_decision = effect.get("baseline_decision")
        if not isinstance(final_decision, str) or not isinstance(baseline_decision, str):
            return False
        return decision_rank.get(final_decision, 0) < decision_rank.get(baseline_decision, 0)
    return False


def _agent_latencies(trace: AnalysisTrace | None) -> dict[str, float]:
    if trace is None:
        return {}
    latency_by_agent: dict[str, float] = {}
    for step in trace.steps:
        latency_by_agent[step.agent_name] = (
            latency_by_agent.get(step.agent_name, 0) + step.latency_ms
        )
    return latency_by_agent


def _log_agent_effects(
    trace: AnalysisTrace | None,
    run_id: str | None,
    ticker: str,
    effects: dict[str, dict[str, Any]],
) -> None:
    for agent, effect in effects.items():
        if effect["downgraded"]:
            logger.info(
                "agent_downgraded",
                extra={
                    "event": "agent_downgraded",
                    "run_id": run_id,
                    "ticker": ticker,
                    "agent": agent,
                },
            )

    if trace is None:
        return
    for step in trace.steps:
        if step.status != "degraded":
            continue
        reason = "unknown"
        if step.output is not None:
            reason = str(step.output.get("reason", step.status))
        logger.info(
            "agent_degraded",
            extra={
                "event": "agent_degraded",
                "run_id": run_id,
                "ticker": ticker,
                "agent": step.agent_name,
                "reason": reason,
            },
        )
