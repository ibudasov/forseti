# Fundamental agent evaluation

The Fundamental Analyst stays disabled in `enforced` mode until a frozen
evaluation suite, shadow reporting, and explicit rollout gates show that it
improves decisions without breaking deterministic safety rules.

## Evaluation artifacts

- `tests/fixtures/fundamental_agent_eval/suite.json` stores the reviewed frozen
  cases.
- `app/schemas/fundamental_evaluation.py` defines the fixture, label, report,
  and shadow-report contracts.
- `app/services/fundamental_evaluation.py` validates fixtures, evaluates either
  recorded or live runs, applies the bounded policy in `shadow`, and emits
  machine-readable metrics plus review samples.
- `scripts/eval_fundamental_agent.py` is the CLI entrypoint used by the Make
  targets.

Each frozen case contains:

- an immutable `FundamentalAnalysisRequest`;
- the deterministic baseline `AnalyzeResponse`;
- one recorded `FundamentalAssessmentResult`;
- human labels describing acceptable adjustment range, acceptable signals,
  expected counterfactual decision, required claims, forbidden claims, and
  whether abstention is appropriate.

## Leakage and fixture validation

The offline harness validates every case before computing metrics:

- the request `context_hash` must recompute exactly from the stored request;
- every evidence chunk publication timestamp must be at or before
  `snapshot_at`;
- the request schema version must match the current context schema.

That keeps the suite replayable and blocks accidental future leakage.

## Offline evaluation

Run the recorded suite without network or model calls:

```bash
make eval-fundamental-agent
```

The command prints JSON by default and exits non-zero only when a hard safety
gate fails.

Reported metrics include:

- structured-output validity;
- citation validity;
- material-claim citation coverage;
- unsupported-claim rate;
- abstention rate and appropriate-abstention precision;
- adjustment distribution;
- promotion/downgrade/unchanged counts;
- baseline-to-counterfactual confusion matrix;
- latency, token usage, and estimated cost;
- provider-failure and neutral-fallback rates.

Hard gates currently require:

- zero hard-blocker promotions;
- zero risk-field mutations;
- zero accepted unknown citations;
- zero accepted structured-output contract failures;
- provider failures to preserve the deterministic baseline.

## Live evaluation

Live evaluation is explicit and cannot run accidentally:

```bash
make eval-fundamental-agent-live CONFIRM_COST=yes
```

The command refuses to run unless:

- `CONFIRM_COST=yes` is passed; and
- `VERTEX_AI_PROJECT` is configured.

This keeps default tests and CI offline.

## Shadow reporting

Aggregate stored `shadow` runs with:

```bash
make fundamental-shadow-report
```

The shadow report groups runs by version stamp
(`model|prompt|context|assessment|policy`) and reports:

- promotion/downgrade/unchanged outcomes;
- status counts;
- rejection reasons;
- missing-information frequencies;
- baseline vs counterfactual matrix;
- latency and token/cost summaries.

It intentionally uses structured effect payloads only and does not emit raw
document text.

## Human review samples

Each evaluation report includes a bounded review sample set containing:

- baseline decision;
- counterfactual decision;
- context coverage summary;
- proposed adjustment;
- findings with citations;
- source links.

These samples are for manual labeling of overly positive, overly negative,
unsupported, or insufficient-evidence behavior before any rollout expansion.

## Rollout policy

- `off` remains the safe default.
- `shadow` is the proving ground for versioned prompts, models, schemas, and
  policy logic.
- `enforced` should only be enabled after reviewed frozen results and shadow
  metrics satisfy the documented hard gates and the team agrees on quality
  thresholds for agreement, unnecessary downgrades, and unsupported claims.
