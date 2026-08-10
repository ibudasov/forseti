# ADR 002 — RAG never touches risk math

**Status:** Accepted  
**Date:** 2026-08-10

## Context

Forseti's deterministic rules engine calculates entry range, stop-loss, take-profit, position size, and risk/reward ratio from price and fundamental data. Introducing an LLM-backed RAG layer creates the risk that generated text could inadvertently influence these numbers.

## Decision

The RAG synthesis layer **must never calculate, modify, or suggest** trade parameters (entry range, stop-loss, take-profit, position size, risk/reward ratio). This is a non-negotiable product rule.

Enforcement is multi-layered:

1. **Prompt guardrail** — the system prompt explicitly forbids outputting trade parameters.
2. **Schema guardrail** — `SynthesisOutput` uses `model_config = ConfigDict(extra="forbid")`. Any attempt to add a forbidden field (e.g., `stop_loss`) raises a Pydantic `ValidationError` immediately.
3. **Constant** — `_FORBIDDEN_FIELDS` in `synthesis.py` enumerates the disallowed field names and is tested directly.
4. **`evidence` block in `AnalyzeResponse`** — the evidence block is a separate field; it cannot override `stop_loss`, `entry_range`, `take_profit`, or `position_size` because those fields are set exclusively by the deterministic engine.

## Consequences

- Evidence explanations are purely narrative (claims with source citations).
- The deterministic rules engine remains unchanged and isolated.
- A confidence-downgrade *flag* (`status: "insufficient_data"`) is allowed when evidence is thin — but it is advisory only; the engine still computes risk levels independently.
