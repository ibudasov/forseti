#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from app.db.session import get_engine
from app.schemas.analyze import AnalyzeRequest
from app.services.analyzer import analyze
from app.services.critic import Critic, CriticModelPort, ModelOutput
from app.services.critique_context import (
    build_analyst_views,
    build_critique_request,
    build_draft_from_response,
)


class StubCriticModel:
    model_name = "stub-critic"

    def __init__(self, request) -> None:
        self._request = request

    def generate(self, prompt: str) -> ModelOutput:
        del prompt
        payload = {
            "schema_version": "1.0",
            "run_id": self._request.run_id,
            "context_hash": self._request.context_hash,
            "agent_name": "critic_guardrail",
            "status": "completed",
            "verdict": "accept",
            "objections": [],
            "proposed_decision": None,
            "proposed_confidence_penalty": 0.0,
            "revision_instructions": "",
            "summary": "Stub critic accepted the draft without objections.",
        }
        return ModelOutput(text=json.dumps(payload), token_usage={})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--as-of", dest="as_of")
    parser.add_argument("--run-id")
    parser.add_argument("--live", action="store_true", help="Use the live Gemini/Vertex critic model.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = args.ticker.strip().upper()
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    request = AnalyzeRequest(ticker=ticker, as_of_date=as_of)
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
    model_port: CriticModelPort | None = None
    if not args.live:
        model_port = StubCriticModel(critique_request)
    result = Critic(model_port=model_port).review(critique_request)
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return 0


def _default_run_id(ticker: str, as_of: date | None) -> str:
    if as_of is None:
        return f"review-draft:{ticker}"
    return f"review-draft:{ticker}:{as_of.isoformat()}"


if __name__ == "__main__":
    sys.exit(main())
