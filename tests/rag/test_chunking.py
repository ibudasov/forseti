"""Tests for text chunking logic."""
from __future__ import annotations

from app.rag.chunking import chunk_text


def test_single_sentence_produces_one_chunk():
    text = "This is a short sentence."
    chunks = chunk_text(text, chunk_size=800, overlap=100)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].chunk_index == 0


def test_long_text_splits_into_multiple_chunks():
    # Create text with many sentences exceeding chunk_size (~800 tokens ≈ 3200 chars)
    sentence = "This is a test sentence that contains some meaningful content. "
    long_text = sentence * 80  # ~80 * 15 tokens = 1200 tokens
    chunks = chunk_text(long_text, chunk_size=800, overlap=100)
    assert len(chunks) > 1


def test_chunk_indices_are_sequential():
    sentence = "Each sentence here ends with a period. "
    text = sentence * 60
    chunks = chunk_text(text, chunk_size=400, overlap=50)
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i


def test_overlap_is_respected():
    # Build text where we can verify tail sentences appear in next chunk
    sentences = [f"Sentence number {i} ends here." for i in range(50)]
    text = " ".join(sentences)
    chunks = chunk_text(text, chunk_size=200, overlap=50)
    if len(chunks) > 1:
        # Last words of chunk[0] should appear in chunk[1] (overlap)
        last_chunk0_words = chunks[0].text.split()[-5:]
        first_chunk1_words = chunks[1].text.split()[:20]
        overlap_found = any(w in first_chunk1_words for w in last_chunk0_words)
        assert overlap_found, "Expected overlap between consecutive chunks"


def test_empty_text_returns_no_chunks():
    chunks = chunk_text("", chunk_size=800, overlap=100)
    assert chunks == []


def test_whitespace_only_text_returns_no_chunks():
    chunks = chunk_text("   \n\t  ", chunk_size=800, overlap=100)
    assert chunks == []
