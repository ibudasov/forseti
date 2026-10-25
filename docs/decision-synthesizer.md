# Decision Synthesizer

The Decision Synthesizer turns the deterministic recommendation and typed analyst
views into a structured, cited memo. Its contract and policy are implemented in
`app/schemas/synthesis.py` and `app/services/decision_synthesizer.py`; this
service is not yet wired into the workflow.

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
