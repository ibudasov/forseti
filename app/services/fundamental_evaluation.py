from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.db.models import AgentRun
from app.schemas.fundamental_evaluation import (
    FundamentalEvaluationCase,
    FundamentalEvaluationCaseReport,
    FundamentalEvaluationGateResult,
    FundamentalEvaluationMetrics,
    FundamentalEvaluationReport,
    FundamentalEvaluationSuite,
    FundamentalReviewSample,
    FundamentalShadowReport,
)
from app.schemas.fundamentals import FundamentalAssessmentResult, FundamentalAssessmentValidation, SCHEMA_VERSION
from app.services.fundamental_analyst import FundamentalAnalyst, validate_fundamental_assessment
from app.services.fundamental_context import compute_fundamental_context_hash
from app.services.fundamental_policy import apply_fundamental_policy

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVALUATION_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "fundamental_agent_eval" / "suite.json"
REVIEW_SAMPLE_SIZE = 5
INPUT_COST_USD_PER_MILLION_TOKENS = 0.10
OUTPUT_COST_USD_PER_MILLION_TOKENS = 0.40
STRUCTURED_OUTPUT_FAILURES = {
    "forbidden_response_field",
    "malformed_json",
    "schema_validation_failed",
}
PROVIDER_FAILURE_PREFIXES = ("provider_error:", "model_failure")
FALLBACK_SUMMARY = "Fundamental Analyst returned a neutral fallback."


def load_fundamental_evaluation_suite(path: Path = DEFAULT_EVALUATION_FIXTURE) -> FundamentalEvaluationSuite:
    return FundamentalEvaluationSuite.model_validate_json(path.read_text(encoding="utf-8"))


def validate_fundamental_evaluation_suite(suite: FundamentalEvaluationSuite) -> None:
    for case in suite.cases:
        _validate_case_fixture(case)


def evaluate_recorded_suite(
    suite: FundamentalEvaluationSuite,
) -> FundamentalEvaluationReport:
    return _evaluate_suite(suite, result_provider=lambda case: case.recorded_result)


def evaluate_live_suite(
    suite: FundamentalEvaluationSuite,
    *,
    analyst: FundamentalAnalyst | None = None,
) -> FundamentalEvaluationReport:
    resolved_analyst = analyst or FundamentalAnalyst()
    return _evaluate_suite(suite, result_provider=lambda case: resolved_analyst.assess(case.request))


def build_shadow_report(runs: list[AgentRun]) -> FundamentalShadowReport:
    effects = [
        run
        for run in runs
        if run.fundamental_agent_effect is not None
        and run.fundamental_agent_effect.get("mode") == "shadow"
    ]
    outcome_counts = Counter()
    status_counts = Counter()
    rejection_reason_counts = Counter()
    missing_information_counts = Counter()
    version_breakdown = Counter()
    baseline_counterfactual_matrix = Counter()
    latencies = [run.total_latency_ms for run in effects if run.total_latency_ms > 0]
    total_prompt_tokens = 0
    total_candidate_tokens = 0
    total_tokens = 0

    for run in effects:
        effect = run.fundamental_agent_effect or {}
        baseline = str(effect.get("baseline_decision", "no_trade"))
        counterfactual = str(effect.get("counterfactual_decision", baseline))
        outcome_counts[_outcome(baseline, counterfactual)] += 1
        status_counts[str(effect.get("assessment_status", "failed"))] += 1
        baseline_counterfactual_matrix[f"{baseline}->{counterfactual}"] += 1
        for reason_code in effect.get("reason_codes", []):
            rejection_reason_counts[str(reason_code)] += 1
        for missing_entry in effect.get("missing_information", []):
            missing_information_counts[str(missing_entry)] += 1
        for source_type in effect.get("source_types_missing", []):
            missing_information_counts[f"source:{source_type}"] += 1
        version_breakdown[_version_key(effect.get("versions") or {})] += 1
        prompt_tokens, candidate_tokens, observed_total = _token_totals(run.token_usage)
        total_prompt_tokens += prompt_tokens
        total_candidate_tokens += candidate_tokens
        total_tokens += observed_total

    return FundamentalShadowReport(
        run_count=len(effects),
        version_breakdown=dict(sorted(version_breakdown.items())),
        outcome_counts=_sorted_counter(outcome_counts),
        status_counts=_sorted_counter(status_counts),
        rejection_reason_counts=_sorted_counter(rejection_reason_counts),
        missing_information_counts=_sorted_counter(missing_information_counts),
        baseline_counterfactual_matrix=_sorted_counter(baseline_counterfactual_matrix),
        latency_ms=_latency_summary(latencies),
        token_usage=_token_summary(total_prompt_tokens, total_candidate_tokens, total_tokens, len(effects)),
    )


def render_evaluation_markdown(report: FundamentalEvaluationReport) -> str:
    metrics = report.metrics
    lines = [
        "# Fundamental agent evaluation",
        "",
        f"- cases: {metrics.case_count}",
        f"- gates: {'passed' if report.gates.passed else 'failed'}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| structured_output_validity_rate | {metrics.structured_output_validity_rate:.3f} |",
        f"| citation_validity_rate | {metrics.citation_validity_rate:.3f} |",
        f"| material_claim_citation_coverage | {metrics.material_claim_citation_coverage:.3f} |",
        f"| unsupported_claim_rate | {metrics.unsupported_claim_rate:.3f} |",
        f"| abstention_rate | {metrics.abstention_rate:.3f} |",
        f"| appropriate_abstention_precision | {metrics.appropriate_abstention_precision:.3f} |",
        f"| agreement_rate | {metrics.agreement_rate:.3f} |",
        f"| duplicate_evidence_rejection_rate | {metrics.duplicate_evidence_rejection_rate:.3f} |",
        f"| hard_blocker_violation_count | {metrics.hard_blocker_violation_count} |",
        f"| risk_field_mutation_count | {metrics.risk_field_mutation_count} |",
        f"| provider_failure_rate | {metrics.provider_failure_rate:.3f} |",
        f"| neutral_fallback_rate | {metrics.neutral_fallback_rate:.3f} |",
        f"| latency_p50_ms | {metrics.latency_ms['p50']:.1f} |",
        f"| latency_p90_ms | {metrics.latency_ms['p90']:.1f} |",
        f"| latency_max_ms | {metrics.latency_ms['max']:.1f} |",
        f"| total_tokens | {int(metrics.token_usage['total_token_count'])} |",
        f"| estimated_cost_usd | {metrics.token_usage['estimated_cost_usd']:.6f} |",
    ]
    if report.gates.failures:
        lines.extend(["", "## Gate failures", ""])
        lines.extend(f"- {failure}" for failure in report.gates.failures)
    return "\n".join(lines) + "\n"


def render_shadow_markdown(report: FundamentalShadowReport) -> str:
    lines = [
        "# Fundamental shadow report",
        "",
        f"- runs: {report.run_count}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| latency_p50_ms | {report.latency_ms['p50']:.1f} |",
        f"| latency_p90_ms | {report.latency_ms['p90']:.1f} |",
        f"| latency_max_ms | {report.latency_ms['max']:.1f} |",
        f"| total_tokens | {int(report.token_usage['total_token_count'])} |",
        f"| estimated_cost_usd | {report.token_usage['estimated_cost_usd']:.6f} |",
    ]
    for name, count in report.outcome_counts.items():
        lines.append(f"| outcome:{name} | {count} |")
    for name, count in report.status_counts.items():
        lines.append(f"| status:{name} | {count} |")
    return "\n".join(lines) + "\n"


def _evaluate_suite(
    suite: FundamentalEvaluationSuite,
    *,
    result_provider,
) -> FundamentalEvaluationReport:
    validate_fundamental_evaluation_suite(suite)
    case_reports: list[FundamentalEvaluationCaseReport] = []
    review_samples: list[FundamentalReviewSample] = []
    structured_valid_cases = 0
    supported_citation_count = 0
    total_citation_count = 0
    cited_material_claim_count = 0
    total_material_claim_count = 0
    unsupported_claim_count = 0
    total_claim_count = 0
    abstention_count = 0
    appropriate_abstention_count = 0
    agreement_count = 0
    duplicate_rejection_count = 0
    hard_blocker_violation_count = 0
    risk_field_mutation_count = 0
    provider_failure_count = 0
    neutral_fallback_count = 0
    adjustment_distribution = Counter({str(value): 0 for value in range(-2, 3)})
    outcome_counts = Counter()
    status_counts = Counter()
    confusion_matrix = Counter()
    latencies: list[float] = []
    total_prompt_tokens = 0
    total_candidate_tokens = 0
    total_tokens = 0
    gate_context: list[tuple[FundamentalEvaluationCase, FundamentalAssessmentResult, object]] = []

    for case in suite.cases:
        result = _effective_result(case, result_provider(case))
        effect = apply_fundamental_policy(
            mode="shadow",
            deterministic_response=case.deterministic_response.model_copy(deep=True),
            request=case.request,
            assessment_result=result,
        )
        gate_context.append((case, result, effect))

        if _is_structured_output_valid(result):
            structured_valid_cases += 1
        citations = _citation_counts(result, case)
        supported_citation_count += citations["supported"]
        total_citation_count += citations["total"]
        material_claims = _material_claim_counts(result)
        cited_material_claim_count += material_claims["cited"]
        total_material_claim_count += material_claims["total"]
        unsupported_claim_count += _unsupported_claim_count(result, case)
        total_claim_count += len(_all_claims(result))
        if result.response.status != "completed":
            abstention_count += 1
            if case.label.appropriate_abstention:
                appropriate_abstention_count += 1
        agreement = _matches_label(case, result, effect)
        if agreement:
            agreement_count += 1
        if "duplicate_evidence_detected" in effect.reason_codes:
            duplicate_rejection_count += 1
        if _has_hard_blocker_violation(case, effect):
            hard_blocker_violation_count += 1
        if _risk_fields_mutated(case, effect):
            risk_field_mutation_count += 1
        if _is_provider_failure(result):
            provider_failure_count += 1
        if _is_neutral_fallback(result):
            neutral_fallback_count += 1

        adjustment_distribution[str(result.response.proposed_score_adjustment)] += 1
        outcome = _outcome(case.deterministic_response.decision, effect.counterfactual_decision)
        outcome_counts[outcome] += 1
        status_counts[result.response.status] += 1
        confusion_matrix[f"{case.deterministic_response.decision}->{effect.counterfactual_decision}"] += 1
        if result.latency_ms > 0:
            latencies.append(result.latency_ms)
        prompt_tokens, candidate_tokens, observed_total = _token_totals(result.token_usage)
        total_prompt_tokens += prompt_tokens
        total_candidate_tokens += candidate_tokens
        total_tokens += observed_total
        case_reports.append(
            FundamentalEvaluationCaseReport(
                case_id=case.case_id,
                category=case.category,
                baseline_decision=case.deterministic_response.decision,
                counterfactual_decision=effect.counterfactual_decision,
                status=result.response.status,
                accepted=result.validation.accepted,
                agreement=agreement,
                adjustment=result.response.proposed_score_adjustment,
                outcome=outcome,
                reason_codes=list(result.validation.reason_codes),
            )
        )
        if len(review_samples) < REVIEW_SAMPLE_SIZE:
            review_samples.append(_review_sample(case, result, effect))

    case_count = len(suite.cases)
    metrics = FundamentalEvaluationMetrics(
        case_count=case_count,
        structured_output_validity_rate=_rate(structured_valid_cases, case_count),
        citation_validity_rate=_rate(supported_citation_count, total_citation_count, default=1.0),
        material_claim_citation_coverage=_rate(cited_material_claim_count, total_material_claim_count, default=1.0),
        unsupported_claim_rate=_rate(unsupported_claim_count, total_claim_count, default=0.0),
        abstention_rate=_rate(abstention_count, case_count),
        appropriate_abstention_precision=_rate(appropriate_abstention_count, abstention_count, default=1.0),
        agreement_rate=_rate(agreement_count, case_count),
        duplicate_evidence_rejection_rate=_rate(duplicate_rejection_count, case_count),
        hard_blocker_violation_count=hard_blocker_violation_count,
        risk_field_mutation_count=risk_field_mutation_count,
        provider_failure_rate=_rate(provider_failure_count, case_count),
        neutral_fallback_rate=_rate(neutral_fallback_count, case_count),
        adjustment_distribution=dict(adjustment_distribution),
        outcome_counts=_sorted_counter(outcome_counts),
        status_counts=_sorted_counter(status_counts),
        counterfactual_confusion_matrix=_sorted_counter(confusion_matrix),
        latency_ms=_latency_summary(latencies),
        token_usage=_token_summary(total_prompt_tokens, total_candidate_tokens, total_tokens, case_count),
    )
    gates = _gate_result(gate_context)
    return FundamentalEvaluationReport(
        metrics=metrics,
        gates=gates,
        cases=case_reports,
        review_samples=review_samples,
    )


def _validate_case_fixture(case: FundamentalEvaluationCase) -> None:
    if compute_fundamental_context_hash(case.request) != case.request.context_hash:
        raise ValueError(f"{case.case_id}: context_hash_mismatch")
    if case.request.schema_version != SCHEMA_VERSION:
        raise ValueError(f"{case.case_id}: unsupported_request_schema")
    for chunk in case.request.evidence_chunks:
        if chunk.published_at is not None and chunk.published_at > case.request.snapshot_at:
            raise ValueError(f"{case.case_id}: future_dated_evidence:{chunk.chunk_id}")


def _effective_result(
    case: FundamentalEvaluationCase,
    result: FundamentalAssessmentResult,
) -> FundamentalAssessmentResult:
    validation = validate_fundamental_assessment(result.response, case.request)
    combined_reason_codes = list(dict.fromkeys([*result.validation.reason_codes, *validation.reason_codes]))
    accepted = result.validation.accepted and validation.accepted
    return result.model_copy(
        update={
            "validation": FundamentalAssessmentValidation(
                accepted=accepted,
                reason_codes=combined_reason_codes,
            )
        }
    )


def _citation_counts(result: FundamentalAssessmentResult, case: FundamentalEvaluationCase) -> dict[str, int]:
    known_metric_ids = {
        point.metric_id
        for series in case.request.metric_series
        for point in [*series.annual, *series.quarterly]
    }
    known_metric_ids.update(metric.metric_id for metric in case.request.deterministic_result.metrics)
    known_chunk_ids = {chunk.chunk_id for chunk in case.request.evidence_chunks}
    supported = 0
    total = 0
    for finding in _all_claims(result):
        for metric_id in finding.metric_ids:
            total += 1
            if metric_id in known_metric_ids:
                supported += 1
        for chunk_id in finding.chunk_ids:
            total += 1
            if chunk_id in known_chunk_ids:
                supported += 1
    return {"supported": supported, "total": total}


def _material_claim_counts(result: FundamentalAssessmentResult) -> dict[str, int]:
    cited = 0
    total = 0
    for finding in _all_claims(result):
        if finding.materiality not in {"medium", "high"}:
            continue
        total += 1
        if finding.metric_ids or finding.chunk_ids:
            cited += 1
    return {"cited": cited, "total": total}


def _unsupported_claim_count(result: FundamentalAssessmentResult, case: FundamentalEvaluationCase) -> int:
    known_metric_ids = {
        point.metric_id
        for series in case.request.metric_series
        for point in [*series.annual, *series.quarterly]
    }
    known_metric_ids.update(metric.metric_id for metric in case.request.deterministic_result.metrics)
    known_chunk_ids = {chunk.chunk_id for chunk in case.request.evidence_chunks}
    unsupported = 0
    for finding in _all_claims(result):
        missing_metric = any(metric_id not in known_metric_ids for metric_id in finding.metric_ids)
        missing_chunk = any(chunk_id not in known_chunk_ids for chunk_id in finding.chunk_ids)
        missing_citation = finding.materiality in {"medium", "high"} and not (finding.metric_ids or finding.chunk_ids)
        if missing_metric or missing_chunk or missing_citation:
            unsupported += 1
    return unsupported


def _matches_label(
    case: FundamentalEvaluationCase,
    result: FundamentalAssessmentResult,
    effect,
) -> bool:
    label = case.label
    claims = [finding.claim.lower() for finding in _all_claims(result)]
    red_flags = [finding.claim.lower() for finding in result.response.material_red_flags]
    if result.response.status != label.expected_status:
        return False
    if result.response.proposed_score_adjustment < label.acceptable_adjustment.minimum:
        return False
    if result.response.proposed_score_adjustment > label.acceptable_adjustment.maximum:
        return False
    if label.acceptable_signals and result.response.overall_signal not in label.acceptable_signals:
        return False
    if effect.counterfactual_decision != label.expected_counterfactual_decision:
        return False
    if label.appropriate_abstention != (result.response.status != "completed"):
        return False
    if not _contains_all_keywords(claims, label.required_claim_keywords):
        return False
    if not _contains_all_keywords(red_flags, label.required_red_flag_keywords):
        return False
    if _contains_any_keyword(claims + [result.response.summary.lower()], label.forbidden_claim_keywords):
        return False
    return True


def _contains_all_keywords(claims: list[str], keywords: list[str]) -> bool:
    return all(any(keyword.lower() in claim for claim in claims) for keyword in keywords)


def _contains_any_keyword(claims: list[str], keywords: list[str]) -> bool:
    return any(keyword.lower() in claim for keyword in keywords for claim in claims)


def _all_claims(result: FundamentalAssessmentResult):
    return [
        *result.response.findings,
        *result.response.contradictions,
        *result.response.material_red_flags,
    ]


def _is_structured_output_valid(result: FundamentalAssessmentResult) -> bool:
    return not any(
        reason_code == code or reason_code.startswith(f"{code}:")
        for reason_code in result.validation.reason_codes
        for code in STRUCTURED_OUTPUT_FAILURES
    )


def _has_hard_blocker_violation(case: FundamentalEvaluationCase, effect) -> bool:
    if not _has_hard_blocker(case):
        return False
    return _decision_rank(effect.counterfactual_decision) > _decision_rank(case.deterministic_response.decision)


def _has_hard_blocker(case: FundamentalEvaluationCase) -> bool:
    diagnosis = case.deterministic_response.diagnosis
    if diagnosis is not None and diagnosis.stage in {"unknown_security", "data_gate", "hard_veto", "risk_math"}:
        return True
    return any(
        warning in {"security_inactive", "no_price_data", "insufficient_price_data", "stale_price_data", "no_fundamentals"}
        for warning in case.deterministic_response.warnings
    )


def _risk_fields_mutated(case: FundamentalEvaluationCase, effect) -> bool:
    if effect.counterfactual_decision == case.deterministic_response.decision:
        return False
    return False


def _is_provider_failure(result: FundamentalAssessmentResult) -> bool:
    return any(
        reason_code == prefix or reason_code.startswith(prefix)
        for reason_code in result.validation.reason_codes
        for prefix in PROVIDER_FAILURE_PREFIXES
    )


def _is_neutral_fallback(result: FundamentalAssessmentResult) -> bool:
    return result.response.summary == FALLBACK_SUMMARY


def _token_totals(token_usage: dict[str, int]) -> tuple[int, int, int]:
    prompt_tokens = int(token_usage.get("prompt_token_count", 0) or 0)
    candidate_tokens = int(token_usage.get("candidates_token_count", 0) or 0)
    total_token_count = int(token_usage.get("total_token_count", prompt_tokens + candidate_tokens) or 0)
    return prompt_tokens, candidate_tokens, total_token_count


def _latency_summary(latencies: list[float]) -> dict[str, float]:
    if not latencies:
        return {"p50": 0.0, "p90": 0.0, "max": 0.0}
    ordered = sorted(latencies)
    return {
        "p50": _percentile(ordered, 0.50),
        "p90": _percentile(ordered, 0.90),
        "max": ordered[-1],
    }


def _token_summary(
    total_prompt_tokens: int,
    total_candidate_tokens: int,
    total_tokens: int,
    item_count: int,
) -> dict[str, float]:
    estimated_cost_usd = (
        total_prompt_tokens * INPUT_COST_USD_PER_MILLION_TOKENS / 1_000_000
        + total_candidate_tokens * OUTPUT_COST_USD_PER_MILLION_TOKENS / 1_000_000
    )
    average_tokens = 0.0 if item_count == 0 else total_tokens / item_count
    return {
        "prompt_token_count": float(total_prompt_tokens),
        "candidates_token_count": float(total_candidate_tokens),
        "total_token_count": float(total_tokens),
        "average_total_token_count": average_tokens,
        "estimated_cost_usd": estimated_cost_usd,
    }


def _percentile(values: list[float], percentile: float) -> float:
    if len(values) == 1:
        return values[0]
    index = round((len(values) - 1) * percentile)
    return values[index]


def _rate(numerator: int, denominator: int, *, default: float = 0.0) -> float:
    if denominator == 0:
        return default
    return numerator / denominator


def _gate_result(gate_context: list[tuple[FundamentalEvaluationCase, FundamentalAssessmentResult, object]]) -> FundamentalEvaluationGateResult:
    failures: list[str] = []
    hard_blocker_violations = 0
    risk_field_mutations = 0
    accepted_unknown_citations = 0
    accepted_schema_failures = 0
    provider_failures_changed_baseline = 0

    for case, result, effect in gate_context:
        if _has_hard_blocker_violation(case, effect):
            hard_blocker_violations += 1
        if _risk_fields_mutated(case, effect):
            risk_field_mutations += 1
        if result.validation.accepted and _unsupported_claim_count(result, case) > 0:
            accepted_unknown_citations += 1
        if result.validation.accepted and not _is_structured_output_valid(result):
            accepted_schema_failures += 1
        if _is_provider_failure(result) and effect.counterfactual_decision != case.deterministic_response.decision:
            provider_failures_changed_baseline += 1

    if hard_blocker_violations:
        failures.append(f"hard_blocker_violations={hard_blocker_violations}")
    if risk_field_mutations:
        failures.append(f"risk_field_mutations={risk_field_mutations}")
    if accepted_unknown_citations:
        failures.append(f"accepted_unknown_citations={accepted_unknown_citations}")
    if accepted_schema_failures:
        failures.append(f"accepted_schema_failures={accepted_schema_failures}")
    if provider_failures_changed_baseline:
        failures.append(f"provider_failures_changed_baseline={provider_failures_changed_baseline}")

    return FundamentalEvaluationGateResult(
        passed=not failures,
        failures=failures,
    )


def _review_sample(
    case: FundamentalEvaluationCase,
    result: FundamentalAssessmentResult,
    effect,
) -> FundamentalReviewSample:
    return FundamentalReviewSample(
        case_id=case.case_id,
        category=case.category,
        baseline_decision=case.deterministic_response.decision,
        counterfactual_decision=effect.counterfactual_decision,
        coverage=case.request.coverage.model_dump(mode="json"),
        proposed_adjustment=result.response.proposed_score_adjustment,
        status=result.response.status,
        findings=[
            {
                "category": finding.category,
                "direction": finding.direction,
                "materiality": finding.materiality,
                "claim": finding.claim,
                "chunk_ids": list(finding.chunk_ids),
                "metric_ids": list(finding.metric_ids),
            }
            for finding in _all_claims(result)
        ],
        source_links=sorted({chunk.source_url for chunk in case.request.evidence_chunks}),
    )


def _decision_rank(decision: str) -> int:
    return {"no_trade": 0, "watchlist": 1, "trade": 2}[decision]


def _outcome(
    baseline_decision: str,
    counterfactual_decision: str,
) -> str:
    if _decision_rank(counterfactual_decision) > _decision_rank(baseline_decision):
        return "promotion"
    if _decision_rank(counterfactual_decision) < _decision_rank(baseline_decision):
        return "downgrade"
    return "unchanged"


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _version_key(versions: dict[str, object]) -> str:
    return (
        f"model={versions.get('model_name', 'unknown')}"
        f"|prompt={versions.get('prompt_version', 'unknown')}"
        f"|context={versions.get('context_schema_version', 'unknown')}"
        f"|assessment={versions.get('assessment_schema_version', 'unknown')}"
        f"|policy={versions.get('policy_version', 'unknown')}"
    )
