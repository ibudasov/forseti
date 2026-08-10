"""Synthesis service for RAG - generates grounded evidence-backed explanations."""
from __future__ import annotations

from typing import List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from app.services.retrieval import RetrievalResult


@dataclass
class EvidenceItem:
    """Single piece of evidence with source citation."""
    text: str
    chunk_id: int
    source_url: str
    published_at: Optional[str] = None


@dataclass
class EvidenceSynthesis:
    """Structured synthesis output with citations."""
    bullish_drivers: List[EvidenceItem]
    bearish_risks: List[EvidenceItem]
    catalysts: List[EvidenceItem]
    news_alignment: str  # supporting / neutral / weakening
    red_flags: List[EvidenceItem]
    confidence_adjustment: Optional[float] = None  # -0.1 to +0.1
    status: str = "complete"  # complete, insufficient_data, error

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "bullish_drivers": [asdict(item) for item in self.bullish_drivers],
            "bearish_risks": [asdict(item) for item in self.bearish_risks],
            "catalysts": [asdict(item) for item in self.catalysts],
            "news_alignment": self.news_alignment,
            "red_flags": [asdict(item) for item in self.red_flags],
            "confidence_adjustment": self.confidence_adjustment,
            "status": self.status,
        }


class GroundingError(Exception):
    """Raised when synthesis fails guardrail checks."""
    pass


def _check_guardrails(synthesis: EvidenceSynthesis) -> None:
    """
    Verify that synthesis output contains no numeric trading parameters.
    
    This enforces the hard rule: RAG layer never calculates or modifies
    entry range, stop-loss, take-profit, or position size.
    
    Args:
        synthesis: The synthesis output to validate
        
    Raises:
        GroundingError: If guardrails are violated
    """
    forbidden_keywords = [
        "entry",
        "stop loss",
        "take profit",
        "position size",
        "risk/reward",
        "dollars",
        "eur",
        "$",
    ]
    
    all_text = " ".join([
        " ".join([item.text for item in synthesis.bullish_drivers]),
        " ".join([item.text for item in synthesis.bearish_risks]),
        " ".join([item.text for item in synthesis.catalysts]),
        synthesis.news_alignment,
        " ".join([item.text for item in synthesis.red_flags]),
    ]).lower()
    
    for keyword in forbidden_keywords:
        if keyword in all_text and any(c.isdigit() for c in all_text[all_text.find(keyword):all_text.find(keyword)+50]):
            raise GroundingError(
                f"Guardrail violation: synthesis mentions '{keyword}' with numeric values. "
                "RAG must never touch money math."
            )


def synthesize_evidence(
    retrieval_results: List[RetrievalResult],
    question_context: Optional[str] = None,
) -> EvidenceSynthesis:
    """
    Synthesize evidence from retrieved chunks into structured explanation.
    
    This is a basic implementation that extracts evidence from retrieval results.
    In production, this would call Gemini to perform grounded synthesis.
    
    Args:
        retrieval_results: List of retrieved chunks from retrieval service
        question_context: Optional context about what was being asked
        
    Returns:
        EvidenceSynthesis with bullish drivers, risks, catalysts, etc.
        
    Raises:
        GroundingError: If output violates guardrails
    """
    if not retrieval_results:
        return EvidenceSynthesis(
            bullish_drivers=[],
            bearish_risks=[],
            catalysts=[],
            news_alignment="neutral",
            red_flags=[],
            status="insufficient_data",
        )
    
    # Basic synthesis: categorize retrieved chunks into evidence categories
    # In production, this would be replaced with actual LLM call
    bullish_items = []
    bearish_items = []
    catalyst_items = []
    red_flag_items = []
    
    # Simple heuristic: look for sentiment keywords to categorize
    for result in retrieval_results:
        text_lower = result.text.lower()
        
        if any(word in text_lower for word in ["growth", "increase", "opportunity", "bullish", "strong", "positive"]):
            bullish_items.append(EvidenceItem(
                text=result.text[:200],  # Truncate for display
                chunk_id=result.chunk_id,
                source_url=result.source_url,
                published_at=result.published_at.isoformat() if result.published_at else None,
            ))
        
        if any(word in text_lower for word in ["risk", "decline", "weak", "bearish", "negative", "threat"]):
            bearish_items.append(EvidenceItem(
                text=result.text[:200],
                chunk_id=result.chunk_id,
                source_url=result.source_url,
                published_at=result.published_at.isoformat() if result.published_at else None,
            ))
        
        if any(word in text_lower for word in ["catalyst", "event", "milestone", "announcement", "earnings"]):
            catalyst_items.append(EvidenceItem(
                text=result.text[:200],
                chunk_id=result.chunk_id,
                source_url=result.source_url,
                published_at=result.published_at.isoformat() if result.published_at else None,
            ))
        
        if any(word in text_lower for word in ["red flag", "concern", "issue", "problem", "controversy", "lawsuit"]):
            red_flag_items.append(EvidenceItem(
                text=result.text[:200],
                chunk_id=result.chunk_id,
                source_url=result.source_url,
                published_at=result.published_at.isoformat() if result.published_at else None,
            ))
    
    # Determine news alignment based on ratio of bullish to bearish
    if bullish_items and not bearish_items:
        news_alignment = "supporting"
    elif bearish_items and not bullish_items:
        news_alignment = "weakening"
    else:
        news_alignment = "neutral"
    
    synthesis = EvidenceSynthesis(
        bullish_drivers=bullish_items[:3],
        bearish_risks=bearish_items[:3],
        catalysts=catalyst_items[:3],
        news_alignment=news_alignment,
        red_flags=red_flag_items[:2],
        status="complete",
    )
    
    # Run guardrails check
    _check_guardrails(synthesis)
    
    return synthesis


def synthesize_no_trade_evidence(
    retrieval_results: List[RetrievalResult],
) -> EvidenceSynthesis:
    """
    Generate evidence for a no-trade recommendation.
    
    Emphasizes risks, lack of catalysts, and red flags.
    
    Args:
        retrieval_results: List of retrieved chunks
        
    Returns:
        EvidenceSynthesis structured for justifying no-trade
    """
    synthesis = synthesize_evidence(retrieval_results, question_context="Why not trade this?")
    
    # Adjust synthesis to emphasize risks for no-trade case
    if not synthesis.bearish_risks and not synthesis.red_flags:
        synthesis.status = "insufficient_data"
    
    return synthesis
