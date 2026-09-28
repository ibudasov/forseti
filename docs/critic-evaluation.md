# Critic evaluation

The Critic stays `off` by default until frozen golden cases and shadow metrics
show that it catches bad recommendations without degrading good ones.

## Evaluation artifacts

- `tests/fixtures/critic/*.json` stores one self-contained golden case per file
- `tests/fixtures/critic/loader.py` auto-discovers fixtures, hydrates them, and
  dispatches each case to the correct execution layer
- `tests/test_critic_golden.py` is the regression suite
- `scripts/eval_critic.py` is the CLI entrypoint used by the Make targets

Each fixture declares a `case_type`:

- `policy` — call `apply_critic_policy(...)` directly
- `workflow_revision` — drive the bounded revision path in
  `AgenticAnalysisWorkflow`
- `model_failure` — run the `Critic` wrapper with a fake failing model port,
  then feed the fallback result into policy

## Running the harness

Offline, fixture-only evaluation:

```bash
make eval-critic
```

Manual live gate (confirmation required, never for CI):

```bash
make eval-critic-live CONFIRM_COST=yes
```

Shadow aggregation from persisted runs:

```bash
make critic-shadow-report
```

`make eval-critic` exits non-zero on any failing case. The suite is fully
offline: no case uses Vertex, Gemini, Alpha Vantage, or network I/O.

## Fixture shape and auto-discovery

Adding `tests/fixtures/critic/<name>.json` automatically adds one pytest case and
one evaluation-harness case. There is no hand-maintained registry.

Each fixture includes:

- `case_type`
- `mode`
- `request` (`CritiqueRequest` payload)
- `baseline` (`DraftRecommendation` payload)
- `response` (`AnalyzeResponse` payload)
- either `critique_result`, `critique_results` + `revision_outcome`, or
  `model_failure`
- `expected.effect` plus any extra expectations such as
  `decision_synthesizer_call_count`

Use `__construct__: true` only when the case deliberately needs an otherwise
invalid object shape, such as a trade draft with a missing risk field or an
uncited material finding.

## Adding a golden case

1. Copy an existing JSON fixture from `tests/fixtures/critic/`.
2. Pick the smallest execution layer that exercises the rule:
   `policy` before `workflow_revision`, `workflow_revision` before
   `model_failure`.
3. Freeze the input request, baseline, canned critique payload, and expected
   effect fields.
4. Run `make eval-critic` and `make test`.
5. Do not edit other code just to register the case; discovery is automatic.

Malformed fixtures fail loudly with the fixture filename and the missing/invalid
field name.

## Reading the shadow report

`make critic-shadow-report` summarizes persisted `critic_effect` payloads for
`mode=shadow` runs only. It reports:

- objection rate: runs with any objections / total shadow runs
- decision-change rate: runs where the counterfactual critic decision differs
  from the baseline decision
- severity distribution from the `critic_guardrail` trace step
- deterministic-versus-model objection counts

This report is intentionally structured-only. It does not print raw prompt or
evidence text.

## Promotion criteria: `shadow` -> `enforced`

Do not flip `CRITIC_MODE` to `enforced` until all of these hold:

1. all frozen golden cases pass in CI and locally
2. at least 100 shadow runs have been collected across multiple tickers/sectors
3. zero observed cases of shadow mode changing deterministic risk numbers or
   upgrading a decision label
4. zero model-failure cases where the degraded path changed the deterministic
   response
5. manual review of a representative objection sample shows false objections at
   or below 5%
6. the shadow report stays stable for 14 consecutive days with no unexplained
   severity spikes
7. a human reviewer signs off on the shadow report, golden output, and scorecard

## Live mode today

The CLI exposes `--live` to mirror the fundamental-agent harness and to keep the
manual confirmation gate explicit. Today it replays the same frozen critic
fixtures after checking `--confirm-cost yes` and `VERTEX_AI_PROJECT`; there are
no live-only critic golden cases yet.
