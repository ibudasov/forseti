"""Rules Engine tool: evaluates hard vetoes and checklist scoring.

Wraps `app.services.vetoes.check_vetoes` and
`app.services.checklist.evaluate_checklist`. Deterministic, no LLM involved.
Gathers its own inputs from the repository so agents only need a ticker.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Callable, List, Optional

from pydantic import BaseModel, Field

from app.db.repository import (
    get_latest_bars,
    get_latest_fundamental,
    get_latest_macro_daily,
    get_latest_technical_feature,
    get_next_earnings_event,
)
from app.services.checklist import evaluate_checklist
from app.services.vetoes import check_vetoes

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


class RulesEngineOutput(BaseModel):
    """Result of evaluating vetoes and the checklist for a ticker."""

    veto_rule_id: Optional[str] = Field(default=None, description="Rule id of the first triggered veto, if any.")
    veto_detail: Optional[str] = Field(default=None, description="Human-readable veto detail, if any.")
    checklist_score: int = Field(description="Total checklist score.")
    checklist_reasons: List[str] = Field(default_factory=list, description="rule_id: detail for each scored rule.")


EvaluateRules = Callable[[str], RulesEngineOutput]


def build_rules_engine_tool(
    engine: Optional["Engine"] = None,
    today: Optional[date] = None,
) -> EvaluateRules:
    """Create an `evaluate_rules` tool bound to the given collaborators."""

    def evaluate_rules(ticker: str) -> RulesEngineOutput:
        """Evaluate the deterministic hard vetoes and checklist score for a ticker.

        Never computes trade numbers; only reports which rules fired and why.
        """
        bars = list(reversed(get_latest_bars(ticker, 250, engine=engine)))
        latest_bar = bars[-1] if bars else None
        technical_feature = get_latest_technical_feature(ticker, engine=engine)
        fundamental = get_latest_fundamental(ticker, engine=engine)
        vix_row = get_latest_macro_daily(engine=engine)
        next_earnings = get_next_earnings_event(
            ticker, on_or_after=today or date.min, engine=engine
        )
        vix_close = Decimal(str(vix_row.vix)) if vix_row and vix_row.vix is not None else None

        veto = check_vetoes(
            rsi=technical_feature.rsi_14 if technical_feature else None,
            latest_bar=latest_bar,
            vix_close=vix_close,
            next_earnings=next_earnings,
            sma_200=technical_feature.sma_200 if technical_feature else None,
            today=today,
        )
        if veto:
            return RulesEngineOutput(
                veto_rule_id=veto.rule_id,
                veto_detail=veto.detail,
                checklist_score=0,
                checklist_reasons=[],
            )

        score, results = evaluate_checklist(
            latest_bar=latest_bar,
            fundamental=fundamental,
            technical_feature=technical_feature,
            vix_close=vix_close,
        )
        return RulesEngineOutput(
            checklist_score=score,
            checklist_reasons=[f"{r.rule_id}: {r.detail}" for r in results],
        )

    return evaluate_rules
