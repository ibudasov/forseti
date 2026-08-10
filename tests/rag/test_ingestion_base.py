"""Tests for ingestion base utilities."""
from __future__ import annotations

from app.rag.ingestion.base import compute_source_hash


def test_compute_source_hash_is_deterministic():
    h1 = compute_source_hash("https://example.com/doc", 0, "Some text.")
    h2 = compute_source_hash("https://example.com/doc", 0, "Some text.")
    assert h1 == h2


def test_compute_source_hash_differs_by_chunk_index():
    h1 = compute_source_hash("https://example.com/doc", 0, "Same text.")
    h2 = compute_source_hash("https://example.com/doc", 1, "Same text.")
    assert h1 != h2


def test_compute_source_hash_differs_by_text():
    h1 = compute_source_hash("https://example.com/doc", 0, "Text A")
    h2 = compute_source_hash("https://example.com/doc", 0, "Text B")
    assert h1 != h2


def test_compute_source_hash_is_hex_string():
    h = compute_source_hash("url", 0, "text")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
