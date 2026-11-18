"""Idempotent raw-document archiving before RAG chunking."""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from app.rag.ingestion.base import RawDocument

logger = logging.getLogger(__name__)


def _payload_bytes(doc: RawDocument) -> bytes:
    payload = doc.raw_payload if doc.raw_payload is not None else doc.text
    if isinstance(payload, bytes):
        return payload
    return payload.encode("utf-8")


def _document_hash(doc: RawDocument, payload: bytes) -> str:
    hasher = hashlib.sha256()
    hasher.update(f"{doc.source_url}::".encode("utf-8"))
    hasher.update(payload)
    return hasher.hexdigest()


def _document_date(doc: RawDocument, run_date: Optional[date]) -> str:
    if doc.published_at is not None:
        return doc.published_at.date().isoformat()
    resolved_date = run_date or datetime.now(timezone.utc).date()
    return resolved_date.isoformat()


def archive_raw_document(
    doc: RawDocument,
    raw_dir: Optional[str],
    *,
    run_date: Optional[date] = None,
) -> Optional[Path]:
    """Persist the raw document payload under ``RAW_DOCUMENT_DIR``; warn and continue on I/O errors."""
    if not raw_dir or not raw_dir.strip():
        return None

    payload = _payload_bytes(doc)
    source_hash = _document_hash(doc, payload)
    extension = (doc.raw_extension or "txt").lstrip(".") or "txt"
    target_path = (
        Path(raw_dir)
        / doc.source_type.value
        / doc.ticker.upper()
        / _document_date(doc, run_date)
        / f"{source_hash}.{extension}"
    )

    try:
        if target_path.exists():
            return target_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(payload)
        return target_path
    except OSError as exc:
        logger.warning(
            "Raw document archive failed for %s (%s): %s",
            doc.ticker,
            doc.source_type.value,
            exc,
        )
        return None
