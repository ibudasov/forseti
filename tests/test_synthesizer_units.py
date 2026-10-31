"""Unit tests for the LE-5 typed synthesizer integration pieces."""
from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from agents.config import load_agent_config
from agents.orchestration.adk_events import event_author, event_text, event_token_usage
from agents.orchestration.registry import (
    DECISION_SYNTHESIZER_NAME,
    DRAFT_RECOMMENDATION_OUTPUT_KEY,
    build_agent_registry,
)
from agents.orchestration.typed_view_agent import TypedDecisionMemoAgent
from app.schemas.analyze import AnalysisTrace, AnalyzeResponse, TraceStep
from app.schemas.critique import CritiqueRequest, DraftRecommendation
from app.schemas.memo import DecisionMemo, DecisionMemoItem, SynthesizerEffect
from app.schemas.synthesis import DecisionMemoResponse
from app.services.critic_rules import evaluate_deterministic_objections
from app.services.decision_synthesizer import build_decision_memo, memo_hash, render_risk_block
from app.services.synthesis_context import build_synthesis_request
from app.settings import Settings
from app.trust.invariants import (
    check_synthesizer_never_changes_risk_numbers,
    check_synthesizer_step_zero_tokens_in_typed_mode,
)
from tests.agents.test_typed_view_agent import _run_agent

AS_OF_DATE = date(2026, 9, 27)
SNAPSHOT_AT = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _trade_response(**changes) -> AnalyzeResponse:
    payload = {
        "ticker": "NVDA",
        "decision": "trade",
        "confidence": 0.7,
        "reasons": ["baseline"],
        "warnings": ["stale_price_data"],
        "entry_range": (100.0, 101.0),
        "stop_loss": 95.0,
        "take_profit": (110.0, 111.0),
        "risk_reward": 2.5,
        "position_size_eur": 2000.0,
        "engine_version": "v1.rules.0",
        "trace_id": "run-1",
    }
    return AnalyzeResponse(**{**payload, **changes})


def _synthesis_request(response: AnalyzeResponse | None = None, critic_findings=()):
    return build_synthesis_request(
        run_id="run-1",
        response=response or _trade_response(),
        analyst_views=[],
        as_of_date=AS_OF_DATE,
        snapshot_at=SNAPSHOT_AT,
        critic_findings=critic_findings,
    )


def _memo_response(request) -> DecisionMemoResponse:
    return DecisionMemoResponse(
        run_id=request.run_id,
        context_hash=request.context_hash,
        proposed_decision="watchlist",
        confidence=0.5,
        thesis="Thesis.",
        key_reasons=[DecisionMemoItem(claim="Stale prices.", warning_codes=["stale_price_data"])],
    )


def test_synthesizer_contract_defaults_to_legacy():
    assert Settings(_env_file=None).SYNTHESIZER_CONTRACT == "legacy"


def test_synthesis_request_never_allows_promotion_above_baseline():
    request = _synthesis_request(_trade_response(decision="watchlist", confidence=0.4))

    assert "trade" not in request.allowed_decisions
    assert request.max_confidence == 0.4


def test_synthesis_request_carries_known_warning_codes_and_critic_findings():
    request = _synthesis_request(critic_findings=["Fix the stop."])

    assert "stale_price_data" in request.known_warning_codes
    assert request.critic_findings == ["Fix the stop."]


def test_decision_memo_renders_risk_block_from_the_request_numbers():
    request = _synthesis_request()

    memo = build_decision_memo(_memo_response(request), request)

    assert memo.risk_block == render_risk_block(request)
    assert "95" in memo.risk_block
    assert memo.thesis == "Thesis."


def test_memo_hash_is_stable_and_changes_with_content():
    request = _synthesis_request()
    memo = build_decision_memo(_memo_response(request), request)

    assert memo_hash(memo) == memo_hash(memo.model_copy())
    assert memo_hash(memo) != memo_hash(memo.model_copy(update={"thesis": "Other."}))


def test_typed_memo_agent_emits_zero_token_event_and_writes_state():
    agent = TypedDecisionMemoAgent(
        name=DECISION_SYNTHESIZER_NAME, state_key=DRAFT_RECOMMENDATION_OUTPUT_KEY, rendered_memo="memo text"
    )

    events, session = _run_agent(agent)

    assert len(events) == 1
    assert event_author(events[0]) == DECISION_SYNTHESIZER_NAME
    assert event_text(events[0]) == "memo text"
    assert event_token_usage(events[0]) == {}
    assert session.state[DRAFT_RECOMMENDATION_OUTPUT_KEY] == "memo text"


def test_registry_uses_toolless_typed_memo_agent_when_memo_is_synthesized():
    config = load_agent_config(Settings(_env_file=None))

    registry = build_agent_registry(config, synthesized_memo="memo text")

    synthesizer = registry.specialists[DECISION_SYNTHESIZER_NAME]
    assert isinstance(synthesizer, TypedDecisionMemoAgent)
    assert not getattr(synthesizer, "tools", [])


def test_registry_keeps_legacy_synthesizer_with_risk_tool_by_default():
    config = load_agent_config(Settings(_env_file=None))

    synthesizer = build_agent_registry(config).specialists[DECISION_SYNTHESIZER_NAME]

    assert not isinstance(synthesizer, TypedDecisionMemoAgent)
    assert synthesizer.tools


def _critique_request(memo_claims: list[str]) -> CritiqueRequest:
    draft = DraftRecommendation(
        decision="trade",
        confidence=0.7,
        reasons=["Reason"],
        warnings=[],
        memo="",
        source="deterministic",
        entry_range=(100.0, 101.0),
        stop_loss=95.0,
        take_profit=(110.0, 111.0),
        risk_reward=2.5,
        position_size_eur=2000.0,
        memo_claims=memo_claims,
    )
    return CritiqueRequest(
        run_id="run-1",
        context_hash="hash",
        ticker="NVDA",
        as_of_date=AS_OF_DATE,
        snapshot_at=SNAPSHOT_AT,
        draft=draft,
        analyst_views=[],
        deterministic_warnings=[],
        allowed_actions=["accept", "request_revision"],
    )


def _memo_objections(memo_claims: list[str]):
    request = _critique_request(memo_claims)
    objections = evaluate_deterministic_objections(request=request, baseline=request.draft)
    return [item for item in objections if item.objection_id.startswith("det:memo_claim_mismatch")]


def test_critic_flags_memo_claims_that_contradict_risk_numbers():
    objections = _memo_objections(["The stop loss is 80.00."])

    assert objections
    assert objections[0].severity == "high"
    assert objections[0].category == "internal_inconsistency"


def test_critic_accepts_memo_claims_consistent_with_risk_numbers():
    assert _memo_objections(["Momentum is positive."]) == []


def _typed_trace(*, tokens: dict[str, int], with_effect: bool = True) -> AnalysisTrace:
    effect = SynthesizerEffect(
        contract="typed",
        prompt_version="v1",
        fallback_used=False,
        proposed_decision="trade",
        final_decision="trade",
        memo_hash="a" * 64,
    )
    return AnalysisTrace(
        run_id="run-1",
        ticker="NVDA",
        steps=[
            TraceStep(
                sequence=1,
                agent_name="decision_synthesizer",
                status="completed",
                output={"source": "typed_synthesis"},
                token_usage=tokens,
            )
        ],
        synthesizer_effect=effect if with_effect else None,
    )


def test_synthesizer_zero_token_invariant_rejects_tokens_on_typed_step():
    violations = check_synthesizer_step_zero_tokens_in_typed_mode(_typed_trace(tokens={"total_token_count": 5}))

    assert [violation.code for violation in violations] == ["synthesizer_typed_step_nonzero_tokens"]


def test_synthesizer_zero_token_invariant_accepts_zero_tokens():
    assert check_synthesizer_step_zero_tokens_in_typed_mode(_typed_trace(tokens={})) == []


def _memo(risk_block: str) -> DecisionMemo:
    return DecisionMemo(thesis="T", risk_block=risk_block)


def test_risk_numbers_invariant_flags_changed_numbers_and_missing_risk_block_values():
    deterministic = _trade_response()
    agentic = deterministic.model_copy(update={"stop_loss": 90.0, "memo": _memo("no numbers")})

    codes = {
        violation.code
        for violation in check_synthesizer_never_changes_risk_numbers(
            deterministic, agentic, _typed_trace(tokens={})
        )
    }

    assert codes == {"synthesizer_changed_risk_number", "memo_risk_block_mismatch"}


def test_risk_numbers_invariant_accepts_rendered_risk_block():
    deterministic = _trade_response()
    request = _synthesis_request(deterministic)
    agentic = deterministic.model_copy(update={"memo": _memo(render_risk_block(request))})

    assert check_synthesizer_never_changes_risk_numbers(deterministic, agentic, _typed_trace(tokens={})) == []


def test_risk_numbers_invariant_ignores_legacy_runs():
    deterministic = _trade_response()
    agentic = deterministic.model_copy(update={"stop_loss": 90.0})

    assert (
        check_synthesizer_never_changes_risk_numbers(deterministic, agentic, _typed_trace(tokens={}, with_effect=False))
        == []
    )


def test_synthesizer_effect_migration_adds_and_drops_jsonb_column(db_engine):
    migration = import_module("migrations.versions.0013_synthesizer_effect")
    with db_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS agent_run_steps CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS agent_runs CASCADE"))
        connection.execute(text("CREATE TABLE agent_runs (run_id VARCHAR(36) PRIMARY KEY)"))

        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

        columns = {column["name"]: column for column in inspect(connection).get_columns("agent_runs")}
        assert columns["synthesizer_effect"]["type"].__class__.__name__ == "JSONB"
        assert columns["synthesizer_effect"]["nullable"] is True

        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

        assert {column["name"] for column in inspect(connection).get_columns("agent_runs")} == {"run_id"}


def test_agent_run_cost_migration_adds_and_drops_columns(db_engine):
    migration = import_module("migrations.versions.0014_agent_run_cost")
    with db_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS agent_run_steps CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS agent_runs CASCADE"))
        connection.execute(text("CREATE TABLE agent_runs (run_id VARCHAR(36) PRIMARY KEY)"))

        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

        columns = {column["name"]: column for column in inspect(connection).get_columns("agent_runs")}
        assert columns["cost_usd"]["type"].__class__.__name__ == "NUMERIC"
        assert columns["cost_breakdown"]["type"].__class__.__name__ == "JSONB"
        assert columns["pricing_version"]["nullable"] is True
        assert columns["cost_usd"]["nullable"] is True

        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

        assert {column["name"] for column in inspect(connection).get_columns("agent_runs")} == {"run_id"}


def test_synthesizer_report_has_scorecard_line_for_rejections_without_fallback():
    from scripts.eval_synthesizer import _render_markdown
    from tests.fixtures.synthesizer.loader import evaluate_synthesizer_cases, load_synthesizer_cases

    report = evaluate_synthesizer_cases(load_synthesizer_cases()).to_dict()

    assert report["rejected_without_fallback_count"] == 0
    assert "| Rejected memos without fallback (must be 0) | 0 |" in _render_markdown(report)
