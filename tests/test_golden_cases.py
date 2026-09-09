"""Golden regression cases for deterministic and replayed agent runs."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from sqlmodel import Session

from agents.config import load_agent_config
from agents.orchestration.cassette_runner import build_cassette_runner_factory
from agents.orchestration.workflow import AgenticAnalysisWorkflow
from app.schemas.analyze import AnalyzeRequest
from app.services.pipeline import LinearPipeline
from app.settings import Settings
from tests.fixtures.golden.loader import all_golden_case_names, deterministic_fields, load_golden_case

_EXPECTED_OBSERVED_AGENTS = [
    "trade_analyst_supervisor",
    "fundamental_analyst",
    "technical_analyst",
    "decision_synthesizer",
    "critic_guardrail",
]


def _run_linear_case(case_name: str, db_engine):
    case = load_golden_case(case_name)
    with Session(db_engine) as session:
        case.seed(session)
    request = AnalyzeRequest(ticker=case.ticker, as_of_date=case.today)
    response = LinearPipeline(engine=db_engine).analyze(request)
    return case, response


@pytest.mark.parametrize("case_name", all_golden_case_names())
def test_linear_pipeline_matches_frozen_golden_case(case_name, db_engine):
    case, response = _run_linear_case(case_name, db_engine)

    case.assert_matches(response)


@pytest.mark.parametrize("case_name", all_golden_case_names())
def test_agentic_replay_matches_linear_pipeline(case_name, db_engine):
    case, linear_response = _run_linear_case(case_name, db_engine)
    workflow = AgenticAnalysisWorkflow(
        load_agent_config(Settings(_env_file=None)),
        engine=db_engine,
        runner_factory=build_cassette_runner_factory(case.cassette_path),
    )

    replayed_response = workflow.analyze(
        case.ticker,
        request=AnalyzeRequest(ticker=case.ticker, as_of_date=case.today),
    )

    case.assert_matches(replayed_response)
    assert deterministic_fields(replayed_response) == deterministic_fields(linear_response)
    assert replayed_response.trace is not None
    assert replayed_response.trace.observed_agents == _EXPECTED_OBSERVED_AGENTS


def test_mutated_fixture_fails_the_golden_assertion(db_engine):
    case = load_golden_case("clear_trade")
    mutated_inputs = deepcopy(case.inputs)
    mutated_inputs["price_bar"]["bars"] = 199
    mutated_case = replace(case, inputs=mutated_inputs)

    with Session(db_engine) as session:
        mutated_case.seed(session)
    response = LinearPipeline(engine=db_engine).analyze(
        AnalyzeRequest(ticker=mutated_case.ticker, as_of_date=mutated_case.today)
    )

    with pytest.raises(AssertionError, match="decision: expected 'trade', got 'watchlist'"):
        case.assert_matches(response)
