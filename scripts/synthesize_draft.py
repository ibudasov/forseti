#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone

from app.db.session import get_engine
from app.schemas.analyze import AnalyzeRequest
from app.services.analyzer import analyze
from app.services.decision_synthesizer import (
    DecisionSynthesizer,
    build_decision_synthesis_request,
)

_DECISION_RANK = {"no_trade": 0, "watchlist": 1, "trade": 2}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--as-of", dest="as_of")
    parser.add_argument("--run-id")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    analyze_request = AnalyzeRequest(
        ticker=args.ticker,
        as_of_date=date.fromisoformat(args.as_of) if args.as_of else None,
    )
    snapshot_at = datetime.now(timezone.utc)
    response = analyze(
        analyze_request.ticker,
        engine=get_engine(),
        today=analyze_request.as_of_date,
    )
    run_id = args.run_id or f"synthesize-draft:{analyze_request.ticker}:{snapshot_at.date().isoformat()}"
    allowed_decisions = [
        decision
        for decision, rank in _DECISION_RANK.items()
        if rank <= _DECISION_RANK[response.decision]
    ]
    request = build_decision_synthesis_request(
        run_id=run_id,
        ticker=response.ticker,
        as_of_date=analyze_request.as_of_date or snapshot_at.date(),
        snapshot_at=snapshot_at,
        baseline_decision=response.decision,
        baseline_confidence=response.confidence,
        entry_range=response.entry_range,
        stop_loss=response.stop_loss,
        take_profit=response.take_profit,
        risk_reward=response.risk_reward,
        position_size_eur=response.position_size_eur,
        time_stop_at=response.time_stop_at,
        reasons=response.reasons,
        warnings=response.warnings,
        diagnosis=response.diagnosis,
        analyst_views=[],
        known_metric_ids=[],
        known_chunk_ids=[],
        known_warning_codes=[warning.split(":", maxsplit=1)[0] for warning in response.warnings],
        allowed_decisions=allowed_decisions,
        max_confidence=response.confidence,
    )
    result = DecisionSynthesizer().synthesize(request)
    print(
        json.dumps(
            {
                "request": request.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    from app.runtime import run_job

    sys.exit(run_job(main))
