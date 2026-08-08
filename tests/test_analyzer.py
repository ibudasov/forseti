"""Tests for analyzer module."""
from __future__ import annotations

import pytest

from app.services.analyzer import validate_and_normalize_ticker


def test_validate_and_normalize_ticker_uppercases_and_trims():
    assert validate_and_normalize_ticker(" nvda ") == "NVDA"


def test_validate_and_normalize_ticker_rejects_url_like_input():
    with pytest.raises(ValueError):
        validate_and_normalize_ticker("https://broker.example/NVDA")


def test_validate_and_normalize_ticker_rejects_empty():
    with pytest.raises(ValueError):
        validate_and_normalize_ticker("   ")


def test_validate_and_normalize_ticker_rejects_too_long():
    with pytest.raises(ValueError):
        validate_and_normalize_ticker("TOOLONGTICKER")


def test_validate_and_normalize_ticker_rejects_invalid_chars():
    with pytest.raises(ValueError):
        validate_and_normalize_ticker("NV$DA")
