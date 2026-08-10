"""Input Resolver tool: normalizes a raw ticker reference.

Wraps `app.services.analyzer.validate_and_normalize_ticker`. Deterministic,
no LLM involved.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.services.analyzer import validate_and_normalize_ticker


class ResolveTickerOutput(BaseModel):
    """Result of resolving a raw ticker reference."""

    ticker: str = Field(default="", description="Normalized, validated ticker symbol.")
    is_valid: bool = Field(description="Whether the raw input resolved to a valid ticker.")
    error: Optional[str] = Field(default=None, description="Validation error, if any.")


def resolve_ticker(raw_ticker: str) -> ResolveTickerOutput:
    """Normalize and validate a ticker symbol or platform reference.

    Delegates entirely to the deterministic ticker validation rules. Never
    guesses or infers a ticker from a company name, URL, or description.
    """
    try:
        normalized = validate_and_normalize_ticker(raw_ticker)
    except ValueError as exc:
        return ResolveTickerOutput(ticker="", is_valid=False, error=str(exc))
    return ResolveTickerOutput(ticker=normalized, is_valid=True, error=None)
