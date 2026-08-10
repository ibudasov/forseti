"""Text chunking: ~800 token chunks with 100 token overlap, sentence-boundary aware."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_APPROX_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class TextChunk:
    text: str
    chunk_index: int


def _tokens_approx(text: str) -> int:
    return max(1, len(text) // _APPROX_CHARS_PER_TOKEN)


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> List[TextChunk]:
    """Split *text* into overlapping chunks of approximately *chunk_size* tokens.

    Splits are made on sentence boundaries where possible.  Each returned
    :class:`TextChunk` carries its zero-based *chunk_index*.
    """
    sentences = _SENTENCE_BOUNDARY.split(text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks: List[TextChunk] = []
    current_tokens = 0
    current_sentences: List[str] = []

    for sentence in sentences:
        sentence_tokens = _tokens_approx(sentence)

        if current_tokens + sentence_tokens > chunk_size and current_sentences:
            chunks.append(TextChunk(text=" ".join(current_sentences), chunk_index=len(chunks)))
            # Overlap: keep tail sentences whose combined size ≤ overlap
            overlap_sentences: List[str] = []
            overlap_tokens = 0
            for s in reversed(current_sentences):
                s_tokens = _tokens_approx(s)
                if overlap_tokens + s_tokens > overlap:
                    break
                overlap_sentences.insert(0, s)
                overlap_tokens += s_tokens
            current_sentences = overlap_sentences
            current_tokens = overlap_tokens

        current_sentences.append(sentence)
        current_tokens += sentence_tokens

    if current_sentences:
        chunks.append(TextChunk(text=" ".join(current_sentences), chunk_index=len(chunks)))

    return chunks
