"""Replay every golden case across all agent modes with offline model and retrieval stubs."""
from __future__ import annotations

from itertools import product
import socket
from types import SimpleNamespace

import pytest

import agents.orchestration.workflow as workflow_module
import app.services.fundamental_context as fundamental_context_module
from agents.config import load_agent_config
from agents.orchestration.cassette_runner import build_cassette_runner_factory
from agents.orchestration.workflow import AgenticAnalysisWorkflow
from app.schemas.analyze import AnalyzeRequest
from app.schemas.critique import CritiqueResponse, CritiqueResult, CritiqueValidation
from app.services.fundamental_evaluation import load_fundamental_evaluation_suite
from app.settings import Settings
from app.trust.invariants import (
    DECISION_RANK,
    RISK_FIELDS,
    check_downgrade_only_behavior,
    check_fundamental_agent_authority,
    check_technical_agent_effect_rank,
    check_technical_risk_fields_untouched_in_shadow,
)
from tests.fixtures.golden.loader import all_golden_case_names
from tests.fixtures.technical.factory import build_assessment_result
from tests.test_golden_cases import _run_linear_case

_MODES = ("off", "shadow", "enforced")
_RECORDED_FUNDAMENTAL = load_fundamental_evaluation_suite().cases[0].recorded_result


def _fundamental_assessment(request, adjustment):
    response = _RECORDED_FUNDAMENTAL.response.model_copy(
        update={
            "run_id": request.run_id,
            "context_hash": request.context_hash,
            "proposed_score_adjustment": adjustment,
        }
    )
    # Positive output deliberately bypasses model validation to exercise the policy's own guard.
    return _RECORDED_FUNDAMENTAL.model_copy(update={"response": response})


def _technical_assessment(request):
    return build_assessment_result(
        request,
        {
            "response": {
                "status": "completed",
                "overall_signal": "negative",
                "trend_state": "downtrend",
                "momentum_state": "weakening",
                "proposed_score_adjustment": -2,
                "summary": "Frozen negative technical assessment.",
            }
        },
    )


def _critique_assessment(self, request):
    return CritiqueResult(
        response=CritiqueResponse(
            run_id=request.run_id,
            context_hash=request.context_hash,
            status="completed",
            verdict="accept",
            proposed_confidence_penalty=0.1,
            summary="Frozen cautious critique.",
        ),
        validation=CritiqueValidation(accepted=True),
        model_name="fixture-critic",
        prompt_version="critic-guardrail.v1",
    )


def _reject_network(*args, **kwargs):
    raise AssertionError("External network I/O is forbidden in golden authority tests.")


@pytest.mark.parametrize("case_name", all_golden_case_names())
@pytest.mark.parametrize("fundamental_mode,technical_mode,critic_mode", tuple(product(_MODES, repeat=3)))
@pytest.mark.parametrize("adjustment", [-2, 0, 2])
def test_agent_authority_matrix_over_golden_cases(
    case_name, fundamental_mode, technical_mode, critic_mode, adjustment, db_engine, monkeypatch
):
    monkeypatch.setattr(socket.socket, "connect", _reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _reject_network)
    monkeypatch.setattr(
        fundamental_context_module, "_default_retriever", lambda **kwargs: lambda *args, **kwargs: []
    )
    case, baseline = _run_linear_case(case_name, db_engine)
    settings = Settings(
        _env_file=None,
        FUNDAMENTAL_AGENT_MODE=fundamental_mode,
        TECHNICAL_AGENT_MODE=technical_mode,
        CRITIC_MODE=critic_mode,
    )
    monkeypatch.setattr(workflow_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        workflow_module,
        "FundamentalAnalyst",
        lambda: SimpleNamespace(assess=lambda request: _fundamental_assessment(request, adjustment)),
    )
    monkeypatch.setattr(
        workflow_module, "TechnicalAnalyst", lambda: SimpleNamespace(assess=_technical_assessment)
    )
    monkeypatch.setattr(AgenticAnalysisWorkflow, "_review_critique", _critique_assessment)
    workflow = AgenticAnalysisWorkflow(
        load_agent_config(settings),
        engine=db_engine,
        runner_factory=build_cassette_runner_factory(case.cassette_path),
    )

    response = workflow.analyze(
        case.ticker, request=AnalyzeRequest(ticker=case.ticker, as_of_date=case.today)
    )

    assert check_downgrade_only_behavior(baseline, response) == []
    assert check_fundamental_agent_authority(response) == []
    assert check_technical_agent_effect_rank(response) == []
    assert check_technical_risk_fields_untouched_in_shadow(response) == []
    assert response.trace is not None
    fundamental_effect = response.fundamental_agent_effect
    assert (fundamental_effect is None) == (fundamental_mode == "off")
    post_fundamental_decision = baseline.decision
    if fundamental_effect is not None:
        assert fundamental_effect.applied_adjustment <= 0
        assert fundamental_effect.baseline_decision == baseline.decision
        assert response.trace.fundamental_agent_effect == fundamental_effect
        if fundamental_mode == "shadow":
            assert fundamental_effect.final_decision == baseline.decision
        post_fundamental_decision = fundamental_effect.final_decision
    technical_effect = response.technical_agent_effect
    assert (technical_effect is None) == (technical_mode == "off")
    post_technical_decision = post_fundamental_decision
    if technical_effect is not None:
        assert technical_effect.baseline_decision == post_fundamental_decision
        assert response.trace.technical_agent_effect == technical_effect
        post_technical_decision = technical_effect.final_decision
    assert DECISION_RANK[response.decision] <= DECISION_RANK[post_technical_decision]
    critic_effect = response.critic_effect
    assert (critic_effect is None) == (critic_mode == "off")
    if critic_effect is not None:
        assert DECISION_RANK[critic_effect.final_decision] <= DECISION_RANK[critic_effect.baseline_decision]
        assert critic_effect.final_confidence <= critic_effect.baseline_confidence
        assert response.trace.critic_effect == critic_effect
    for field_name in (*RISK_FIELDS, "time_stop_at"):
        actual = getattr(response, field_name)
        expected = getattr(baseline, field_name)
        cleared_for_downgrade = actual is None and response.decision != "trade" and technical_mode == "enforced"
        assert actual == expected or cleared_for_downgrade, field_name
