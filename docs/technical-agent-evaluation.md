# Technical agent evaluation

The Technical Analyst stays `off`/`shadow` until frozen golden cases and
shadow metrics show it downgrades decisions correctly without ever upgrading
one or mutating risk math it should not touch.

## Evaluation artifacts

- `tests/fixtures/technical/*.json` stores one self-contained golden case per
  file
- `tests/fixtures/technical/factory.py` builds readable, small `TechnicalAnalysisRequest`/
  `TechnicalAssessmentResult`/`AnalyzeResponse` objects from fixture JSON,
  including recomputing `context_hash` so no fixture ever hand-writes one
- `tests/fixtures/technical/loader.py` auto-discovers fixtures, hydrates them,
  and dispatches each case to the correct execution layer
- `tests/test_technical_golden.py` is the regression suite
- `scripts/eval_technical_agent.py` is the CLI entrypoint used by the Make
  targets

Each fixture declares a `case_type`:

- `assessment` — run `TechnicalAnalyst.assess(request)` against a fake
  `TechnicalModelPort` that replays `model_output` (or raises on
  `{"provider_error": "..."}` entries), then asserts on the returned
  `TechnicalAssessmentResult`
- `policy` — call `apply_technical_policy(...)` directly against a frozen
  `assessment` payload, `deterministic_response`, and optional
  `post_fundamental_response`
- `workflow` — build a real `TechnicalAssessmentResult` and feed it through
  `build_analyst_views(technical=...)`, asserting the Critic-facing
  `AnalystView` is not `absent`
- `live_model` — store a frozen `TechnicalAnalysisRequest` in
  `tests/fixtures/technical/live/`; these cases are skipped offline and only
  really call Gemini under `make eval-technical-agent-live CONFIRM_COST=yes`

## Running the harness

Offline, fixture-only evaluation:

```bash
make eval-technical-agent
```

Manual live gate (confirmation required, never for CI):

```bash
make eval-technical-agent-live CONFIRM_COST=yes
```

Shadow aggregation from persisted runs:

```bash
make technical-shadow-report
```

To export a structured sample without raw model output:

```bash
make technical-shadow-report SAMPLE=20 OUT=reports/trust/technical-shadow-sample.csv
```

`make eval-technical-agent` exits non-zero on any failing case, completes in
well under 30 seconds, and the offline suite is fully frozen: no case uses
Vertex, Gemini, Alpha Vantage, or any other network I/O. `tests/test_technical_golden.py`
additionally monkeypatches `socket.socket.connect`/`connect_ex` around every
offline case so an accidental network call fails the suite instead of hanging
it.

## Fixture shape and auto-discovery

Adding `tests/fixtures/technical/<name>.json` automatically adds one pytest
case and one evaluation-harness case. There is no hand-maintained registry;
`tests/test_technical_golden.py` asserts the discovered fixture names against
a literal roster so a silently-skipped or accidentally-duplicated fixture
fails the build.

Each fixture includes:

- `case_type`, `description`, `mode` (`off` | `shadow` | `enforced`)
- `request` (`TechnicalAnalysisRequest` payload, before `context_hash` is
  recomputed)
- for `assessment` cases: `model_output` (a raw JSON string, or a list of
  strings/`{"provider_error": "..."}` entries to drive retries)
- for `policy`/`workflow` cases: `assessment` (a `TechnicalAssessmentResponse`
  + optional `validation` payload), `deterministic_response`, and optionally
  `post_fundamental_response`
- `expected` — the specific fields the case asserts on (for example
  `accepted`, `reason_codes`, `reason_codes_include`, `proposed_score_adjustment`,
  `applied_adjustment`, `final_decision`, `risk_fields_unchanged_or_cleared`,
  `technical_view_absent`)

## Adding a golden case

1. Copy an existing JSON fixture from `tests/fixtures/technical/`.
2. Pick the smallest execution layer that exercises the rule: `assessment`
   before `policy`, `policy` before `workflow`.
3. Freeze the input request, canned model output (or assessment payload), and
   the expected fields.
4. Run `make eval-technical-agent` and `make test`.
5. Do not edit other code just to register the case; discovery is automatic.
   If a case exposes a genuine validator bug, fix the validator in
   `app/services/technical_analyst.py` and add a focused unit test instead of
   weakening the fixture's expectations.

Malformed fixtures fail loudly with the fixture filename and the
missing/invalid field name (see `_case_from_payload` in
`tests/fixtures/technical/loader.py`).

### A note on `positive_adjustment`

`TechnicalAssessmentResponse` rejects `proposed_score_adjustment > 0` at the
Pydantic schema layer, before `_validate_bounds`'s `adjustment_out_of_bounds`
check ever runs. A `+1` proposal is therefore always reported as
`schema_validation_failed`, not `adjustment_out_of_bounds`. The
`positive_adjustment` fixture documents and freezes this: it is intentional
defense-in-depth, not a bug, and the fixture must not be "fixed" to produce
`adjustment_out_of_bounds` instead.

## Reading the shadow report

`make technical-shadow-report` summarizes persisted `technical_agent_effect`
payloads for `mode=shadow` runs only. It reports:

- adjustment distribution (`-2` through `0`)
- counterfactual decision-change rate: shadow runs where `decision_changed`
  was recorded true
- rejection rate by reason code
- `trend_state`/`momentum_state` distribution
- p50/p95/max latency and prompt/candidate/total token totals, joined from
  the `technical_analyst` `AgentRunStep` rows

This report is intentionally structured-only. `make technical-shadow-report
SAMPLE=20 OUT=...` exports one row per shadow run (`run_id`, `ticker`,
`applied_adjustment`, `final_decision`, `decision_changed`, `trend_state`,
`momentum_state`, `reason_codes`) and never includes raw model output or
prompts.

## Promotion criteria: `shadow` -> `enforced` (final, TA-7)

Do not flip `TECHNICAL_AGENT_MODE` to `enforced` until all of these hold.
The `Status` column reflects what this sandbox can verify right now: items
that only require the frozen offline suite are marked `met`/`not met`
honestly; every item that needs live shadow-run collection (no GCP ADC and
no ingested market data are available here, see `docs/trust-report.md`) is
marked `pending`. See `docs/trust/technical-shadow.md` for the full
evidence table once live data lands.

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | All TA-6 offline golden cases pass in CI | **met** | `make eval-technical-agent` passes: all fixtures in `tests/fixtures/technical/` (`assessment`/`policy`/`workflow` cases) pass, 0 failures. |
| 2 | >= 100 shadow runs across >= 5 sectors | pending | Requires `make technical-shadow-batch CONFIRM_COST=yes REPEAT=<N>` against an environment with GCP ADC + ingested market data. |
| 3 | 0 invariant violations: no upgrades, no changed risk numbers, no ungrounded numeric claims | pending | The 4 invariants (`check_technical_agent_effect_rank`, `check_technical_risk_fields_untouched_in_shadow`, `check_technical_numeric_claims_grounded`, `check_technical_step_zero_tokens_in_typed_mode`) are implemented and unit-tested offline in `tests/test_trust_invariants.py`; they still need to run clean against real shadow data via `scripts/trust_e2e.py`. |
| 4 | Assessment rejection rate <= 10%, no single reason code above 5% | pending | Needs `make technical-shadow-report` against a real persisted shadow sample. |
| 5 | Counterfactual downgrade rate between 5% and 40% | pending | Needs the same real shadow sample as above. |
| 6 | Manual review: >= 80% of sampled downgrades judged justified | pending | Needs `make technical-shadow-report SAMPLE=20 OUT=reports/trust/technical-shadow-sample.csv` plus owner labelling. |
| 7 | Usefulness: mean forward 20-day return of downgraded runs is below that of neutral runs (reported with sample size, no significance claim) | pending | `scripts/technical_backtest.py` is implemented and unit-tested offline against synthetic bars (`tests/test_technical_backtest.py`); it needs real persisted shadow runs plus stored price bars to produce the real table via `make technical-backtest`. |
| 8 | p95 added latency <= 8s, mean added cost per run reported | pending | Needs a real shadow sample; `scripts/trust_e2e.py` already prints per-run latency/token usage. |
| 9 | Human sign-off | pending | Blocked on all of the above. |

See `docs/trust/technical-shadow.md` for the exact commands required to move
each `pending` row to `met`/`not met` once an environment with working GCP
ADC and ingested market data is available.

## Live mode today

`make eval-technical-agent-live CONFIRM_COST=yes` does two things:

1. it replays the frozen offline fixtures, unchanged
2. it additionally loads `tests/fixtures/technical/live/*.json`, builds the
   real `TechnicalAnalyst` with `GeminiTechnicalModel`, and performs a real
   Vertex/Gemini call for each live case

Safety rules:

- `CONFIRM_COST=yes` is mandatory
- `VERTEX_AI_PROJECT` must be configured or the command fails immediately
- live fixtures assert structural properties only:
  - the response schema validates
  - the adjustment is within `[allowed_adjustment_min, allowed_adjustment_max]`
  - the adjustment is `0` for the clean, confirmatory uptrend case
  - required risk-flag categories are present for the seeded breakdown and
    divergence cases
  - every numeric claim passes citation validation
- it prints latency, token usage, and an estimated cost per case (using
  the price catalog in `app/pricing/catalog.json`)
- live cases are marked `@pytest.mark.live` and, in `tests/test_technical_golden.py`,
  are only ever run against a stub model port keyed off the fixture's
  `run_id` — `make test` and CI never make a real network call, even for
  `live`-marked tests
- the real Gemini call only happens through `scripts/eval_technical_agent.py
  --live --confirm-cost yes`, never through `make test` or any
  `pull_request`-triggered CI workflow

At least three `live_model` fixtures exist: `clean_uptrend`, `breakdown`, and
`divergence` (`tests/fixtures/technical/live/`).
