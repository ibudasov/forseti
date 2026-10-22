"""Technical Analyst: interprets TA-2 facts only, cites them by metric_id.

Mirrors ``app.services.fundamental_analyst``. The analyst never computes new
indicator values; it reads the immutable ``TechnicalAnalysisRequest`` facts
and deterministic metrics, and proposes a downgrade-only score adjustment.
Any malformed, uncited, or out-of-policy output degrades to a neutral
fallback so a bad model response can never silently change the deterministic
baseline.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from pydantic import ValidationError

from app.schemas.technicals import (
    TechnicalAnalysisRequest,
    TechnicalAssessmentResponse,
    TechnicalAssessmentResult,
    TechnicalAssessmentValidation,
    TechnicalFinding,
)
from app.services.llm_schema import gemini_token_usage, inline_response_schema
from app.settings import get_settings
from app.trust.memo_checks import _parse_number

logger = logging.getLogger(__name__)

PROMPT_VERSION = "technical-analyst.v1"
MAX_RAW_OUTPUT_CHARS = 4_000
FORBIDDEN_RESPONSE_FIELDS = frozenset(
    {
        "decision",
        "entry_range",
        "stop_loss",
        "take_profit",
        "risk_reward",
        "position_size_eur",
        "time_stop_at",
        "confidence",
        "technical_signal",
        "proposed_decision",
        "entry",
        "target",
        "support_level",
        "resistance_level",
    }
)

_PCT_UNIT = "pct"
_RAW_VALUE_TOLERANCE = 0.0001
_PERCENT_TOLERANCE = 0.01
_DECIMAL_OR_PERCENT_CLAIM = re.compile(r"[-+]?\d+(?:\.\d+)?%|[-+]?\d+\.\d+")


@dataclass(frozen=True)
class ModelOutput:
    text: str
    token_usage: dict[str, int]


class RetryableModelError(RuntimeError):
    """Raised when a model call can be retried safely."""


class TechnicalModelPort(Protocol):
    model_name: str

    def generate(self, prompt: str) -> ModelOutput:
        """Return one raw JSON response for the supplied prompt."""


class GeminiTechnicalModel:
    def __init__(
        self,
        *,
        model_name: str,
        vertex_project: str | None,
        vertex_location: str,
        temperature: float,
    ) -> None:
        self.model_name = model_name
        self._vertex_project = vertex_project
        self._vertex_location = vertex_location
        self._temperature = temperature

    def generate(self, prompt: str) -> ModelOutput:
        if not self._vertex_project:
            raise RetryableModelError("vertex_project_not_configured")
        try:
            import vertexai
            from vertexai.generative_models import GenerationConfig, GenerativeModel

            vertexai.init(project=self._vertex_project, location=self._vertex_location)
            response = GenerativeModel(self.model_name).generate_content(
                prompt,
                generation_config=GenerationConfig.from_dict(
                    {
                        "temperature": self._temperature,
                        "response_mime_type": "application/json",
                        "response_schema": _technical_response_schema(),
                    }
                ),
            )
        except Exception as exc:
            raise RetryableModelError(str(exc)) from exc
        return ModelOutput(text=response.text, token_usage=gemini_token_usage(response))


class TechnicalAnalyst:
    def __init__(
        self,
        model_port: TechnicalModelPort | None = None,
        *,
        max_retries: int | None = None,
    ) -> None:
        settings = get_settings()
        self.model_port = model_port or GeminiTechnicalModel(
            model_name=settings.GEMINI_MODEL,
            vertex_project=settings.VERTEX_AI_PROJECT,
            vertex_location=settings.VERTEX_AI_LOCATION,
            temperature=settings.AGENT_MODEL_TEMPERATURE,
        )
        self.max_retries = settings.AGENT_MAX_RETRIES if max_retries is None else max_retries

    def assess(self, request: TechnicalAnalysisRequest) -> TechnicalAssessmentResult:
        if _coverage_insufficient(request):
            return _fallback_result(
                request,
                reason_codes=["insufficient_coverage"],
                raw_output="",
                latency_ms=0.0,
                token_usage={},
                model_name=self.model_port.model_name,
            )

        prompt = build_technical_analyst_prompt(request)
        started_at = time.monotonic()
        token_usage: dict[str, int] = {}
        last_error = "model_failure"
        raw_output = ""
        for attempt in range(self.max_retries + 1):
            try:
                model_output = self.model_port.generate(prompt)
                raw_output = model_output.text
                token_usage = model_output.token_usage
                response = _parse_response(raw_output)
            except RetryableModelError as exc:
                last_error = f"provider_error:{exc}"
                if attempt < self.max_retries:
                    continue
                break
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = _format_parse_error(exc)
                if attempt < self.max_retries:
                    continue
                break

            validation = validate_technical_assessment(response, request)
            latency_ms = (time.monotonic() - started_at) * 1000
            if not validation.accepted:
                logger.info(
                    "technical_assessment_rejected run_id=%s reasons=%s",
                    request.run_id,
                    validation.reason_codes,
                )
                return _fallback_result(
                    request,
                    reason_codes=validation.reason_codes,
                    raw_output=raw_output,
                    latency_ms=latency_ms,
                    token_usage=token_usage,
                    model_name=self.model_port.model_name,
                )
            return TechnicalAssessmentResult(
                response=response,
                validation=validation,
                raw_output=_sanitize_raw_output(raw_output),
                latency_ms=latency_ms,
                token_usage=token_usage,
                model_name=self.model_port.model_name,
                prompt_version=PROMPT_VERSION,
            )

        latency_ms = (time.monotonic() - started_at) * 1000
        return _fallback_result(
            request,
            reason_codes=[last_error],
            raw_output=raw_output,
            latency_ms=latency_ms,
            token_usage=token_usage,
            model_name=self.model_port.model_name,
        )


def _coverage_insufficient(request: TechnicalAnalysisRequest) -> bool:
    return bool(request.coverage.required_facts_missing) or request.coverage.bars_stale


def build_technical_analyst_prompt(request: TechnicalAnalysisRequest) -> str:
    request_json = json.dumps(request.model_dump(mode="json"), sort_keys=True, indent=2)
    return (
        "You are the Technical Analyst for Forseti.\n"
        "Read the provided TechnicalAnalysisRequest and return JSON only.\n"
        "Never output markdown, prose outside JSON, tool calls, or extra keys.\n"
        "Never compute new indicator values; only reference facts by metric_id from the request.\n"
        "Every number you write in a claim must equal the value of one of that finding's cited facts.\n"
        f"Keep proposed_score_adjustment within [{request.allowed_adjustment_min}, {request.allowed_adjustment_max}]; "
        "it is downgrade-only.\n"
        "Use -1 for one well-supported technical risk the deterministic rules miss, and -2 for a severe one "
        "(for example a breakdown below swing_low_20 combined with a weakening trend).\n"
        "Passing deterministic rules is not a reason for anything; use 0 when the evidence is confirmatory or mixed.\n"
        "Never include decision labels, entry or exit levels, stop-loss, take-profit, position size, "
        "risk/reward, time stops, or confidence.\n"
        "Status rules: insufficient_data and failed must always use proposed_score_adjustment 0.\n"
        "A negative adjustment requires at least one risk_flag or a medium/high-materiality negative finding.\n"
        "Finding IDs must be unique across findings, contradictions, and risk_flags.\n\n"
        "Valid examples:\n"
        "- confirmatory trend and momentum -> completed, neutral, adjustment 0 with supporting findings.\n"
        "- mixed signals -> completed, neutral, adjustment 0 with explicit contradictions.\n"
        "- one well-supported risk the deterministic rules miss -> completed, negative, adjustment -1 with a risk_flag.\n"
        "- severe breakdown below swing_low_20 with weakening momentum -> completed, strong_negative, "
        "adjustment -2 with a risk_flag.\n"
        "- insufficient facts -> insufficient_data, neutral, adjustment 0.\n"
        "- provider/runtime issue -> failed, neutral, adjustment 0.\n\n"
        "Return this schema exactly:\n"
        "{\n"
        '  "schema_version": "1.0",\n'
        '  "run_id": "...",\n'
        '  "context_hash": "...",\n'
        '  "agent_name": "technical_analyst",\n'
        '  "status": "completed|insufficient_data|failed",\n'
        '  "overall_signal": "strong_negative|negative|neutral|positive|strong_positive",\n'
        '  "trend_state": "uptrend|downtrend|range|transition|unknown",\n'
        '  "momentum_state": "strengthening|stable|weakening|overextended|unknown",\n'
        '  "proposed_score_adjustment": 0,\n'
        '  "findings": [],\n'
        '  "contradictions": [],\n'
        '  "risk_flags": [],\n'
        '  "missing_information": [],\n'
        '  "summary": ""\n'
        "}\n\n"
        f"TechnicalAnalysisRequest:\n{request_json}\n"
    )


def _technical_response_schema() -> dict[str, Any]:
    return inline_response_schema(TechnicalAssessmentResponse)


def validate_technical_assessment(
    response: TechnicalAssessmentResponse,
    request: TechnicalAnalysisRequest,
) -> TechnicalAssessmentValidation:
    known_metrics = _known_metrics(request)
    reason_codes = [
        *_validate_identity(response, request),
        *_validate_bounds(response, request),
        *_validate_citations(response, known_metrics),
        *_validate_numeric_claims(response, known_metrics),
        *_validate_negative_adjustment_support(response),
    ]
    return TechnicalAssessmentValidation(accepted=not reason_codes, reason_codes=_deduplicated(reason_codes))


def _validate_identity(
    response: TechnicalAssessmentResponse,
    request: TechnicalAnalysisRequest,
) -> list[str]:
    reason_codes: list[str] = []
    if response.run_id != request.run_id:
        reason_codes.append("run_id_mismatch")
    if response.context_hash != request.context_hash:
        reason_codes.append("context_hash_mismatch")
    return reason_codes


def _validate_bounds(
    response: TechnicalAssessmentResponse,
    request: TechnicalAnalysisRequest,
) -> list[str]:
    in_bounds = request.allowed_adjustment_min <= response.proposed_score_adjustment <= request.allowed_adjustment_max
    return [] if in_bounds else ["adjustment_out_of_bounds"]


def _validate_citations(
    response: TechnicalAssessmentResponse,
    known_metrics: dict[str, tuple[Decimal | None, str]],
) -> list[str]:
    for finding in _all_findings(response):
        if set(finding.metric_ids) - set(known_metrics):
            return ["unknown_metric_citation"]
    return []


def _validate_numeric_claims(
    response: TechnicalAssessmentResponse,
    known_metrics: dict[str, tuple[Decimal | None, str]],
) -> list[str]:
    for finding in _all_findings(response):
        if not _claim_numbers_are_cited(finding, known_metrics):
            return ["uncited_numeric_claim"]
    return []


def _validate_negative_adjustment_support(response: TechnicalAssessmentResponse) -> list[str]:
    if response.proposed_score_adjustment >= 0:
        return []
    has_support = bool(response.risk_flags) or any(
        finding.direction == "negative" and finding.materiality in {"medium", "high"}
        for finding in response.findings
    )
    return [] if has_support else ["negative_adjustment_without_support"]


def _claim_numbers_are_cited(
    finding: TechnicalFinding,
    known_metrics: dict[str, tuple[Decimal | None, str]],
) -> bool:
    claim_numbers = _extract_claim_numbers(finding.claim)
    if not claim_numbers:
        return True
    cited_facts = [known_metrics[metric_id] for metric_id in finding.metric_ids if metric_id in known_metrics]
    return all(
        any(_number_matches_fact(number, fact_value, unit) for fact_value, unit in cited_facts)
        for number in claim_numbers
    )


def _extract_claim_numbers(claim: str) -> list[float]:
    """Extract every decimal or percent literal in a claim as a bare float.

    A trailing ``%`` is stripped before parsing; ``_number_matches_fact``
    tries both the fraction and the percent-scale interpretation for
    ``pct``-unit facts, so the percent sign itself does not need to be
    tracked here.
    """
    numbers = []
    for match in _DECIMAL_OR_PERCENT_CLAIM.finditer(claim):
        raw = match.group(0)
        parsed = _parse_number(raw[:-1] if raw.endswith("%") else raw)
        if parsed is not None:
            numbers.append(parsed)
    return numbers


def _number_matches_fact(number: float, fact_value: Decimal | None, unit: str) -> bool:
    if fact_value is None:
        return False
    numeric = float(fact_value)
    if unit == _PCT_UNIT:
        fraction_match = math.isclose(number, numeric, rel_tol=0.0, abs_tol=_RAW_VALUE_TOLERANCE)
        percent_match = math.isclose(number, numeric * 100, rel_tol=0.0, abs_tol=_PERCENT_TOLERANCE)
        return fraction_match or percent_match
    return math.isclose(number, numeric, rel_tol=0.0, abs_tol=_RAW_VALUE_TOLERANCE)


def _known_metrics(request: TechnicalAnalysisRequest) -> dict[str, tuple[Decimal | None, str]]:
    known_metrics: dict[str, tuple[Decimal | None, str]] = {
        fact.metric_id: (fact.value, fact.unit) for fact in request.facts
    }
    known_metrics.update(
        {metric.metric_id: (metric.value, metric.unit) for metric in request.deterministic_result.metrics}
    )
    return known_metrics


def _all_findings(response: TechnicalAssessmentResponse) -> list[TechnicalFinding]:
    return [*response.findings, *response.contradictions, *response.risk_flags]


def _parse_response(raw_output: str) -> TechnicalAssessmentResponse:
    payload = json.loads(raw_output)
    _ensure_no_forbidden_fields(payload)
    return TechnicalAssessmentResponse.model_validate(payload)


def _ensure_no_forbidden_fields(payload: Any) -> None:
    if isinstance(payload, dict):
        forbidden = FORBIDDEN_RESPONSE_FIELDS & set(payload)
        if forbidden:
            raise ValueError(f"forbidden_response_field:{sorted(forbidden)[0]}")
        for value in payload.values():
            _ensure_no_forbidden_fields(value)
        return
    if isinstance(payload, list):
        for value in payload:
            _ensure_no_forbidden_fields(value)


def _fallback_result(
    request: TechnicalAnalysisRequest,
    *,
    reason_codes: list[str],
    raw_output: str,
    latency_ms: float,
    token_usage: dict[str, int],
    model_name: str,
) -> TechnicalAssessmentResult:
    response = TechnicalAssessmentResponse(
        run_id=request.run_id,
        context_hash=request.context_hash,
        status="insufficient_data" if "insufficient_coverage" in reason_codes else "failed",
        overall_signal="neutral",
        trend_state="unknown",
        momentum_state="unknown",
        proposed_score_adjustment=0,
        findings=[],
        contradictions=[],
        risk_flags=[],
        missing_information=_missing_information(request),
        summary="Technical Analyst returned a neutral fallback.",
    )
    return TechnicalAssessmentResult(
        response=response,
        validation=TechnicalAssessmentValidation(accepted=False, reason_codes=_deduplicated(reason_codes)),
        raw_output=_sanitize_raw_output(raw_output),
        latency_ms=latency_ms,
        token_usage=token_usage,
        model_name=model_name,
        prompt_version=PROMPT_VERSION,
    )


def _missing_information(request: TechnicalAnalysisRequest) -> list[str]:
    missing = [f"fact:{fact_name}" for fact_name in request.coverage.required_facts_missing]
    if request.coverage.bars_stale:
        missing.append("bars_stale")
    return missing


def _deduplicated(reason_codes: list[str]) -> list[str]:
    return list(dict.fromkeys(reason_codes))


def _sanitize_raw_output(raw_output: str) -> str:
    if len(raw_output) <= MAX_RAW_OUTPUT_CHARS:
        return raw_output
    return raw_output[:MAX_RAW_OUTPUT_CHARS] + "…"


def _format_parse_error(exc: Exception) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return "malformed_json"
    if isinstance(exc, ValidationError):
        return "schema_validation_failed"
    return str(exc)
