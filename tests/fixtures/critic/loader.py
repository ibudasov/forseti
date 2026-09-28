from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import patch

import agents.orchestration.workflow as workflow_module
from agents.config import load_agent_config
from agents.orchestration.registry import AgentRegistry
from agents.orchestration.workflow import AgenticAnalysisWorkflow
from app.db.models import AgentRun, AgentRunStep
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse
from app.schemas.critique import (
    AnalystView,
    CriticEffect,
    CritiqueObjection,
    CritiqueRequest,
    CritiqueResponse,
    CritiqueResult,
    CritiqueValidation,
    DraftRecommendation,
)
from app.schemas.fundamentals import CitedFinding, EvidenceChunkInput
from app.services.critic import Critic, ModelOutput, RetryableModelError
from app.services.critic_policy import apply_critic_policy
from app.services.critic_rules import evaluate_deterministic_objections
from app.settings import Settings

FIXTURE_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURE_DIR = FIXTURE_DIR
_CONSTRUCT_KEY = "__construct__"
_POLICY_LAYER: Literal["policy"] = "policy"
_WORKFLOW_LAYER: Literal["workflow_revision"] = "workflow_revision"
_MODEL_FAILURE_LAYER: Literal["model_failure"] = "model_failure"


@dataclass(frozen=True)
class RevisionOutcomeFixture:
    decision: Literal["trade", "watchlist", "no_trade"]
    confidence: float
    reasons: tuple[str, ...]
    raw_output: str
    token_usage: dict[str, int]
    latency_ms: float
    error_reason: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class CriticGoldenCase:
    name: str
    filename: str
    case_type: Literal["policy", "workflow_revision", "model_failure"]
    mode: Literal["off", "shadow", "enforced"]
    notes: str
    request: CritiqueRequest
    baseline: DraftRecommendation
    response: AnalyzeResponse
    expected_effect: dict[str, Any]
    deterministic_strategy: Literal["evaluate", "literal"] = "literal"
    deterministic_objections: tuple[CritiqueObjection, ...] = ()
    critique_result: CritiqueResult | None = None
    critique_results: tuple[CritiqueResult, ...] = ()
    revision_outcome: RevisionOutcomeFixture | None = None
    model_failure: dict[str, Any] | None = None
    expected_validation: dict[str, Any] | None = None
    shadow_matches_off: bool = False
    decision_synthesizer_call_count: int | None = None
    critic_review_call_count: int | None = None
    unused_critique_result_count: int | None = None


@dataclass(frozen=True)
class CriticCaseResult:
    name: str
    case_type: str
    layer: str
    passed: bool
    failure_message: str | None
    effect: CriticEffect | None
    validation: CritiqueValidation | None = None
    decision_synthesizer_call_count: int | None = None
    critic_review_call_count: int | None = None
    unused_critique_result_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "case_type": self.case_type,
            "layer": self.layer,
            "passed": self.passed,
            "failure_message": self.failure_message,
            "effect": self.effect.model_dump(mode="json") if self.effect is not None else None,
            "validation": (
                self.validation.model_dump(mode="json") if self.validation is not None else None
            ),
            "decision_synthesizer_call_count": self.decision_synthesizer_call_count,
            "critic_review_call_count": self.critic_review_call_count,
            "unused_critique_result_count": self.unused_critique_result_count,
        }


@dataclass(frozen=True)
class CriticSuiteResult:
    total_cases: int
    passed_cases: int
    failed_cases: int
    passed: bool
    cases: tuple[CriticCaseResult, ...]
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "passed": self.passed,
            "cases": [case.to_dict() for case in self.cases],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class CriticShadowReport:
    run_count: int
    objection_rate: float
    decision_change_rate: float
    severity_distribution: dict[str, int]
    deterministic_vs_model_split: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _NoopRecorder:
    enabled = False

    def record_run_config(self, payload):
        return None

    def record_agent_prompts(self, registry):
        return None

    def record_user_message(self, message):
        return None

    def record_event(self, index, event):
        return None

    def record_summary(self, payload):
        return None


class _Resolved:
    is_valid = True
    ticker = "NVDA"
    error = None


class _RaisingModelPort:
    def __init__(self, message: str):
        self.model_name = "fake-critic"
        self._message = message

    def generate(self, prompt: str) -> ModelOutput:
        del prompt
        raise RetryableModelError(self._message)


class _GarbageModelPort:
    def __init__(self, text: str):
        self.model_name = "fake-critic"
        self._text = text

    def generate(self, prompt: str) -> ModelOutput:
        del prompt
        return ModelOutput(text=self._text, token_usage={"total_token_count": 1})


def critic_case_paths(fixture_dir: Path = DEFAULT_FIXTURE_DIR) -> list[Path]:
    return sorted(path for path in fixture_dir.glob("*.json"))


def critic_case_names(fixture_dir: Path = DEFAULT_FIXTURE_DIR) -> list[str]:
    return [path.stem for path in critic_case_paths(fixture_dir)]


def load_critic_case(path: Path) -> CriticGoldenCase:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name}: top-level JSON value must be an object.")
    return _case_from_payload(path.name, payload)


def load_critic_cases(fixture_dir: Path = DEFAULT_FIXTURE_DIR) -> list[CriticGoldenCase]:
    return [load_critic_case(path) for path in critic_case_paths(fixture_dir)]


def evaluate_suite(cases: list[CriticGoldenCase]) -> CriticSuiteResult:
    results = tuple(run_critic_case(case) for case in cases)
    passed_cases = sum(1 for result in results if result.passed)
    failed_cases = len(results) - passed_cases
    notes: list[str] = []
    if not any(case.case_type == _MODEL_FAILURE_LAYER for case in cases):
        notes.append("No model-failure cases were discovered.")
    return CriticSuiteResult(
        total_cases=len(results),
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        passed=failed_cases == 0,
        cases=results,
        notes=tuple(notes),
    )


def render_suite_markdown(result: CriticSuiteResult) -> str:
    lines = [
        "# Critic evaluation",
        "",
        "| Case | Layer | Result | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for case in result.cases:
        detail = case.failure_message or "ok"
        lines.append(
            f"| {case.name} | {case.layer} | {'pass' if case.passed else 'fail'} | {detail} |"
        )
    lines.extend(
        [
            "",
            f"totals: {result.passed_cases}/{result.total_cases} passing; {result.failed_cases} failing",
        ]
    )
    if result.notes:
        lines.append("")
        lines.extend(f"- {note}" for note in result.notes)
    return "\n".join(lines) + "\n"


def run_critic_case(case: CriticGoldenCase) -> CriticCaseResult:
    try:
        if case.case_type == _POLICY_LAYER:
            return _run_policy_case(case)
        if case.case_type == _WORKFLOW_LAYER:
            return _run_workflow_case(case)
        if case.case_type == _MODEL_FAILURE_LAYER:
            return _run_model_failure_case(case)
        raise ValueError(f"Unsupported case_type '{case.case_type}'.")
    except Exception as exc:  # broad by design to keep suite reporting complete
        return CriticCaseResult(
            name=case.name,
            case_type=case.case_type,
            layer=case.case_type,
            passed=False,
            failure_message=str(exc),
            effect=None,
        )


def build_shadow_report(runs: list[AgentRun], steps: list[AgentRunStep]) -> CriticShadowReport:
    shadow_runs = [
        run for run in runs if run.critic_effect is not None and run.critic_effect.get("mode") == "shadow"
    ]
    severity_by_run_id = {
        step.run_id: _severity_counts(step)
        for step in steps
        if step.agent_name == "critic_guardrail" and step.output is not None
    }
    objection_runs = 0
    decision_change_runs = 0
    severity_distribution = {"high": 0, "medium": 0, "low": 0}
    deterministic_count = 0
    model_count = 0

    for run in shadow_runs:
        effect = run.critic_effect or {}
        objection_total = sum(int(value) for value in (effect.get("objection_counts") or {}).values())
        if objection_total > 0:
            objection_runs += 1
        if bool(effect.get("decision_changed")):
            decision_change_runs += 1
        for severity, count in severity_by_run_id.get(run.run_id, {}).items():
            severity_distribution[severity] = severity_distribution.get(severity, 0) + count
        deterministic_count += len(effect.get("deterministic_objection_ids") or [])
        model_count += len(effect.get("model_objection_ids") or [])

    run_count = len(shadow_runs)
    return CriticShadowReport(
        run_count=run_count,
        objection_rate=_rate(objection_runs, run_count),
        decision_change_rate=_rate(decision_change_runs, run_count),
        severity_distribution=severity_distribution,
        deterministic_vs_model_split={
            "deterministic": deterministic_count,
            "model": model_count,
        },
    )


def render_shadow_markdown(report: CriticShadowReport) -> str:
    lines = [
        "# Critic shadow report",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| runs | {report.run_count} |",
        f"| objection_rate | {report.objection_rate:.3f} |",
        f"| decision_change_rate | {report.decision_change_rate:.3f} |",
        f"| deterministic_objections | {report.deterministic_vs_model_split['deterministic']} |",
        f"| model_objections | {report.deterministic_vs_model_split['model']} |",
    ]
    for severity in ("high", "medium", "low"):
        lines.append(f"| severity:{severity} | {report.severity_distribution.get(severity, 0)} |")
    return "\n".join(lines) + "\n"


def _run_policy_case(case: CriticGoldenCase) -> CriticCaseResult:
    critique_result = _require_value(case.critique_result, case.filename, "critique_result")
    deterministic_objections = _deterministic_objections(case)
    effect = apply_critic_policy(
        mode=case.mode,
        response=case.response.model_copy(deep=True),
        request=case.request,
        baseline=case.baseline,
        critique_result=critique_result,
        deterministic_objections=deterministic_objections,
    )
    _assert_effect(case, effect)
    if case.shadow_matches_off:
        _assert_shadow_matches_off(case, critique_result, deterministic_objections, effect)
    return CriticCaseResult(
        name=case.name,
        case_type=case.case_type,
        layer=_POLICY_LAYER,
        passed=True,
        failure_message=None,
        effect=effect,
        validation=critique_result.validation,
    )


def _run_model_failure_case(case: CriticGoldenCase) -> CriticCaseResult:
    failure_config = _require_value(case.model_failure, case.filename, "model_failure")
    model_port = _model_port_from_failure(failure_config)
    critic = Critic(model_port=model_port, max_retries=0)
    critique_result = critic.review(case.request)
    deterministic_objections = _deterministic_objections(case)
    effect = apply_critic_policy(
        mode=case.mode,
        response=case.response.model_copy(deep=True),
        request=case.request,
        baseline=case.baseline,
        critique_result=critique_result,
        deterministic_objections=deterministic_objections,
    )
    _assert_effect(case, effect)
    _assert_validation(case, critique_result.validation)
    return CriticCaseResult(
        name=case.name,
        case_type=case.case_type,
        layer=_MODEL_FAILURE_LAYER,
        passed=True,
        failure_message=None,
        effect=effect,
        validation=critique_result.validation,
    )


def _run_workflow_case(case: CriticGoldenCase) -> CriticCaseResult:
    critique_results = list(case.critique_results)
    if not critique_results:
        critique_results = [_require_value(case.critique_result, case.filename, "critique_result")]
    revision_outcome = _require_value(case.revision_outcome, case.filename, "revision_outcome")
    review_call_count = 0
    revision_call_count = 0
    synthesizer_payload = {
        "decision": case.response.decision,
        "confidence": case.response.confidence,
        "reasons": list(case.response.reasons),
    }
    events = [
        _event("decision_synthesizer", text=json.dumps(synthesizer_payload)),
        _event("critic_guardrail", text="reviewed"),
    ]

    def _review(self, critique_request):
        del self, critique_request
        nonlocal review_call_count
        review_call_count += 1
        if not critique_results:
            raise AssertionError(f"{case.filename}: workflow requested an unexpected extra critique review.")
        return critique_results.pop(0)

    def _request_revision(self, **kwargs):
        del self, kwargs
        nonlocal revision_call_count
        revision_call_count += 1
        synthesis = workflow_module.DecisionSynthesis(
            decision=revision_outcome.decision,
            confidence=revision_outcome.confidence,
            reasons=list(revision_outcome.reasons),
        )
        return workflow_module._DecisionRevisionResult(
            synthesis=synthesis,
            raw_output=revision_outcome.raw_output,
            token_usage=revision_outcome.token_usage,
            latency_ms=revision_outcome.latency_ms,
            error_reason=revision_outcome.error_reason,
            error_detail=revision_outcome.error_detail,
        )

    with patch.object(workflow_module, "resolve_ticker", lambda ticker_reference: _Resolved()), patch.object(
        workflow_module,
        "analyze_request",
        lambda request, engine=None: case.response.model_copy(deep=True),
    ), patch.object(
        workflow_module,
        "build_agent_registry",
        lambda config, engine=None, today=None: AgentRegistry(tools={}, specialists={}, root_agent=None),
    ), patch.object(
        workflow_module,
        "build_critique_request",
        lambda *args, **kwargs: case.request,
    ), patch.object(
        workflow_module,
        "evaluate_deterministic_objections",
        lambda **kwargs: _deterministic_objections(case),
    ), patch.object(
        workflow_module,
        "_critic_mode",
        lambda: case.mode,
    ), patch.object(
        workflow_module.AgenticAnalysisWorkflow,
        "_persist_trace",
        lambda self, trace: None,
    ), patch.object(
        workflow_module.AgenticAnalysisWorkflow,
        "_review_critique",
        _review,
    ), patch.object(
        workflow_module.AgenticAnalysisWorkflow,
        "_request_synthesis_revision",
        _request_revision,
    ):
        workflow = AgenticAnalysisWorkflow(
            load_agent_config(Settings(_env_file=None)),
            runner_factory=lambda registry, ticker: iter(events),
            llm_io_recorder=_NoopRecorder(),
        )
        analyzed = workflow.analyze(
            case.response.ticker,
            request=AnalyzeRequest(ticker=case.response.ticker, as_of_date=case.request.as_of_date),
        )

    effect = _require_value(analyzed.critic_effect, case.filename, "critic_effect")
    _assert_effect(case, effect)
    actual_synthesizer_calls = 1 + revision_call_count
    _assert_runtime_count(
        case.filename,
        "decision_synthesizer_call_count",
        case.decision_synthesizer_call_count,
        actual_synthesizer_calls,
    )
    _assert_runtime_count(
        case.filename,
        "critic_review_call_count",
        case.critic_review_call_count,
        review_call_count,
    )
    _assert_runtime_count(
        case.filename,
        "unused_critique_result_count",
        case.unused_critique_result_count,
        len(critique_results),
    )
    return CriticCaseResult(
        name=case.name,
        case_type=case.case_type,
        layer=_WORKFLOW_LAYER,
        passed=True,
        failure_message=None,
        effect=effect,
        decision_synthesizer_call_count=actual_synthesizer_calls,
        critic_review_call_count=review_call_count,
        unused_critique_result_count=len(critique_results),
    )


def _case_from_payload(filename: str, payload: dict[str, Any]) -> CriticGoldenCase:
    case_type = _required_str(payload, filename, "case_type")
    if case_type not in {_POLICY_LAYER, _WORKFLOW_LAYER, _MODEL_FAILURE_LAYER}:
        raise ValueError(f"{filename}: unknown case_type '{case_type}'.")
    mode = _required_str(payload, filename, "mode")
    if mode not in {"off", "shadow", "enforced"}:
        raise ValueError(f"{filename}: unknown mode '{mode}'.")
    request = _critique_request(payload.get("request"), filename)
    baseline = _draft(payload.get("baseline"), filename, label="baseline")
    response = _analyze_response(payload.get("response"), filename)
    expected = _mapping(payload.get("expected"), filename, "expected")
    deterministic_payload = payload.get("deterministic_objections", {"strategy": "literal", "objections": []})
    deterministic_strategy, deterministic_objections = _deterministic_spec(deterministic_payload, filename)

    critique_result = None
    if payload.get("critique_result") is not None:
        critique_result = _critique_result(payload["critique_result"], filename)
    critique_results = tuple(
        _critique_result(case_payload, filename) for case_payload in payload.get("critique_results", [])
    )
    revision_outcome = None
    if payload.get("revision_outcome") is not None:
        revision_outcome = _revision_outcome(payload["revision_outcome"], filename)

    return CriticGoldenCase(
        name=Path(filename).stem,
        filename=filename,
        case_type=cast(Literal["policy", "workflow_revision", "model_failure"], case_type),
        mode=cast(Literal["off", "shadow", "enforced"], mode),
        notes=str(payload.get("notes", "")).strip(),
        request=request,
        baseline=baseline,
        response=response,
        expected_effect=_mapping(expected.get("effect", {}), filename, "expected.effect"),
        deterministic_strategy=deterministic_strategy,
        deterministic_objections=deterministic_objections,
        critique_result=critique_result,
        critique_results=critique_results,
        revision_outcome=revision_outcome,
        model_failure=_optional_mapping(payload.get("model_failure"), filename, "model_failure"),
        expected_validation=_optional_mapping(expected.get("validation"), filename, "expected.validation"),
        shadow_matches_off=bool(expected.get("shadow_matches_off", False)),
        decision_synthesizer_call_count=_optional_int(
            expected.get("decision_synthesizer_call_count"),
            filename,
            "expected.decision_synthesizer_call_count",
        ),
        critic_review_call_count=_optional_int(
            expected.get("critic_review_call_count"),
            filename,
            "expected.critic_review_call_count",
        ),
        unused_critique_result_count=_optional_int(
            expected.get("unused_critique_result_count"),
            filename,
            "expected.unused_critique_result_count",
        ),
    )


def _deterministic_spec(
    payload: Any,
    filename: str,
) -> tuple[Literal["evaluate", "literal"], tuple[CritiqueObjection, ...]]:
    mapping = _mapping(payload, filename, "deterministic_objections")
    strategy = _required_str(mapping, filename, "deterministic_objections.strategy")
    if strategy not in {"evaluate", "literal"}:
        raise ValueError(
            f"{filename}: deterministic_objections.strategy must be 'evaluate' or 'literal'."
        )
    objections = tuple(
        _critique_objection(objection_payload, filename)
        for objection_payload in mapping.get("objections", [])
    )
    return cast(Literal["evaluate", "literal"], strategy), objections


def _deterministic_objections(case: CriticGoldenCase) -> list[CritiqueObjection]:
    if case.deterministic_strategy == "evaluate":
        return evaluate_deterministic_objections(request=case.request, baseline=case.baseline)
    return list(case.deterministic_objections)


def _assert_effect(case: CriticGoldenCase, effect: CriticEffect) -> None:
    actual = effect.model_dump(mode="json")
    mismatches: list[str] = []
    for field_name, expected_value in case.expected_effect.items():
        actual_value = actual.get(field_name)
        if not _values_match(actual_value, expected_value):
            mismatches.append(
                f"{field_name}: expected {expected_value!r}, got {actual_value!r}"
            )
    if mismatches:
        raise AssertionError(f"{case.filename}: effect mismatch\n" + "\n".join(mismatches))


def _assert_validation(case: CriticGoldenCase, validation: CritiqueValidation) -> None:
    if case.expected_validation is None:
        return
    actual = validation.model_dump(mode="json")
    mismatches: list[str] = []
    for field_name, expected_value in case.expected_validation.items():
        actual_value = actual.get(field_name)
        if not _values_match(actual_value, expected_value):
            mismatches.append(
                f"{field_name}: expected {expected_value!r}, got {actual_value!r}"
            )
    if mismatches:
        raise AssertionError(f"{case.filename}: validation mismatch\n" + "\n".join(mismatches))


def _assert_shadow_matches_off(
    case: CriticGoldenCase,
    critique_result: CritiqueResult,
    deterministic_objections: list[CritiqueObjection],
    shadow_effect: CriticEffect,
) -> None:
    # `apply_critic_policy` fully computes the counterfactual effect in shadow
    # mode too, so `shadow_effect.final_decision`/`final_confidence` may
    # legitimately differ from the pre-critique response when an objection
    # would (if enforced) change the outcome. What "shadow is byte-identical
    # to off" actually guarantees is: the caller's `response` argument is
    # never mutated by the policy call itself in either mode — only the
    # workflow decides, based on mode, whether to apply the effect to the
    # live response (and it never does in `shadow` or `off`).
    response_snapshot = case.response.model_copy(deep=True)
    off_effect = apply_critic_policy(
        mode="off",
        response=case.response.model_copy(deep=True),
        request=case.request,
        baseline=case.baseline,
        critique_result=critique_result,
        deterministic_objections=deterministic_objections,
    )
    if case.response.decision != response_snapshot.decision or not math.isclose(
        case.response.confidence, response_snapshot.confidence, abs_tol=1e-9
    ):
        raise AssertionError(f"{case.filename}: response argument was mutated by apply_critic_policy.")

    shadow_dump = shadow_effect.model_dump(mode="json")
    off_dump = off_effect.model_dump(mode="json")
    shadow_dump.pop("mode", None)
    off_dump.pop("mode", None)
    if shadow_dump != off_dump:
        raise AssertionError(
            f"{case.filename}: shadow effect diverged from off effect when excluding mode."
        )
    if not deterministic_objections and not critique_result.response.objections:
        raise AssertionError(
            f"{case.filename}: shadow_matches_off case must exercise at least one objection."
        )


def _assert_runtime_count(
    filename: str,
    label: str,
    expected_value: int | None,
    actual_value: int,
) -> None:
    if expected_value is None:
        return
    if expected_value != actual_value:
        raise AssertionError(f"{filename}: {label} expected {expected_value}, got {actual_value}")


def _values_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, float):
        return isinstance(actual, (float, int)) and math.isclose(float(actual), expected, abs_tol=1e-9)
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(_values_match(actual_item, expected_item) for actual_item, expected_item in zip(actual, expected))
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        if set(actual) != set(expected):
            return False
        return all(_values_match(actual[key], expected[key]) for key in expected)
    return actual == expected


def _severity_counts(step: AgentRunStep) -> dict[str, int]:
    output = step.output or {}
    counts = output.get("objection_counts_by_severity") or {}
    return {
        severity: int(counts.get(severity, 0))
        for severity in ("high", "medium", "low")
    }


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _revision_outcome(payload: Any, filename: str) -> RevisionOutcomeFixture:
    mapping = _mapping(payload, filename, "revision_outcome")
    reasons = mapping.get("reasons") or []
    if not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons):
        raise ValueError(f"{filename}: revision_outcome.reasons must be a list of strings.")
    return RevisionOutcomeFixture(
        decision=cast(
            Literal["trade", "watchlist", "no_trade"],
            _required_str(mapping, filename, "revision_outcome.decision"),
        ),
        confidence=float(mapping.get("confidence")),
        reasons=tuple(reasons),
        raw_output=str(mapping.get("raw_output", "")),
        token_usage=_token_usage(mapping.get("token_usage", {}), filename, "revision_outcome.token_usage"),
        latency_ms=float(mapping.get("latency_ms", 0.0)),
        error_reason=(str(mapping["error_reason"]) if mapping.get("error_reason") is not None else None),
        error_detail=(str(mapping["error_detail"]) if mapping.get("error_detail") is not None else None),
    )


def _draft(payload: Any, filename: str, *, label: str) -> DraftRecommendation:
    mapping = _mapping(payload, filename, label)
    construct, data = _strip_construct(mapping)
    data = _normalize_recommendation_payload(data)
    if construct:
        return DraftRecommendation.model_construct(**data)
    return DraftRecommendation.model_validate(data)


def _analyze_response(payload: Any, filename: str) -> AnalyzeResponse:
    mapping = _mapping(payload, filename, "response")
    return AnalyzeResponse.model_validate(mapping)


def _critique_result(payload: Any, filename: str) -> CritiqueResult:
    mapping = _mapping(payload, filename, "critique_result")
    response = _critique_response(mapping.get("response"), filename)
    validation = CritiqueValidation.model_validate(_mapping(mapping.get("validation"), filename, "critique_result.validation"))
    data = {
        "response": response,
        "validation": validation,
        "raw_output": str(mapping.get("raw_output", "{}")),
        "latency_ms": float(mapping.get("latency_ms", 0.0)),
        "token_usage": _token_usage(mapping.get("token_usage", {}), filename, "critique_result.token_usage"),
        "model_name": str(mapping.get("model_name", "fixture-critic")),
        "prompt_version": str(mapping.get("prompt_version", "critic-guardrail.v1")),
    }
    return CritiqueResult.model_validate(data)


def _critique_response(payload: Any, filename: str) -> CritiqueResponse:
    mapping = _mapping(payload, filename, "critique_result.response")
    construct, data = _strip_construct(mapping)
    data["objections"] = [_critique_objection(item, filename) for item in data.get("objections", [])]
    if construct:
        return CritiqueResponse.model_construct(**data)
    return CritiqueResponse.model_validate(data)


def _critique_request(payload: Any, filename: str) -> CritiqueRequest:
    mapping = _mapping(payload, filename, "request")
    construct, data = _strip_construct(mapping)
    draft, draft_constructed = _draft_with_flag(data.get("draft"), filename)
    views: list[AnalystView] = []
    view_constructed = False
    for index, view_payload in enumerate(data.get("analyst_views", [])):
        view, was_constructed = _analyst_view_with_flag(view_payload, filename, index)
        views.append(view)
        view_constructed = view_constructed or was_constructed
    chunks = [EvidenceChunkInput.model_validate(chunk) for chunk in data.get("evidence_chunks", [])]
    data = {
        **data,
        "draft": draft,
        "analyst_views": views,
        "evidence_chunks": chunks,
    }
    if construct or draft_constructed or view_constructed:
        return CritiqueRequest.model_construct(**data)
    return CritiqueRequest.model_validate(data)


def _draft_with_flag(payload: Any, filename: str) -> tuple[DraftRecommendation, bool]:
    mapping = _mapping(payload, filename, "request.draft")
    construct, data = _strip_construct(mapping)
    data = _normalize_recommendation_payload(data)
    if construct:
        return DraftRecommendation.model_construct(**data), True
    return DraftRecommendation.model_validate(data), False


def _analyst_view_with_flag(payload: Any, filename: str, index: int) -> tuple[AnalystView, bool]:
    label = f"request.analyst_views[{index}]"
    mapping = _mapping(payload, filename, label)
    construct, data = _strip_construct(mapping)
    findings: list[CitedFinding] = []
    finding_constructed = False
    for finding_index, finding_payload in enumerate(data.get("findings", [])):
        finding, was_constructed = _finding_with_flag(finding_payload, filename, label, finding_index)
        findings.append(finding)
        finding_constructed = finding_constructed or was_constructed
    data = {**data, "findings": findings}
    if construct or finding_constructed:
        return AnalystView.model_construct(**data), True
    return AnalystView.model_validate(data), False


def _finding_with_flag(
    payload: Any,
    filename: str,
    parent_label: str,
    index: int,
) -> tuple[CitedFinding, bool]:
    label = f"{parent_label}.findings[{index}]"
    mapping = _mapping(payload, filename, label)
    construct, data = _strip_construct(mapping)
    if construct:
        return CitedFinding.model_construct(**data), True
    return CitedFinding.model_validate(data), False


def _critique_objection(payload: Any, filename: str) -> CritiqueObjection:
    mapping = _mapping(payload, filename, "objection")
    construct, data = _strip_construct(mapping)
    if construct:
        return CritiqueObjection.model_construct(**data)
    return CritiqueObjection.model_validate(data)


def _event(
    author: str,
    *,
    text: str = "",
    token_usage: dict[str, int] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
):
    parts = []
    if text:
        parts.append(SimpleNamespace(text=text))
    return SimpleNamespace(
        author=author,
        content=SimpleNamespace(parts=parts),
        usage_metadata=SimpleNamespace(**(token_usage or {})),
        error_code=error_code,
        error_message=error_message,
    )


def _model_port_from_failure(config: dict[str, Any]):
    failure_type = str(config.get("type", "")).strip()
    if failure_type == "retryable_error":
        return _RaisingModelPort(str(config.get("message", "timeout")))
    if failure_type == "garbage_output":
        return _GarbageModelPort(str(config.get("text", "not json")))
    raise ValueError(f"model_failure.type must be 'retryable_error' or 'garbage_output'; got {failure_type!r}.")


def _required_str(mapping: dict[str, Any], filename: str, key: str) -> str:
    field_name = key.rsplit(".", maxsplit=1)[-1]
    value = mapping.get(field_name)
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"{filename}: missing non-empty '{key}'.")


def _mapping(payload: Any, filename: str, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{filename}: '{label}' must be an object.")
    return deepcopy(payload)


def _optional_mapping(payload: Any, filename: str, label: str) -> dict[str, Any] | None:
    if payload is None:
        return None
    return _mapping(payload, filename, label)


def _optional_int(payload: Any, filename: str, label: str) -> int | None:
    if payload is None:
        return None
    if not isinstance(payload, int):
        raise ValueError(f"{filename}: '{label}' must be an integer.")
    return payload


def _strip_construct(mapping: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    data = deepcopy(mapping)
    construct = bool(data.pop(_CONSTRUCT_KEY, False))
    return construct, data


def _token_usage(payload: Any, filename: str, label: str) -> dict[str, int]:
    mapping = _mapping(payload, filename, label)
    normalized: dict[str, int] = {}
    for key, value in mapping.items():
        if not isinstance(value, int):
            raise ValueError(f"{filename}: '{label}.{key}' must be an integer.")
        normalized[str(key)] = value
    return normalized


def _normalize_recommendation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for field_name in ("entry_range", "take_profit"):
        value = normalized.get(field_name)
        if isinstance(value, list):
            normalized[field_name] = tuple(value)
    return normalized


def _require_value(value: Any, filename: str, label: str):
    if value is None:
        raise ValueError(f"{filename}: missing required '{label}'.")
    return value
