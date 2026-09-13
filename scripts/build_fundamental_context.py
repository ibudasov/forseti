#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from app.services.fundamental_context import build_fundamental_analysis_request
from app.db.session import get_engine


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--as-of", dest="as_of")
    parser.add_argument("--run-id")
    parser.add_argument("--json", action="store_true", help="Print the context as JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    ticker = args.ticker.strip().upper()
    run_id = args.run_id or _default_run_id(ticker, as_of)
    request = build_fundamental_analysis_request(
        ticker=ticker,
        run_id=run_id,
        as_of=as_of,
        engine=get_engine(),
    )
    if args.json:
        print(json.dumps(request.model_dump(mode="json"), indent=2))
        return 0
    print(request.context_hash)
    return 0


def _default_run_id(ticker: str, as_of: date | None) -> str:
    if as_of is None:
        return f"fundamental-context:{ticker}"
    return f"fundamental-context:{ticker}:{as_of.isoformat()}"


if __name__ == "__main__":
    sys.exit(main())
