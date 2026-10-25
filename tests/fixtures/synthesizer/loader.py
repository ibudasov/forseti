from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas.synthesis import DecisionMemoResponse, DecisionSynthesisRequest
from app.services.decision_synthesizer import (
    DecisionSynthesizer,
    ModelOutput,
    build_decision_synthesis_request,
)

FIXTURE_DIR = Path(__file__).resolve().parent
_NUMBER_PATTERN = re.compile(r"(?<![\w.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?")
_DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}
_MONEY_FIELDS = {
    "entry_range",
    "stop_loss",
    "take_profit",
    "risk_reward",
    "position_size_eur",
    "time_stop_at",
}


@dataclass(frozen=True)
class SynthesizerCase:
    name: str
    request: DecisionSynthesisRequest
    response: dict[str, Any]
    expected_fallback: bool


@dataclass(frozen=True)
class SynthesizerEvaluation:
    passed: bool
    total_cases: int
    valid_schema_rate: float
    citation_precision: float
    numeric_claim_accuracy: float
    downgrade_only_compliance: float
    fallback_rate: float
    money_field_count: int
    promotion_count: int
    unsupported_numeric_claims_rejected: int
    fallback_memo_count: int
    rejected_without_fallback_count: int
    cases: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "total_cases": self.total_cases,
            "metrics": {
                "valid_schema_rate": self.valid_schema_rate,
                "citation_precision": self.citation_precision,
                "numeric_claim_accuracy": self.numeric_claim_accuracy,
                "downgrade_only_compliance": self.downgrade_only_compliance,
                "fallback_rate": self.fallback_rate,
            },
            "money_field_count": self.money_field_count,
            "promotion_count": self.promotion_count,
            "unsupported_numeric_claims_rejected": self.unsupported_numeric_claims_rejected,
            "fallback_memo_count": self.fallback_memo_count,
            "rejected_without_fallback_count": self.rejected_without_fallback_count,
            "cases": list(self.cases),
        }


class RecordedModel:
    model_name = "recorded-synthesizer"

    def __init__(self, response: dict[str, Any], request: DecisionSynthesisRequest) -> None:
        self._response = {
            "schema_version": "1.0",
            "run_id": request.run_id,
            "context_hash": request.context_hash,
            **response,
        }

    def generate(self, prompt: str) -> ModelOutput:
        del prompt
        return ModelOutput(text=json.dumps(self._response), token_usage={})


def load_synthesizer_cases(fixture_dir: Path = FIXTURE_DIR) -> list[SynthesizerCase]:
    payload = json.loads((fixture_dir / "cases.json").read_text(encoding="utf-8"))
    cases = []
    for entry in payload:
        baseline_decision = entry["baseline_decision"]
        allowed_decisions = [
            decision
            for decision, rank in _DECISION_RANK.items()
            if rank <= _DECISION_RANK[baseline_decision]
        ]
        request = build_decision_synthesis_request(
            run_id=f"synthesizer-eval:{entry['name']}",
            ticker="NVDA",
            as_of_date=date(2026, 9, 8),
            snapshot_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
            baseline_decision=baseline_decision,
            baseline_confidence=entry["baseline_confidence"],
            entry_range=(100.0, 105.0) if baseline_decision == "trade" else None,
            stop_loss=95.0 if baseline_decision == "trade" else None,
            take_profit=(110.0, 115.0) if baseline_decision == "trade" else None,
            risk_reward=2.0 if baseline_decision == "trade" else None,
            position_size_eur=250.0 if baseline_decision == "trade" else None,
            time_stop_at=date(2026, 10, 8) if baseline_decision == "trade" else None,
            reasons=entry["reasons"],
            warnings=entry["warnings"],
            diagnosis=None,
            analyst_views=entry["analyst_views"],
            known_metric_ids=entry["known_metric_ids"],
            known_chunk_ids=entry["known_chunk_ids"],
            known_warning_codes=entry["known_warning_codes"],
            allowed_decisions=allowed_decisions,
            max_confidence=entry["baseline_confidence"],
        )
        cases.append(
            SynthesizerCase(
                name=entry["name"],
                request=request,
                response=entry["response"],
                expected_fallback=entry["expected_fallback"],
            )
        )
    return cases


def evaluate_synthesizer_cases(cases: list[SynthesizerCase], *, live: bool = False) -> SynthesizerEvaluation:
    case_results = []
    valid_schema_count = 0
    fallback_count = 0
    citation_count = 0
    valid_citation_count = 0
    numeric_case_count = 0
    accurate_numeric_case_count = 0
    downgrade_compliant_count = 0
    money_field_count = 0
    promotion_count = 0
    unsupported_numeric_count = 0
    rejected_without_fallback_count = 0

    for case in cases:
        model_port = None if live else RecordedModel(case.response, case.request)
        result = DecisionSynthesizer(model_port=model_port, max_retries=0).synthesize(case.request)
        response = DecisionMemoResponse.model_validate(result.response.model_dump(mode="json"))
        valid_schema_count += 1
        fallback_count += int(result.used_fallback)
        rejected_without_fallback_count += int(not result.validation.accepted and not result.used_fallback)
        refs = _citation_ids(response)
        citation_count += len(refs)
        valid_citation_count += sum(
            _citation_is_known(kind, value, case.request)
            for kind, value in refs
        )
        has_numeric_claims = bool(_NUMBER_PATTERN.search(_candidate_prose(case.response)))
        numeric_case_count += int(has_numeric_claims)
        numeric_is_supported = "unsupported_numeric_claim" not in result.validation.reason_codes
        accurate_numeric_case_count += int(has_numeric_claims and numeric_is_supported)
        unsupported_numeric_count += int(
            "unsupported_numeric_claim" in result.validation.reason_codes
        )
        is_downgrade_compliant = (
            _DECISION_RANK[response.proposed_decision] <= _DECISION_RANK[case.request.baseline_decision]
            and response.confidence <= case.request.max_confidence
        )
        downgrade_compliant_count += int(is_downgrade_compliant)
        promotion_count += int(not is_downgrade_compliant)
        money_field_count += len(_find_money_fields(response.model_dump(mode="json")))
        case_passed = result.used_fallback == case.expected_fallback and (
            result.validation.accepted or result.used_fallback
        )
        case_results.append(
            {
                "name": case.name,
                "passed": case_passed,
                "used_fallback": result.used_fallback,
                "expected_fallback": case.expected_fallback,
                "reason_codes": result.validation.reason_codes,
            }
        )

    total_cases = len(cases)
    valid_schema_rate = _ratio(valid_schema_count, total_cases)
    citation_precision = _ratio(valid_citation_count, citation_count, empty_value=1.0)
    numeric_claim_accuracy = _ratio(
        accurate_numeric_case_count,
        numeric_case_count,
        empty_value=1.0,
    )
    downgrade_only_compliance = _ratio(downgrade_compliant_count, total_cases)
    fallback_rate = _ratio(fallback_count, total_cases)
    passed = (
        valid_schema_rate == 1.0
        and citation_precision >= 0.95
        and money_field_count == 0
        and promotion_count == 0
        and rejected_without_fallback_count == 0
        and all(case["passed"] for case in case_results)
    )
    return SynthesizerEvaluation(
        passed=passed,
        total_cases=total_cases,
        valid_schema_rate=valid_schema_rate,
        citation_precision=citation_precision,
        numeric_claim_accuracy=numeric_claim_accuracy,
        downgrade_only_compliance=downgrade_only_compliance,
        fallback_rate=fallback_rate,
        money_field_count=money_field_count,
        promotion_count=promotion_count,
        unsupported_numeric_claims_rejected=unsupported_numeric_count,
        fallback_memo_count=fallback_count,
        rejected_without_fallback_count=rejected_without_fallback_count,
        cases=tuple(case_results),
    )


def _ratio(numerator: int, denominator: int, *, empty_value: float = 0.0) -> float:
    return numerator / denominator if denominator else empty_value


def _citation_ids(response: DecisionMemoResponse) -> list[tuple[str, str | int]]:
    citations: list[tuple[str, str | int]] = []
    for item in [*response.key_reasons, *response.key_risks, *response.what_would_change]:
        citations.extend(("metric", value) for value in item.metric_ids)
        citations.extend(("chunk", value) for value in item.chunk_ids)
        citations.extend(("warning", value) for value in item.warning_codes)
    return citations


def _citation_is_known(kind: str, value: str | int, request: DecisionSynthesisRequest) -> bool:
    if kind == "metric":
        return value in request.known_metric_ids
    if kind == "chunk":
        return value in request.known_chunk_ids
    return value in request.known_warning_codes


def _candidate_prose(response: dict[str, Any]) -> str:
    return "\n".join(
        [str(response.get("thesis", ""))]
        + [
            item.get("claim", "")
            for group_name in ("key_reasons", "key_risks", "what_would_change")
            for item in response.get(group_name, [])
        ]
    )


def _find_money_fields(value: Any) -> list[str]:
    if isinstance(value, dict):
        found = [key for key in value if key in _MONEY_FIELDS]
        return found + [
            field
            for nested in value.values()
            for field in _find_money_fields(nested)
        ]
    if isinstance(value, list):
        return [field for nested in value for field in _find_money_fields(nested)]
    return []
