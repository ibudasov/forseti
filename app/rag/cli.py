"""CLI entrypoint for scheduled RAG ingestion.

Usage:
    python -m app.rag.cli --ticker NVDA --sector ai
    python -m app.rag.cli --all-active
    python -m app.rag.cli --ticker NVDA --coverage --json [--live]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def _build_embedding_client():
    from app.rag.embedding import MockEmbeddingClient, VertexAIEmbeddingClient
    from app.settings import get_settings

    settings = get_settings()
    if settings.VERTEX_AI_PROJECT:
        return VertexAIEmbeddingClient(
            project=settings.VERTEX_AI_PROJECT,
            location=settings.VERTEX_AI_LOCATION,
            model=settings.EMBEDDING_MODEL,
            dimension=settings.EMBEDDING_DIM,
        )
    logger.info("VERTEX_AI_PROJECT not set — using MockEmbeddingClient (zero vectors)")
    return MockEmbeddingClient(dimension=settings.EMBEDDING_DIM)


def _run_ticker(ticker: str, sector: str | None, embedding_client) -> int:
    from app.rag.pipeline import ingest_ticker

    count = ingest_ticker(
        ticker=ticker,
        embedding_client=embedding_client,
        sector=sector,
    )
    logger.info("ticker=%s stored_chunks=%d", ticker, count)
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forseti RAG ingestion pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ticker", help="Single ticker to ingest")
    group.add_argument("--all-active", action="store_true", help="Ingest all active securities")
    parser.add_argument("--coverage", action="store_true", help="Show stored/live RAG coverage for one ticker")
    parser.add_argument("--live", action="store_true", help="Probe live source availability for coverage")
    parser.add_argument("--json", action="store_true", help="Render coverage as JSON")
    parser.add_argument("--sector", default=None, help="Sector tag for sector news ingestion")
    args = parser.parse_args(argv)

    if args.coverage:
        if not args.ticker:
            parser.error("--coverage requires --ticker")
        from app.rag.coverage import build_coverage_report

        report = build_coverage_report(args.ticker.upper(), live=args.live)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            for source in report["sources"]:
                logger.info(
                    "ticker=%s source_type=%s status=%s chunk_count=%s document_count=%s detail=%s",
                    report["ticker"],
                    source["source_type"],
                    source["status"],
                    source["chunk_count"],
                    source["document_count"],
                    source["detail"],
                )
        return 0

    embedding_client = _build_embedding_client()

    if args.ticker:
        _run_ticker(args.ticker.upper(), args.sector, embedding_client)
        return 0

    # --all-active
    from app.db.repository import list_active_securities

    securities = list_active_securities()
    total = 0
    for security in securities:
        total += _run_ticker(security.ticker, security.sector_tag, embedding_client)
    logger.info("Ingestion complete — total chunks stored: %d", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
