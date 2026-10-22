"""Auto-discovered golden fixtures for the Technical Analyst evaluation harness.

Mirrors ``tests/fixtures/critic/loader.py``: every ``tests/fixtures/technical/*.json``
file is one self-contained case, dispatched by its ``case_type`` to the
smallest layer that exercises it (``assessment`` -> ``policy`` -> ``workflow``
-> ``live_model``). Malformed fixtures fail loudly with the filename and the
missing/invalid field name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from agents.orchestration.workflow import _apply_technical_effect_to_response
from app.db.models import AgentRun, AgentRunStep
from app.schemas.analyze import AnalyzeResponse
from app.schemas.critique import AnalystView
from app.schemas.technicals import TechnicalAgentEffect, TechnicalAnalysisRequest
from app.services.critique_context import build_analyst_views
from app.services.technical_analyst import (
    ModelOutput,
    RetryableModelError,
    TechnicalAnalyst,
    TechnicalModelPort,
)
from app.services.technical_policy import apply_technical_policy
from app.services.token_pricing import token_totals

from .factory import (
    build_analyze_response,
    build_assessment_result,
    build_request,
    risk_fields_present,
    stamp_model_output,
)

FIXTURE_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURE_DIR = FIXTURE_DIR
LIVE_FIXTURE_DIR = FIXTURE_DIR / "live"

_ASSESSMENT_LAYER: Literal["assessment"] = "assessment"
_POLICY_LAYER: Literal["policy"] = "policy"
_WORKFLOW_LAYER: Literal["workflow"] = "workflow"
_LIVE_MODEL_LAYER: Literal["live_model"] = "live_model"
_CASE_TYPES = {_ASSESSMENT_LAYER, _POLICY_LAYER, _WORKFLOW_LAYER, _LIVE_MODEL_LAYER}


@dataclass(frozen=True)
class TechnicalGoldenCase:
    name: str
    filename: str
    case_type: Literal["assessment", "policy", "workflow", "live_model"]
    description: str
    mode: Literal["off", "shadow", "enforced"]
    request: TechnicalAnalysisRequest
    expected: dict[str, Any] = field(default_factory=dict)
    model_entries: tuple[Any, ...] = ()
    max_retries: int = 0
    deterministic_response: AnalyzeResponse | None = None
    post_fundamental_response: AnalyzeResponse | None = None
    assessment_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class TechnicalCaseResult:
    name: str
    case_type: str
    passed: bool
    failure_message: str | None
    latency_ms: float | None = None
    token_usage: dict[str, int] | None = None
    model_call_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "case_type": self.case_type,
            "passed": self.passed,
            "failure_message": self.failure_message,
            "latency_ms": self.latency_ms,
            "token_usage": self.token_usage,
            "model_call_count": self.model_call_count,
        }


@dataclass(frozen=True)
class TechnicalSuiteResult:
    total_cases: int
    passed_cases: int
    failed_cases: int
    passed: bool
    cases: tuple[TechnicalCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "passed": self.passed,
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(frozen=True)
class TechnicalShadowReport:
    run_count: int
    adjustment_distribution: dict[str, int]
    decision_change_rate: float
    rejection_reason_counts: dict[str, int]
    trend_state_distribution: dict[str, int]
    momentum_state_distribution: dict[str, int]
    latency_ms: dict[str, float]
    token_usage: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_count": self.run_count,
            "adjustment_distribution": self.adjustment_distribution,
            "decision_change_rate": self.decision_change_rate,
            "rejection_reason_counts": self.rejection_reason_counts,
            "trend_state_distribution": self.trend_state_distribution,
            "momentum_state_distribution": self.momentum_state_distribution,
            "latency_ms": self.latency_ms,
            "token_usage": self.token_usage,
        }


@dataclass(frozen=True)
class TechnicalShadowSampleRow:
    run_id: str
    ticker: str
    applied_adjustment: int
    final_decision: str
    decision_changed: bool
    trend_state: str
    momentum_state: str
    reason_codes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ticker": self.ticker,
            "applied_adjustment": self.applied_adjustment,
            "final_decision": self.final_decision,
            "decision_changed": self.decision_changed,
            "trend_state": self.trend_state,
            "momentum_state": self.momentum_state,
            "reason_codes": self.reason_codes,
        }


def build_shadow_sample(
    runs: list[AgentRun],
    *,
    limit: int,
) -> list[TechnicalShadowSampleRow]:
    """Export structured, per-run shadow fields only -- never raw model output."""
    rows: list[TechnicalShadowSampleRow] = []
    for run in runs:
        effect = run.technical_agent_effect
        if effect is None or effect.get("mode") != "shadow":
            continue
        rows.append(
            TechnicalShadowSampleRow(
                run_id=run.run_id,
                ticker=run.ticker,
                applied_adjustment=int(effect.get("applied_adjustment", 0)),
                final_decision=str(effect.get("final_decision", "")),
                decision_changed=bool(effect.get("decision_changed")),
                trend_state=str(effect.get("trend_state", "unknown")),
                momentum_state=str(effect.get("momentum_state", "unknown")),
                reason_codes=",".join(effect.get("reason_codes", [])),
            )
        )
        if len(rows) >= limit:
            break
    return rows


class _SequenceModelPort:
    """Replay canned raw outputs or raise provider errors, in order."""

    model_name = "fixture-technical"

    def __init__(self, entries: list[Any]) -> None:
        self._entries = list(entries)
        self.call_count = 0

    def generate(self, prompt: str) -> ModelOutput:
        del prompt
        self.call_count += 1
        entry = self._entries.pop(0)
        if isinstance(entry, dict) and "provider_error" in entry:
            raise RetryableModelError(str(entry["provider_error"]))
        return ModelOutput(text=str(entry), token_usage={"total_token_count": 10})


class _NeverCalledModelPort:
    model_name = "never-called"

    def generate(self, prompt: str) -> ModelOutput:
        del prompt
        raise AssertionError("model must not be called when coverage is insufficient")


def technical_case_paths(fixture_dir: Path = DEFAULT_FIXTURE_DIR) -> list[Path]:
    return sorted(path for path in fixture_dir.glob("*.json"))


def technical_case_names(fixture_dir: Path = DEFAULT_FIXTURE_DIR) -> list[str]:
    return [path.stem for path in technical_case_paths(fixture_dir)]


def live_technical_case_paths(fixture_dir: Path = DEFAULT_FIXTURE_DIR) -> list[Path]:
    live_dir = fixture_dir / LIVE_FIXTURE_DIR.name
    if not live_dir.exists():
        return []
    return sorted(path for path in live_dir.glob("*.json"))


def live_technical_case_names(fixture_dir: Path = DEFAULT_FIXTURE_DIR) -> list[str]:
    return [path.stem for path in live_technical_case_paths(fixture_dir)]


def load_technical_case(path: Path) -> TechnicalGoldenCase:
    filename = _fixture_filename(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{filename}: invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{filename}: top-level JSON value must be an object.")
    return _case_from_payload(filename, payload)


def load_technical_cases(
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    *,
    include_live: bool = False,
) -> list[TechnicalGoldenCase]:
    paths = technical_case_paths(fixture_dir)
    if include_live:
        paths.extend(live_technical_case_paths(fixture_dir))
    return [load_technical_case(path) for path in paths]


def evaluate_suite(cases: list[TechnicalGoldenCase], *, live: bool = False) -> TechnicalSuiteResult:
    results = tuple(run_technical_case(case, live=live) for case in cases)
    passed_cases = sum(1 for result in results if result.passed)
    failed_cases = len(results) - passed_cases
    return TechnicalSuiteResult(
        total_cases=len(results),
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        passed=failed_cases == 0,
        cases=results,
    )


def render_suite_markdown(result: TechnicalSuiteResult) -> str:
    lines = [
        "# Technical agent evaluation",
        "",
        "| Case | Result | Detail |",
        "| --- | --- | --- |",
    ]
    for case in result.cases:
        detail = case.failure_message or "ok"
        lines.append(f"| {case.name} | {'pass' if case.passed else 'fail'} | {detail} |")
    lines.append("")
    lines.append(f"totals: {result.passed_cases}/{result.total_cases} passing; {result.failed_cases} failing")
    return "\n".join(lines) + "\n"


def run_technical_case(
    case: TechnicalGoldenCase,
    *,
    live: bool = False,
    model_port: TechnicalModelPort | None = None,
) -> TechnicalCaseResult:
    try:
        if case.case_type == _ASSESSMENT_LAYER:
            return _run_assessment_case(case)
        if case.case_type == _POLICY_LAYER:
            return _run_policy_case(case)
        if case.case_type == _WORKFLOW_LAYER:
            return _run_workflow_case(case)
        if case.case_type == _LIVE_MODEL_LAYER:
            if not live:
                raise ValueError(f"{case.filename}: live_model cases require live=True.")
            return _run_live_model_case(case, model_port=model_port)
        raise ValueError(f"Unsupported case_type '{case.case_type}'.")
    except Exception as exc:  # broad by design to keep suite reporting complete
        return TechnicalCaseResult(
            name=case.name,
            case_type=case.case_type,
            passed=False,
            failure_message=str(exc),
        )


def build_shadow_report(runs: list[AgentRun], steps: list[AgentRunStep]) -> TechnicalShadowReport:
    shadow_runs = [
        run for run in runs if run.technical_agent_effect is not None and run.technical_agent_effect.get("mode") == "shadow"
    ]
    adjustment_distribution: dict[str, int] = {str(value): 0 for value in range(-2, 1)}
    rejection_reason_counts: dict[str, int] = {}
    trend_state_distribution: dict[str, int] = {}
    momentum_state_distribution: dict[str, int] = {}
    decision_change_runs = 0
    latencies: list[float] = []
    total_prompt_tokens = 0
    total_candidate_tokens = 0
    total_tokens = 0

    step_by_run_id = {
        step.run_id: step for step in steps if step.agent_name == "technical_analyst"
    }

    for run in shadow_runs:
        effect = run.technical_agent_effect or {}
        adjustment_key = str(effect.get("applied_adjustment", 0))
        adjustment_distribution[adjustment_key] = adjustment_distribution.get(adjustment_key, 0) + 1
        if bool(effect.get("decision_changed")):
            decision_change_runs += 1
        for reason_code in effect.get("reason_codes", []):
            rejection_reason_counts[str(reason_code)] = rejection_reason_counts.get(str(reason_code), 0) + 1
        trend_state = str(effect.get("trend_state", "unknown"))
        trend_state_distribution[trend_state] = trend_state_distribution.get(trend_state, 0) + 1
        momentum_state = str(effect.get("momentum_state", "unknown"))
        momentum_state_distribution[momentum_state] = momentum_state_distribution.get(momentum_state, 0) + 1

        step = step_by_run_id.get(run.run_id)
        if step is not None and step.latency_ms > 0:
            latencies.append(step.latency_ms)
        if step is not None:
            prompt_tokens, candidate_tokens, observed_total = token_totals(step.token_usage or {})
            total_prompt_tokens += prompt_tokens
            total_candidate_tokens += candidate_tokens
            total_tokens += observed_total

    run_count = len(shadow_runs)
    return TechnicalShadowReport(
        run_count=run_count,
        adjustment_distribution=dict(sorted(adjustment_distribution.items())),
        decision_change_rate=_rate(decision_change_runs, run_count),
        rejection_reason_counts=dict(sorted(rejection_reason_counts.items())),
        trend_state_distribution=dict(sorted(trend_state_distribution.items())),
        momentum_state_distribution=dict(sorted(momentum_state_distribution.items())),
        latency_ms=_latency_summary(latencies),
        token_usage={
            "prompt_token_count": float(total_prompt_tokens),
            "candidates_token_count": float(total_candidate_tokens),
            "total_token_count": float(total_tokens),
        },
    )


def render_shadow_markdown(report: TechnicalShadowReport) -> str:
    lines = [
        "# Technical shadow report",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| runs | {report.run_count} |",
        f"| decision_change_rate | {report.decision_change_rate:.3f} |",
        f"| latency_p50_ms | {report.latency_ms['p50']:.1f} |",
        f"| latency_p95_ms | {report.latency_ms['p95']:.1f} |",
        f"| total_tokens | {int(report.token_usage['total_token_count'])} |",
    ]
    for adjustment, count in report.adjustment_distribution.items():
        lines.append(f"| adjustment:{adjustment} | {count} |")
    for reason_code, count in report.rejection_reason_counts.items():
        lines.append(f"| reason_code:{reason_code} | {count} |")
    for trend_state, count in report.trend_state_distribution.items():
        lines.append(f"| trend_state:{trend_state} | {count} |")
    for momentum_state, count in report.momentum_state_distribution.items():
        lines.append(f"| momentum_state:{momentum_state} | {count} |")
    return "\n".join(lines) + "\n"


def _run_assessment_case(case: TechnicalGoldenCase) -> TechnicalCaseResult:
    port = _NeverCalledModelPort() if not case.model_entries else _SequenceModelPort(list(case.model_entries))
    analyst = TechnicalAnalyst(model_port=port, max_retries=case.max_retries)

    result = analyst.assess(case.request)

    _assert_assessment_expectations(case, result)
    call_count = getattr(port, "call_count", 0)
    _assert_optional_int(case, "model_call_count", call_count)
    return TechnicalCaseResult(
        name=case.name,
        case_type=case.case_type,
        passed=True,
        failure_message=None,
        latency_ms=result.latency_ms,
        token_usage=result.token_usage,
        model_call_count=call_count,
    )


def _run_policy_case(case: TechnicalGoldenCase) -> TechnicalCaseResult:
    effect = _build_technical_effect(case)
    _assert_effect_expectations(case, effect)
    _assert_risk_field_expectation(case, effect)
    return TechnicalCaseResult(name=case.name, case_type=case.case_type, passed=True, failure_message=None)


def _run_workflow_case(case: TechnicalGoldenCase) -> TechnicalCaseResult:
    assessment_payload = _require(case.assessment_payload, case.filename, "assessment")
    assessment_result = build_assessment_result(case.request, assessment_payload)
    effect = _build_technical_effect(case)
    _assert_effect_expectations(case, effect)

    views = build_analyst_views(technical=assessment_result)
    technical_view = _technical_view(views)
    expect_absent = bool(case.expected.get("technical_view_absent", False))
    if (technical_view.status == "absent") != expect_absent:
        raise AssertionError(
            f"{case.filename}: expected technical_view_absent={expect_absent}, "
            f"got status={technical_view.status!r}"
        )
    return TechnicalCaseResult(name=case.name, case_type=case.case_type, passed=True, failure_message=None)


def _run_live_model_case(
    case: TechnicalGoldenCase,
    *,
    model_port: TechnicalModelPort | None,
) -> TechnicalCaseResult:
    analyst = TechnicalAnalyst(model_port=model_port) if model_port is not None else TechnicalAnalyst()
    result = analyst.assess(case.request)
    _assert_live_expectations(case, result)
    return TechnicalCaseResult(
        name=case.name,
        case_type=case.case_type,
        passed=True,
        failure_message=None,
        latency_ms=result.latency_ms,
        token_usage=result.token_usage,
    )


def _build_technical_effect(case: TechnicalGoldenCase) -> TechnicalAgentEffect:
    assessment_payload = _require(case.assessment_payload, case.filename, "assessment")
    assessment_result = build_assessment_result(case.request, assessment_payload)
    deterministic_response = _require(case.deterministic_response, case.filename, "deterministic_response")
    post_fundamental_response = case.post_fundamental_response or deterministic_response
    return apply_technical_policy(
        mode=case.mode,
        deterministic_response=deterministic_response.model_copy(deep=True),
        post_fundamental_response=post_fundamental_response.model_copy(deep=True),
        request=case.request,
        assessment_result=assessment_result,
    )


def _assert_assessment_expectations(case: TechnicalGoldenCase, result) -> None:
    expected = case.expected
    _assert_optional_bool(case, "accepted", result.validation.accepted)
    if "reason_codes" in expected:
        if list(result.validation.reason_codes) != list(expected["reason_codes"]):
            raise AssertionError(
                f"{case.filename}: reason_codes expected {expected['reason_codes']!r}, "
                f"got {result.validation.reason_codes!r}"
            )
    if "reason_codes_include" in expected:
        missing = [code for code in expected["reason_codes_include"] if code not in result.validation.reason_codes]
        if missing:
            raise AssertionError(f"{case.filename}: missing expected reason codes {missing}")
    _assert_optional_str(case, "status", result.response.status)
    _assert_optional_str(case, "overall_signal", result.response.overall_signal)
    _assert_optional_str(case, "trend_state", result.response.trend_state)
    _assert_optional_str(case, "momentum_state", result.response.momentum_state)
    if "proposed_score_adjustment" in expected:
        if result.response.proposed_score_adjustment != int(expected["proposed_score_adjustment"]):
            raise AssertionError(
                f"{case.filename}: proposed_score_adjustment expected "
                f"{expected['proposed_score_adjustment']}, got {result.response.proposed_score_adjustment}"
            )


def _assert_effect_expectations(case: TechnicalGoldenCase, effect: TechnicalAgentEffect) -> None:
    _assert_effect_field(case, effect, "applied_adjustment", lambda e: e.applied_adjustment, cast_int=True)
    _assert_effect_field(case, effect, "final_decision", lambda e: e.final_decision)
    _assert_effect_field(case, effect, "counterfactual_decision", lambda e: e.counterfactual_decision)
    _assert_effect_field(case, effect, "decision_changed", lambda e: e.decision_changed, cast_bool=True)
    _assert_reason_codes_include(case, effect)
    _assert_final_confidence_not_raised(case, effect)


def _assert_effect_field(
    case: TechnicalGoldenCase,
    effect: TechnicalAgentEffect,
    key: str,
    actual_getter,
    *,
    cast_int: bool = False,
    cast_bool: bool = False,
) -> None:
    if key not in case.expected:
        return
    expected_value = case.expected[key]
    if cast_int:
        expected_value = int(expected_value)
    if cast_bool:
        expected_value = bool(expected_value)
    actual_value = actual_getter(effect)
    if actual_value != expected_value:
        raise AssertionError(f"{case.filename}: {key} expected {expected_value!r}, got {actual_value!r}")


def _assert_reason_codes_include(case: TechnicalGoldenCase, effect: TechnicalAgentEffect) -> None:
    expected = case.expected
    if "reason_codes_include" not in expected:
        return
    missing = [code for code in expected["reason_codes_include"] if code not in effect.reason_codes]
    if missing:
        raise AssertionError(f"{case.filename}: missing expected reason codes {missing}")


def _assert_final_confidence_not_raised(case: TechnicalGoldenCase, effect: TechnicalAgentEffect) -> None:
    expected = case.expected
    if not bool(expected.get("final_confidence_not_raised", False)):
        return
    if effect.final_confidence > effect.baseline_confidence:
        raise AssertionError(f"{case.filename}: final_confidence was raised above baseline_confidence")


def _assert_risk_field_expectation(case: TechnicalGoldenCase, effect: TechnicalAgentEffect) -> None:
    if "risk_fields_unchanged_or_cleared" not in case.expected:
        return
    post_fundamental_response = case.post_fundamental_response or case.deterministic_response
    assert post_fundamental_response is not None  # for type-checkers; guaranteed by _build_technical_effect
    mutated = post_fundamental_response.model_copy(deep=True)
    _apply_technical_effect_to_response(effect, mutated)
    if effect.final_decision == post_fundamental_response.decision:
        if risk_fields_present(mutated) != risk_fields_present(post_fundamental_response):
            raise AssertionError(f"{case.filename}: risk fields changed without a decision change")
        return
    if effect.final_decision != "trade" and risk_fields_present(mutated):
        raise AssertionError(f"{case.filename}: risk fields were not cleared after a downgrade")


def _assert_live_expectations(case: TechnicalGoldenCase, result) -> None:
    expected = case.expected
    if "validation_accepted" in expected:
        if result.validation.accepted != bool(expected["validation_accepted"]):
            raise AssertionError(
                f"{case.filename}: validation_accepted expected {expected['validation_accepted']}, "
                f"got {result.validation.accepted}"
            )
    if "expected_adjustment" in expected:
        if result.response.proposed_score_adjustment != int(expected["expected_adjustment"]):
            raise AssertionError(
                f"{case.filename}: expected_adjustment expected {expected['expected_adjustment']}, "
                f"got {result.response.proposed_score_adjustment}"
            )
    bounds = expected.get("adjustment_bounds")
    if bounds:
        lower, upper = bounds
        if not (lower <= result.response.proposed_score_adjustment <= upper):
            raise AssertionError(
                f"{case.filename}: adjustment {result.response.proposed_score_adjustment} out of bounds {bounds}"
            )
    required_categories = set(expected.get("required_risk_flag_categories", []))
    if required_categories:
        actual_categories = {flag.category for flag in result.response.risk_flags}
        missing = required_categories - actual_categories
        if missing:
            raise AssertionError(f"{case.filename}: missing risk_flag categories {sorted(missing)}")


def _technical_view(views: list[AnalystView]) -> AnalystView:
    for view in views:
        if view.agent_name == "technical_analyst":
            return view
    raise AssertionError("build_analyst_views did not return a technical_analyst view")


def _case_from_payload(filename: str, payload: dict[str, Any]) -> TechnicalGoldenCase:
    case_type = _required_str(payload, filename, "case_type")
    if case_type not in _CASE_TYPES:
        raise ValueError(f"{filename}: unknown case_type '{case_type}'.")
    mode = str(payload.get("mode", "enforced"))
    if mode not in {"off", "shadow", "enforced"}:
        raise ValueError(f"{filename}: unknown mode '{mode}'.")
    request_payload = _mapping(payload.get("request"), filename, "request")
    request = build_request(request_payload)

    model_output = payload.get("model_output")
    model_entries: tuple[Any, ...] = ()
    if model_output is not None:
        raw_entries = model_output if isinstance(model_output, list) else [model_output]
        model_entries = tuple(
            stamp_model_output(entry, request) if isinstance(entry, str) else entry for entry in raw_entries
        )
    max_retries = int(payload.get("max_retries", max(len(model_entries) - 1, 0)))

    deterministic_response = None
    if payload.get("deterministic_response") is not None:
        deterministic_response = build_analyze_response(
            _mapping(payload["deterministic_response"], filename, "deterministic_response")
        )
    post_fundamental_response = None
    if payload.get("post_fundamental_response") is not None:
        post_fundamental_response = build_analyze_response(
            _mapping(payload["post_fundamental_response"], filename, "post_fundamental_response")
        )

    assessment_payload = _optional_mapping(payload.get("assessment"), filename, "assessment")

    return TechnicalGoldenCase(
        name=Path(filename).stem,
        filename=filename,
        case_type=cast(Literal["assessment", "policy", "workflow", "live_model"], case_type),
        description=str(payload.get("description", "")).strip(),
        mode=cast(Literal["off", "shadow", "enforced"], mode),
        request=request,
        expected=_mapping(payload.get("expected", {}), filename, "expected"),
        model_entries=model_entries,
        max_retries=max_retries,
        deterministic_response=deterministic_response,
        post_fundamental_response=post_fundamental_response,
        assessment_payload=assessment_payload,
    )


def _assert_optional_bool(case: TechnicalGoldenCase, key: str, actual: bool) -> None:
    if key in case.expected and bool(case.expected[key]) != actual:
        raise AssertionError(f"{case.filename}: {key} expected {case.expected[key]}, got {actual}")


def _assert_optional_str(case: TechnicalGoldenCase, key: str, actual: str) -> None:
    if key in case.expected and str(case.expected[key]) != actual:
        raise AssertionError(f"{case.filename}: {key} expected {case.expected[key]!r}, got {actual!r}")


def _assert_optional_int(case: TechnicalGoldenCase, key: str, actual: int) -> None:
    if key in case.expected and int(case.expected[key]) != actual:
        raise AssertionError(f"{case.filename}: {key} expected {case.expected[key]}, got {actual}")


def _required_str(mapping: dict[str, Any], filename: str, key: str) -> str:
    value = mapping.get(key)
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"{filename}: missing non-empty '{key}'.")


def _mapping(payload: Any, filename: str, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{filename}: '{label}' must be an object.")
    return dict(payload)


def _optional_mapping(payload: Any, filename: str, label: str) -> dict[str, Any] | None:
    if payload is None:
        return None
    return _mapping(payload, filename, label)


def _require(value, filename: str, label: str):
    if value is None:
        raise ValueError(f"{filename}: missing required '{label}'.")
    return value


def _fixture_filename(path: Path) -> str:
    try:
        return path.relative_to(DEFAULT_FIXTURE_DIR).as_posix()
    except ValueError:
        return path.name


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _latency_summary(latencies: list[float]) -> dict[str, float]:
    if not latencies:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(latencies)
    return {
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "max": ordered[-1],
    }


def _percentile(values: list[float], percentile: float) -> float:
    if len(values) == 1:
        return values[0]
    index = round((len(values) - 1) * percentile)
    return values[index]
