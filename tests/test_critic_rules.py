from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.critique import AnalystView, CritiqueRequest, DraftRecommendation
from app.schemas.fundamentals import CitedFinding
from app.services.critic_rules import (
    FLOAT_ABSOLUTE_TOLERANCE,
    HARD_BLOCKER_WARNINGS,
    _analyst_contradiction_objections,
    _decision_upgrade_objections,
    _missing_trade_field_objections,
    _risk_number_mismatch_objections,
    _signal_polarity,
    _stale_or_incomplete_data_objections,
    _unsupported_claim_objections,
    evaluate_deterministic_objections,
)

AS_OF_DATE = date(2026, 9, 27)
SNAPSHOT_AT = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("field_name", "updated_value"),
    [
        ("entry_range", (101.0, 102.0)),
        ("stop_loss", 94.0),
        ("take_profit", (111.0, 112.0)),
        ("risk_reward", 3.2),
        ("position_size_eur", 2200.0),
    ],
)
def test_risk_number_mismatch_flags_each_risk_field(field_name: str, updated_value) -> None:
    baseline = _trade_draft()
    request = _request(draft=baseline.model_copy(update={field_name: updated_value}))

    objections = _risk_number_mismatch_objections(request=request, baseline=baseline)

    assert len(objections) == 1
    assert objections[0].warning_codes == [f"risk_number_mismatch:{field_name}"]
    assert objections[0].objection_id == "det:risk_number_mismatch:1"


def test_risk_number_mismatch_ignores_identical_numbers() -> None:
    baseline = _trade_draft()

    objections = _risk_number_mismatch_objections(request=_request(draft=baseline), baseline=baseline)

    assert objections == []


def test_risk_number_mismatch_flags_none_against_number() -> None:
    baseline = _trade_draft()
    draft = DraftRecommendation.model_construct(**{**baseline.model_dump(), "stop_loss": None})

    objections = _risk_number_mismatch_objections(
        request=_unsafe_request(draft=draft),
        baseline=baseline,
    )

    assert [objection.warning_codes for objection in objections] == [["risk_number_mismatch:stop_loss"]]


def test_risk_number_mismatch_ignores_differences_below_tolerance() -> None:
    baseline = _trade_draft()
    draft = baseline.model_copy(update={"stop_loss": baseline.stop_loss + (FLOAT_ABSOLUTE_TOLERANCE / 2)})

    objections = _risk_number_mismatch_objections(request=_request(draft=draft), baseline=baseline)

    assert objections == []


@pytest.mark.parametrize(
    ("baseline_decision", "draft_decision", "expects_objection"),
    [
        ("watchlist", "trade", True),
        ("trade", "watchlist", False),
        ("watchlist", "watchlist", False),
    ],
)
def test_decision_upgrade_only_flags_rank_increases(
    baseline_decision: str,
    draft_decision: str,
    expects_objection: bool,
) -> None:
    baseline = _draft(decision=baseline_decision)
    request = _request(draft=_draft(decision=draft_decision))

    objections = _decision_upgrade_objections(request=request, baseline=baseline)

    assert bool(objections) is expects_objection
    if expects_objection:
        assert objections[0].warning_codes == [f"decision_upgrade:{baseline_decision}:{draft_decision}"]


@pytest.mark.parametrize("warning", sorted(HARD_BLOCKER_WARNINGS))
def test_hard_blocker_warnings_each_raise_stale_or_incomplete_data_objection(warning: str) -> None:
    objections = _stale_or_incomplete_data_objections(
        request=_request(warnings=[warning]),
        baseline=_watchlist_draft(),
    )

    assert len(objections) == 1
    assert objections[0].warning_codes == [warning]


def test_non_blocker_warning_does_not_raise_stale_or_incomplete_data_objection() -> None:
    objections = _stale_or_incomplete_data_objections(
        request=_request(warnings=["not_a_blocker"]),
        baseline=_watchlist_draft(),
    )

    assert objections == []


def test_negative_fundamental_trade_contradiction_is_high_severity() -> None:
    fundamental_view = _view(
        agent_name="fundamental_analyst",
        overall_signal="negative",
        findings=[_finding(metric_ids=["revenue_growth:2026-06-30"])],
    )
    request = _request(draft=_trade_draft(), views=[fundamental_view, _absent_technical_view()])

    objections = _analyst_contradiction_objections(
        request=request,
        baseline=_trade_draft(),
    )

    assert len(objections) == 1
    assert objections[0].severity == "high"
    assert objections[0].metric_ids == ["revenue_growth:2026-06-30"]


def test_negative_fundamental_without_trade_decision_does_not_contradict() -> None:
    fundamental_view = _view(agent_name="fundamental_analyst", overall_signal="negative")
    request = _request(draft=_watchlist_draft(), views=[fundamental_view, _absent_technical_view()])

    objections = _analyst_contradiction_objections(
        request=request,
        baseline=_watchlist_draft(),
    )

    assert objections == []


def test_opposite_completed_analyst_views_raise_medium_contradiction() -> None:
    request = _request(
        draft=_watchlist_draft(),
        views=[
            _view(agent_name="fundamental_analyst", overall_signal="positive"),
            _view(agent_name="technical_analyst", overall_signal="negative"),
        ],
    )

    objections = _analyst_contradiction_objections(
        request=request,
        baseline=_watchlist_draft(),
    )

    assert len(objections) == 1
    assert objections[0].severity == "medium"
    assert objections[0].warning_codes == [
        "analyst_contradiction:fundamental_analyst:positive:technical_analyst:negative"
    ]


def test_neutral_signal_and_missing_view_do_not_contradict() -> None:
    neutral_request = _request(
        draft=_watchlist_draft(),
        views=[
            _view(agent_name="fundamental_analyst", overall_signal="positive"),
            _view(agent_name="technical_analyst", overall_signal="neutral"),
        ],
    )
    missing_request = _request(
        draft=_watchlist_draft(),
        views=[_view(agent_name="fundamental_analyst", overall_signal="positive")],
    )

    assert (
        _analyst_contradiction_objections(
            request=neutral_request,
            baseline=_watchlist_draft(),
        )
        == []
    )
    assert (
        _analyst_contradiction_objections(
            request=missing_request,
            baseline=_watchlist_draft(),
        )
        == []
    )


@pytest.mark.parametrize(
    ("signal", "expected_polarity"),
    [
        ("strong_negative", -1),
        ("negative", -1),
        ("neutral", 0),
        ("positive", 1),
        ("strong_positive", 1),
    ],
)
def test_signal_polarity_maps_all_supported_signals(signal: str, expected_polarity: int) -> None:
    assert _signal_polarity(signal) == expected_polarity


def test_trade_draft_validation_rejects_missing_risk_fields() -> None:
    payload = _trade_draft().model_dump()
    payload["stop_loss"] = None

    with pytest.raises(ValidationError, match="Trade recommendations must include stop_loss"):
        DraftRecommendation.model_validate(payload)


def test_missing_trade_fields_raise_internal_inconsistency_when_validation_is_bypassed() -> None:
    baseline = _trade_draft()
    draft = DraftRecommendation.model_construct(**{**baseline.model_dump(), "stop_loss": None})

    objections = _missing_trade_field_objections(
        request=_unsafe_request(draft=draft),
        baseline=baseline,
    )

    assert [objection.warning_codes for objection in objections] == [["missing_risk_field:stop_loss"]]


def test_non_trade_or_complete_trade_draft_does_not_raise_missing_field_objection() -> None:
    watchlist_objections = _missing_trade_field_objections(
        request=_request(draft=_watchlist_draft()),
        baseline=_watchlist_draft(),
    )
    trade_objections = _missing_trade_field_objections(
        request=_request(draft=_trade_draft()),
        baseline=_trade_draft(),
    )

    assert watchlist_objections == []
    assert trade_objections == []


def test_unsupported_claim_flags_uncited_material_finding() -> None:
    uncited_finding = CitedFinding.model_construct(
        finding_id="finding-uncited",
        category="growth_quality",
        direction="positive",
        materiality="high",
        claim="Revenue growth is exceptional.",
        metric_ids=[],
        chunk_ids=[],
    )
    request = _request(
        views=[
            _unsafe_view(agent_name="fundamental_analyst", findings=[uncited_finding]),
            _absent_technical_view(),
        ],
    )

    objections = _unsupported_claim_objections(request=request, baseline=_watchlist_draft())

    assert len(objections) == 1
    assert objections[0].warning_codes == [
        "unsupported_claim:fundamental_analyst:finding-uncited"
    ]


def test_supported_or_incomplete_findings_do_not_raise_unsupported_claim() -> None:
    cited_view = _view(
        agent_name="fundamental_analyst",
        findings=[_finding(chunk_ids=[9])],
    )
    insufficient_view = _view(
        agent_name="technical_analyst",
        status="insufficient_data",
        findings=[_finding(metric_ids=["rsi:2026-09-27"])],
    )
    request = _request(views=[cited_view, insufficient_view])

    objections = _unsupported_claim_objections(request=request, baseline=_watchlist_draft())

    assert objections == []


def test_aggregator_returns_objections_in_table_order() -> None:
    baseline = _trade_draft()
    request = _request(
        draft=baseline.model_copy(update={"stop_loss": baseline.stop_loss + 1.0}),
        warnings=["no_price_data"],
        views=[
            _view(
                agent_name="fundamental_analyst",
                overall_signal="negative",
                findings=[_finding(metric_ids=["revenue_growth:2026-06-30"])],
            ),
            _absent_technical_view(),
        ],
    )

    objections = evaluate_deterministic_objections(request=request, baseline=baseline)

    assert [objection.objection_id for objection in objections] == [
        "det:risk_number_mismatch:1",
        "det:stale_or_incomplete_data:1",
        "det:analyst_contradiction:1",
    ]


def test_aggregator_returns_empty_list_for_consistent_request() -> None:
    baseline = _watchlist_draft()

    objections = evaluate_deterministic_objections(
        request=_request(draft=baseline),
        baseline=baseline,
    )

    assert objections == []


def test_aggregator_objection_ids_are_unique_and_stable() -> None:
    baseline = _trade_draft()
    draft = DraftRecommendation.model_construct(
        **{
            **baseline.model_dump(),
            "entry_range": (baseline.entry_range[0] + 1.0, baseline.entry_range[1] + 1.0),
            "stop_loss": None,
        }
    )
    request = _unsafe_request(draft=draft, warnings=["no_fundamentals"])

    first = evaluate_deterministic_objections(request=request, baseline=baseline)
    second = evaluate_deterministic_objections(request=request, baseline=baseline)

    assert first == second
    assert len({objection.objection_id for objection in first}) == len(first)


def test_aggregator_is_pure_and_does_not_mutate_inputs() -> None:
    baseline = _trade_draft()
    request = _request(
        draft=baseline.model_copy(update={"risk_reward": baseline.risk_reward + 1.0}),
        warnings=["stale_price_data"],
    )
    baseline_before = baseline.model_dump(mode="json")
    request_before = request.model_dump(mode="json")

    first = evaluate_deterministic_objections(request=request, baseline=baseline)
    second = evaluate_deterministic_objections(request=request, baseline=baseline)

    assert first == second
    assert baseline.model_dump(mode="json") == baseline_before
    assert request.model_dump(mode="json") == request_before


def _request(
    *,
    draft: DraftRecommendation | None = None,
    views: list[AnalystView] | None = None,
    warnings: list[str] | None = None,
) -> CritiqueRequest:
    return CritiqueRequest(
        run_id="run-critic-rules",
        context_hash="context-hash",
        ticker="NVDA",
        as_of_date=AS_OF_DATE,
        snapshot_at=SNAPSHOT_AT,
        draft=draft or _watchlist_draft(),
        analyst_views=views or [_absent_fundamental_view(), _absent_technical_view()],
        deterministic_warnings=warnings or [],
        allowed_actions=["accept", "request_revision"],
    )


def _unsafe_request(
    *,
    draft: DraftRecommendation,
    views: list[AnalystView] | None = None,
    warnings: list[str] | None = None,
) -> CritiqueRequest:
    return CritiqueRequest.model_construct(
        schema_version="1.0",
        run_id="run-critic-rules",
        context_hash="context-hash",
        ticker="NVDA",
        as_of_date=AS_OF_DATE,
        snapshot_at=SNAPSHOT_AT,
        draft=draft,
        analyst_views=views or [_absent_fundamental_view(), _absent_technical_view()],
        evidence_chunks=[],
        deterministic_warnings=warnings or [],
        deterministic_diagnosis=None,
        allowed_actions=["accept", "request_revision"],
        max_confidence_penalty=0.30,
    )


def _watchlist_draft() -> DraftRecommendation:
    return _draft(decision="watchlist")


def _trade_draft() -> DraftRecommendation:
    return _draft(decision="trade")


def _draft(*, decision: str) -> DraftRecommendation:
    payload = {
        "decision": decision,
        "confidence": 0.72,
        "reasons": ["Reason"],
        "warnings": [],
        "memo": "",
        "source": "deterministic",
        "entry_range": None,
        "stop_loss": None,
        "take_profit": None,
        "risk_reward": None,
        "position_size_eur": None,
    }
    if decision == "trade":
        payload.update(
            {
                "entry_range": (100.0, 101.0),
                "stop_loss": 95.0,
                "take_profit": (110.0, 111.0),
                "risk_reward": 2.5,
                "position_size_eur": 2000.0,
            }
        )
    return DraftRecommendation(**payload)


def _view(
    *,
    agent_name: str,
    status: str = "completed",
    overall_signal: str = "positive",
    findings: list[CitedFinding] | None = None,
) -> AnalystView:
    return AnalystView(
        agent_name=agent_name,
        status=status,
        overall_signal=overall_signal,
        findings=findings or [],
        summary="summary" if status == "completed" else "",
    )


def _unsafe_view(
    *,
    agent_name: str,
    status: str = "completed",
    overall_signal: str = "positive",
    findings: list[CitedFinding] | None = None,
) -> AnalystView:
    return AnalystView.model_construct(
        agent_name=agent_name,
        status=status,
        overall_signal=overall_signal,
        findings=findings or [],
        summary="summary" if status == "completed" else "",
    )


def _absent_fundamental_view() -> AnalystView:
    return AnalystView(agent_name="fundamental_analyst", status="absent", overall_signal="neutral")


def _absent_technical_view() -> AnalystView:
    return AnalystView(agent_name="technical_analyst", status="absent", overall_signal="neutral")


def _finding(*, materiality: str = "high", metric_ids=None, chunk_ids=None) -> CitedFinding:
    return CitedFinding(
        finding_id="finding-1",
        category="growth_quality",
        direction="positive",
        materiality=materiality,
        claim="Claim",
        metric_ids=metric_ids or [],
        chunk_ids=chunk_ids or [],
    )
