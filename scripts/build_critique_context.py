#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from app.db.session import get_engine
from app.schemas.analyze import AnalyzeRequest
from app.services.analyzer import analyze
from app.services.critique_context import (
    build_analyst_views,
    build_critique_request,
    build_draft_from_response,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--as-of", dest="as_of")
    parser.add_argument("--run-id")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    request = AnalyzeRequest(ticker=args.ticker, as_of_date=as_of)
    engine = get_engine()
    response = analyze(request.ticker, engine=engine, today=request.as_of_date)
    critique_request = build_critique_request(
        ticker=request.ticker,
        run_id=args.run_id or _default_run_id(request.ticker, request.as_of_date),
        draft=build_draft_from_response(response),
        analyst_views=build_analyst_views(),
        deterministic_warnings=response.warnings,
        deterministic_diagnosis=response.diagnosis,
        as_of=request.as_of_date,
        engine=engine,
    )
    print(json.dumps(critique_request.model_dump(mode="json"), indent=2))
    return 0


def _default_run_id(ticker: str, as_of: date | None) -> str:
    if as_of is None:
        return f"critique-context:{ticker}"
    return f"critique-context:{ticker}:{as_of.isoformat()}"


if __name__ == "__main__":
    sys.exit(main())
