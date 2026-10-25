# Decision Synthesizer

The Decision Synthesizer turns the deterministic recommendation and typed analyst
views into a structured, cited memo. Its contract and policy are implemented in
`app/schemas/synthesis.py` and `app/services/decision_synthesizer.py`. The agentic
workflow uses it when `SYNTHESIZER_CONTRACT=typed` (see [Workflow integration](#workflow-integration)).

## Authority and contract

`DecisionSynthesisRequest` carries the deterministic decision, confidence, risk
levels, diagnosis, warnings, analyst views, and the known citation identifiers.
Its context hash uses the shared canonical hashing helper. The request allows
only decisions at or below its baseline and fixes `max_confidence` to the
baseline confidence.

`DecisionMemoResponse` contains a proposed decision, bounded confidence, thesis,
and cited reason, risk, and change-condition items. Each item can cite metric,
evidence-chunk, and warning identifiers. Unknown fields are rejected, and money,
price, risk, and time-stop fields are forbidden. The risk block is rendered from
the deterministic request rather than supplied by the model.

The service validates run and context identity, downgrade-only authority,
citations, numeric claims, decision labels, and deterministic risk numbers. It
retries bounded provider/parse/validation failures, then returns a deterministic
baseline memo. The fallback never raises the baseline decision or confidence.

## Workflow integration

`SYNTHESIZER_CONTRACT=legacy|typed` selects the synthesizer (default `legacy`; the
Makefile test targets pin `legacy`). In `typed` mode the workflow:

- builds a `DecisionSynthesisRequest` after the analyst views are known and calls
  `DecisionSynthesizer.synthesize` outside ADK;
- puts a deterministic `TypedDecisionMemoAgent` in the `decision_synthesizer` ADK slot
  (no LLM call, no `calculate_risk` tool), so the step reports zero tokens;
- applies `proposed_decision` through the shared downgrade-only transition, so a
  promotion is ignored. Risk numbers always come from the rules and the memo risk
  block is rendered from them;
- never reaches `unparsable_synthesis`; a rejected memo becomes a deterministic
  fallback memo and the step is marked degraded with reason `synthesis_fallback`;
- reuses the service for the Critic revision loop with the Critic findings as extra
  input (`critic_findings`), instead of the legacy revision prompt;
- passes the rendered memo and its structured claims to the Critic, whose
  deterministic rules check the claims against risk numbers and decision labels;
- exposes `AnalyzeResponse.memo` (thesis, cited reasons/risks/change conditions,
  rendered risk block; `null` in legacy mode) and persists `synthesizer_effect`
  plus the memo hash on `agent_runs` (migration `0013_synthesizer_effect`).

Invariants `synthesizer_step_zero_tokens_in_typed_mode` and
`synthesizer_never_changes_risk_numbers` are evaluated with the other agentic
invariants. The synthesizer evaluation report adds scorecard lines for fallback
memos served and rejected memos without a fallback (must be 0).

The repository keeps no OpenAPI snapshot file; the `/openapi.json` schema for `memo`
is asserted in `tests/test_synthesizer_api.py`.

### Rollout

1. Run legacy and typed side by side on the golden fixtures (shadow comparison).
2. When the comparison is clean, flip the default to `typed`.
3. After one clean shadow cycle, delete the legacy path (`_GeminiDecisionRevisionModel`,
   `_build_revision_prompt`, the legacy LLM synthesizer and `unparsable_synthesis`).

## Evaluation

Run the deterministic recorded-response suite with:

```sh
make eval-synthesizer
```

It covers a clean trade, watchlist, hard blocker, conflicting analyst views,
hallucinated number, money-field leak, and promotion attempt. The pass criteria
are 100% schema validity after fallback, citation precision of at least 0.95,
zero money fields, and zero promotions.

Live Gemini evaluation requires explicit cost confirmation:

```sh
make eval-synthesizer-live CONFIRM_COST=1
```

To inspect a single ticker's request and result, run:

```sh
make synthesize-draft ticker=NVDA
```

The golden-cassette parse baseline measured for this work is 0 unparsable
synthesizer outputs out of 3 recorded decision-synthesizer outputs. This count
reflects the current recorded fixtures; it does not measure uncaptured live
plain-text generations.
