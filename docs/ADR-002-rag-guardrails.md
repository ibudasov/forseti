# ADR-002: RAG Guardrails – Synthesis Never Influences Trade Math

## Status
Accepted

## Context
The RAG layer generates evidence-backed explanations for trading recommendations. A critical risk is that the LLM (Gemini) or synthesis logic could generate explanations that influence or contradict deterministic trade parameters (entry range, stop-loss, take-profit, position size).

Example bad scenario:
- Rules engine calculates: entry 100-105, stop-loss 95, take-profit 110
- RAG synthesis says: "Enter at 108, stop at 92"
- User confused, potential loss

## Decision
**Hard rule: RAG synthesis output never contains numeric trade parameters. Enforced at synthesis time via guardrails.**

## Rationale

### Why this is non-negotiable
1. **Risk isolation**: Rules engine is deterministic and tested; LLM output is probabilistic
2. **Accountability**: Clear source of truth for trade parameters
3. **Auditability**: Evidence supports decision but doesn't change it
4. **User experience**: Avoids confusion between recommendation and explanation
5. **Regulatory**: Clear separation of analysis (evidence) and decision (math)

### How guardrails work
1. **Pre-synthesis check**: Validate LLM prompt template has no numeric fields
2. **Post-synthesis validation**: Reject output containing forbidden keywords + numbers
3. **Schema enforcement**: Output schema (EvidenceSynthesis) has no numeric trade fields
4. **Test coverage**: Dedicated test proves rule cannot be violated

### Forbidden terms
Evidence cannot mention (with numbers):
- "entry" or "enter" (suggests entry price)
- "stop loss" or "stop"
- "take profit" or "target"
- "position size" or "position"
- "risk/reward" (with numbers)
- Prices in dollars, EUR, or $

### Allowed evidence
Evidence can discuss:
- Market conditions ("strong revenue growth")
- Business drivers ("expansion into new markets")
- Risks ("competitive pressure", "regulatory uncertainty")
- Catalysts ("earnings report", "product launch")
- Red flags ("executive departure", "lawsuit")
- Market alignment ("strong Q1 results support the setup")

## Implementation

### Synthesis Module
```python
def _check_guardrails(synthesis: EvidenceSynthesis) -> None:
    """Validate no numeric trade parameters in synthesis."""
    forbidden_keywords = [
        "entry", "stop loss", "take profit", 
        "position size", "risk/reward", "$", "eur"
    ]
    
    all_text = " ".join([
        # ... concatenate all evidence fields
    ]).lower()
    
    for keyword in forbidden_keywords:
        if keyword in all_text and any(c.isdigit() for c in ...):
            raise GroundingError("Guardrail violation: ...")
```

### API Response Structure
```json
{
  "ticker": "AAPL",
  "evidence": {
    "bullish_drivers": [
      {
        "text": "Strong revenue growth",
        "chunk_id": 123,
        "source_url": "https://...",
        "published_at": "2024-01-15"
      }
    ],
    "bearish_risks": [...],
    "catalysts": [...],
    "news_alignment": "supporting",
    "red_flags": [...],
    "confidence_adjustment": null,
    "status": "complete"
  }
}
```

Note: `confidence_adjustment` only allows ±0.1 (subtle adjustments), never touch entry/SL/TP/size.

## Testing Strategy

### Test 1: Output Schema Validation
```python
def test_synthesis_no_numeric_trade_fields():
    # EvidenceSynthesis dataclass has no fields for prices, sizes, etc.
    assert "entry" not in EvidenceSynthesis.__fields__
    assert "stop_loss" not in EvidenceSynthesis.__fields__
```

### Test 2: Guardrails Rejection
```python
def test_guardrails_reject_entry_price():
    results = [...]
    synthesis = synthesize_evidence(results)
    assert synthesis.bullish_drivers[0].text != "enter at $150"
```

### Test 3: Integration with API
```python
def test_api_evidence_no_money_math():
    response = GET /ticker/AAPL/evidence
    assert "entry" not in response.evidence.bullish_drivers[0].text
```

### Test 4: LLM Output Validation (future)
```python
def test_gemini_synthesis_passes_guardrails():
    # Mock Gemini response
    gemini_output = call_gemini(...)
    synthesis = EvidenceSynthesis.from_gemini(gemini_output)
    _check_guardrails(synthesis)  # Must not raise
```

## Consequences

### Positive
- ✓ Rules engine remains single source of truth for money math
- ✓ Clear accountability for trade decisions
- ✓ Reduces risk of LLM hallucinations influencing positions
- ✓ Simpler output schema, easier to maintain
- ✓ Clear user expectations

### Negative
- ✗ LLM cannot dynamically adjust trade parameters based on evidence
- ✗ Output less flexible (pre-defined categories only)
- ✗ Synthesis constrained to explanatory role only

### Rationale for accepting negatives
Trade parameter calculation must be deterministic. If evidence should influence risk math, that logic belongs in the rules engine, not the RAG layer. This keeps the system auditable and traceable.

## Alternatives Considered

### A. Allow confidence adjustments
- Considered: Evidence could suggest confidence boost (±10%)
- Accepted: Minimal adjustment only (±10%), not entry/SL/TP/size
- Implementation: confidence_adjustment field with explicit bounds

### B. Separate evidence-driven engine
- Rejected: Over-engineered; violates separation of concerns
- RAG is explanatory; rules engine is decisional

### C. Human-in-the-loop for evidence-driven changes
- Rejected: Out of scope for V1; can revisit in V3
- Would require review workflows

## Related Decisions
- **ADR-001**: pgvector-first storage
- **Phase 4 Implementation**: Synthesis module with guardrails

## Enforcement
- Guardrails checked in `app/services/synthesis._check_guardrails()`
- Called before every API response
- Test in `tests/test_rag.py::test_synthesis_guardrails_violation`

## Review Date
Review after first 10 Gemini synthesis calls (Week 7-8 when LLM integration added)
