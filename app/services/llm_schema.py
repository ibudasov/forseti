"""Shared Gemini ``response_schema`` inlining for analyst model ports.

Vertex AI's structured-output schema does not support ``$ref``/``$defs`` or
``additionalProperties``/``const`` the way Pydantic's ``model_json_schema``
emits them. ``inline_response_schema`` walks a Pydantic JSON schema and
produces a self-contained schema Vertex accepts, shared by every analyst so
the inlining rules never drift between them.
"""

from __future__ import annotations

import json
from typing import Any, Type, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


def inline_response_schema(model: Type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions = schema.get("$defs", {})
    return _inline(schema, definitions)


def gemini_token_usage(response: Any) -> dict[str, int]:
    """Extract prompt/candidate/total token counts from a Gemini response."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {}
    return {
        "prompt_token_count": int(getattr(usage, "prompt_token_count", 0) or 0),
        "candidates_token_count": int(getattr(usage, "candidates_token_count", 0) or 0),
        "total_token_count": int(getattr(usage, "total_token_count", 0) or 0),
    }


def strict_parse_json(
    raw_output: str,
    model: Type[ModelT],
    *,
    forbidden_fields: frozenset[str] = frozenset(),
    forbidden_field_error_code: str = "forbidden_response_field",
) -> ModelT:
    payload = json.loads(raw_output)
    ensure_no_forbidden_fields(payload, forbidden_fields, forbidden_field_error_code)
    return model.model_validate(payload)


def ensure_no_forbidden_fields(
    payload: Any,
    forbidden_fields: frozenset[str],
    error_code: str = "forbidden_response_field",
) -> None:
    if isinstance(payload, dict):
        forbidden = forbidden_fields & set(payload)
        if forbidden:
            if error_code == "forbidden_response_field":
                raise ValueError(f"{error_code}:{sorted(forbidden)[0]}")
            raise ValueError(error_code)
        for value in payload.values():
            ensure_no_forbidden_fields(value, forbidden_fields, error_code)
        return
    if isinstance(payload, list):
        for value in payload:
            ensure_no_forbidden_fields(value, forbidden_fields, error_code)


def _inline(value: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return _inline_object(value, definitions)
    if isinstance(value, list):
        return [_inline(item, definitions) for item in value]
    return value


def _inline_object(value: dict[str, Any], definitions: dict[str, Any]) -> Any:
    reference = value.get("$ref")
    if reference is not None:
        definition_name = reference.rsplit("/", maxsplit=1)[-1]
        return _inline(definitions[definition_name], definitions)
    normalized = {}
    for key, item in value.items():
        if key in {"$defs", "additionalProperties"}:
            continue
        if key == "const":
            normalized["enum"] = [_inline(item, definitions)]
            continue
        normalized[key] = _inline(item, definitions)
    return normalized
