from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from agents.config import HARD_RULES_TEXT
from app.schemas.critique import (
    CritiqueRequest,
    CritiqueResponse,
    CritiqueResult,
    CritiqueValidation,
)
from app.settings import get_settings

logger = logging.getLogger(__name__)

PROMPT_VERSION = "critic-guardrail.v1"
MAX_RAW_OUTPUT_CHARS = 4_000
FORBIDDEN_RESPONSE_FIELDS = (
    "entry_range",
    "stop_loss",
    "take_profit",
    "risk_reward",
    "position_size_eur",
    "time_stop_at",
    "proposed_confidence",
)
OBJECTION_CATEGORIES = (
    "analyst_contradiction",
    "unsupported_claim",
    "violated_hard_rule",
    "stale_or_incomplete_data",
    "risk_number_mismatch",
    "internal_inconsistency",
)
_DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}


@dataclass(frozen=True)
class ModelOutput:
    text: str
    token_usage: dict[str, int]


class RetryableModelError(RuntimeError):
    """Raised when a model call can be retried safely."""


class CriticModelPort(Protocol):
    model_name: str

    def generate(self, prompt: str) -> ModelOutput:
        """Return one raw JSON response for the supplied prompt."""


class GeminiCriticModel:
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
            from vertexai.generative_models import GenerativeModel

            vertexai.init(project=self._vertex_project, location=self._vertex_location)
            response = GenerativeModel(self.model_name).generate_content(
                prompt,
                generation_config={
                    "temperature": self._temperature,
                    "response_mime_type": "application/json",
                },
            )
        except Exception as exc:
            raise RetryableModelError(str(exc)) from exc
        return ModelOutput(text=response.text, token_usage=_token_usage(response))


class Critic:
    def __init__(
        self,
        model_port: CriticModelPort | None = None,
        *,
        max_retries: int | None = None,
    ) -> None:
        settings = get_settings()
        self.model_port = model_port or GeminiCriticModel(
            model_name=settings.GEMINI_MODEL,
            vertex_project=settings.VERTEX_AI_PROJECT,
            vertex_location=settings.VERTEX_AI_LOCATION,
            temperature=settings.AGENT_MODEL_TEMPERATURE,
        )
        self.max_retries = settings.AGENT_MAX_RETRIES if max_retries is None else max_retries

    def review(self, request: CritiqueRequest) -> CritiqueResult:
        prompt = build_critic_prompt(request)
        started_at = time.monotonic()
        token_usage: dict[str, int] = {}
        raw_output = ""
        last_error = "provider_error"

        for attempt in range(self.max_retries + 1):
            try:
                model_output = self.model_port.generate(prompt)
                raw_output = model_output.text
                token_usage = model_output.token_usage
                response = _parse_response(raw_output)
            except RetryableModelError as exc:
                last_error = _provider_error_code(exc)
                if attempt < self.max_retries:
                    continue
                break
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = _format_parse_error(exc)
                break

            validation = validate_critique(response, request)
            latency_ms = (time.monotonic() - started_at) * 1000
            if not validation.accepted:
                logger.info(
                    "critique_rejected run_id=%s reasons=%s",
                    request.run_id,
                    validation.reason_codes,
                )
            return CritiqueResult(
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


def build_critic_prompt(request: CritiqueRequest) -> str:
    draft_json = json.dumps(request.draft.model_dump(mode="json"), sort_keys=True, indent=2)
    analyst_views_json = json.dumps(
        [view.model_dump(mode="json") for view in request.analyst_views],
        sort_keys=True,
        indent=2,
    )
    evidence_text = _render_evidence_chunks(request)
    metric_ids_json = json.dumps(_available_metric_ids(request), sort_keys=True, indent=2)
    warning_codes_json = json.dumps(list(request.deterministic_warnings), sort_keys=True, indent=2)
    allowed_actions_json = json.dumps(list(request.allowed_actions), sort_keys=True, indent=2)
    objection_categories_json = json.dumps(list(OBJECTION_CATEGORIES), sort_keys=True, indent=2)
    return (
        f"{HARD_RULES_TEXT}\n\n"
        "Role:\n"
        "- You are the Critic guardrail for Forseti.\n"
        "- Review the supplied draft recommendation and never produce a new draft.\n"
        "- Return JSON only.\n\n"
        f"Draft recommendation:\n{draft_json}\n\n"
        f"Analyst views:\n{analyst_views_json}\n\n"
        f"Evidence chunks:\n{evidence_text}\n\n"
        f"Available metric_ids:\n{metric_ids_json}\n\n"
        f"Deterministic warning codes:\n{warning_codes_json}\n\n"
        "Objection categories:\n"
        f"{objection_categories_json}\n"
        "Medium/high objections must cite at least one chunk_id, metric_id, or deterministic warning code.\n\n"
        "Authority:\n"
        f"- You may lower the decision label only.\n"
        f"- You may propose a confidence penalty between 0.0 and {request.max_confidence_penalty:.2f}.\n"
        "- You may request at most one revision.\n"
        "- You may never raise the decision label or confidence.\n"
        "- You may never emit price, risk, money, or time-stop fields.\n\n"
        f"Allowed actions:\n{allowed_actions_json}\n\n"
        "Required JSON output schema:\n"
        "{\n"
        '  "schema_version": "1.0",\n'
        '  "run_id": "...",\n'
        '  "context_hash": "...",\n'
        '  "agent_name": "critic_guardrail",\n'
        '  "status": "completed|insufficient_data|failed",\n'
        '  "verdict": "accept|revise|reject",\n'
        '  "objections": [\n'
        "    {\n"
        '      "objection_id": "...",\n'
        '      "category": "analyst_contradiction|unsupported_claim|violated_hard_rule|'
        'stale_or_incomplete_data|risk_number_mismatch|internal_inconsistency",\n'
        '      "severity": "low|medium|high",\n'
        '      "source": "deterministic|model",\n'
        '      "claim": "...",\n'
        '      "metric_ids": [],\n'
        '      "chunk_ids": [],\n'
        '      "warning_codes": []\n'
        "    }\n"
        "  ],\n"
        '  "proposed_decision": "trade|watchlist|no_trade|null",\n'
        '  "proposed_confidence_penalty": 0.0,\n'
        '  "revision_instructions": "",\n'
        '  "summary": ""\n'
        "}\n"
        "Emit JSON only. Do not emit markdown, prose outside JSON, or extra keys.\n\n"
        'If the draft is clean, return verdict "accept" with zero objections and proposed_confidence_penalty 0.0. '
        "Manufacturing objections is a failure.\n"
    )


def validate_critique(
    response: CritiqueResponse,
    request: CritiqueRequest,
) -> CritiqueValidation:
    reason_codes = _request_level_reason_codes(response, request)
    if _has_duplicate_objection_ids(response):
        reason_codes.append("duplicate_objection_id")

    known_chunk_ids = {chunk.chunk_id for chunk in request.evidence_chunks}
    known_metric_ids = set(_available_metric_ids(request))
    known_warning_codes = set(request.deterministic_warnings)

    for objection in response.objections:
        reason_codes.extend(
            _objection_reason_codes(
                objection=objection,
                known_chunk_ids=known_chunk_ids,
                known_metric_ids=known_metric_ids,
                known_warning_codes=known_warning_codes,
            )
        )

    return CritiqueValidation(
        accepted=not reason_codes,
        reason_codes=_deduplicated(reason_codes),
    )


def _request_level_reason_codes(
    response: CritiqueResponse,
    request: CritiqueRequest,
) -> list[str]:
    reason_codes: list[str] = []
    if response.run_id != request.run_id:
        reason_codes.append("run_id_mismatch")
    if response.context_hash != request.context_hash:
        reason_codes.append("context_hash_mismatch")
    if response.proposed_confidence_penalty > request.max_confidence_penalty:
        reason_codes.append("penalty_out_of_bounds")
    if _is_proposed_decision_upgrade(response, request):
        reason_codes.append("proposed_decision_upgrade")
    if _has_forbidden_response_fields(response):
        reason_codes.append("forbidden_field_present")
    return reason_codes


def _has_duplicate_objection_ids(response: CritiqueResponse) -> bool:
    objection_ids = [objection.objection_id for objection in response.objections]
    return len(set(objection_ids)) != len(objection_ids)


def _objection_reason_codes(
    *,
    objection,
    known_chunk_ids: set[int],
    known_metric_ids: set[str],
    known_warning_codes: set[str],
) -> list[str]:
    reason_codes: list[str] = []
    if objection.severity in {"medium", "high"} and not (
        objection.metric_ids or objection.chunk_ids or objection.warning_codes
    ):
        reason_codes.append("uncited_material_objection")
    if set(objection.chunk_ids) - known_chunk_ids:
        reason_codes.append("unknown_chunk_citation")
    if set(objection.metric_ids) - known_metric_ids:
        reason_codes.append("unknown_metric_citation")
    if set(objection.warning_codes) - known_warning_codes:
        reason_codes.append("unknown_warning_code")
    return reason_codes


def _render_evidence_chunks(request: CritiqueRequest) -> str:
    if not request.evidence_chunks:
        return "[]"
    entries = []
    for index, chunk in enumerate(request.evidence_chunks, start=1):
        chunk_json = json.dumps(chunk.model_dump(mode="json"), sort_keys=True, indent=2)
        entries.append(f"{index}. chunk_id={chunk.chunk_id}\n{chunk_json}")
    return "\n".join(entries)


def _available_metric_ids(request: CritiqueRequest) -> list[str]:
    metric_ids = {
        metric_id
        for view in request.analyst_views
        for finding in view.findings
        for metric_id in finding.metric_ids
    }
    return sorted(metric_ids)


def _is_proposed_decision_upgrade(response: CritiqueResponse, request: CritiqueRequest) -> bool:
    if response.proposed_decision is None:
        return False
    return _decision_rank(response.proposed_decision) > _decision_rank(request.draft.decision)


def _decision_rank(decision: str) -> int:
    return _DECISION_RANK[decision]


def _has_forbidden_response_fields(response: CritiqueResponse) -> bool:
    extras = getattr(response, "model_extra", None) or getattr(response, "__pydantic_extra__", None) or {}
    if extras and set(extras) & set(FORBIDDEN_RESPONSE_FIELDS):
        return True
    return False


def _parse_response(raw_output: str) -> CritiqueResponse:
    payload = json.loads(raw_output)
    _ensure_no_forbidden_fields(payload)
    return CritiqueResponse.model_validate(payload)


def _ensure_no_forbidden_fields(payload: Any) -> None:
    if isinstance(payload, dict):
        if set(payload) & set(FORBIDDEN_RESPONSE_FIELDS):
            raise ValueError("forbidden_field_present")
        for value in payload.values():
            _ensure_no_forbidden_fields(value)
        return
    if isinstance(payload, list):
        for value in payload:
            _ensure_no_forbidden_fields(value)


def _fallback_result(
    request: CritiqueRequest,
    *,
    reason_codes: list[str],
    raw_output: str,
    latency_ms: float,
    token_usage: dict[str, int],
    model_name: str,
) -> CritiqueResult:
    response = CritiqueResponse(
        run_id=request.run_id,
        context_hash=request.context_hash,
        status="failed",
        verdict="accept",
        objections=[],
        proposed_decision=None,
        proposed_confidence_penalty=0.0,
        revision_instructions="",
        summary="Critic returned a neutral fallback.",
    )
    return CritiqueResult(
        response=response,
        validation=CritiqueValidation(accepted=False, reason_codes=_deduplicated(reason_codes)),
        raw_output=_sanitize_raw_output(raw_output),
        latency_ms=latency_ms,
        token_usage=token_usage,
        model_name=model_name,
        prompt_version=PROMPT_VERSION,
    )


def _deduplicated(reason_codes: list[str]) -> list[str]:
    return list(dict.fromkeys(reason_codes))


def _sanitize_raw_output(raw_output: str) -> str:
    if len(raw_output) <= MAX_RAW_OUTPUT_CHARS:
        return raw_output
    return raw_output[:MAX_RAW_OUTPUT_CHARS] + "…"


def _provider_error_code(exc: RetryableModelError) -> str:
    message = str(exc).strip()
    if not message:
        return "provider_error"
    return f"provider_error:{message}"


def _format_parse_error(exc: Exception) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return "malformed_json"
    if isinstance(exc, ValidationError):
        return "schema_validation_failed"
    return str(exc)


def _token_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {}
    return {
        "prompt_token_count": int(getattr(usage, "prompt_token_count", 0) or 0),
        "candidates_token_count": int(getattr(usage, "candidates_token_count", 0) or 0),
        "total_token_count": int(getattr(usage, "total_token_count", 0) or 0),
    }
