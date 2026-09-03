# Product scorecard

CI answers "does the code run?". The scorecard answers "did the product get
better?" — the question that actually matters when deciding whether to merge
an agent-authored change.

`make scorecard` runs the deterministic screening engine
(`app/services/screening.run_screening`) against a **frozen fixture
universe**, computes a small set of metrics from the result, and prints them
as a Markdown table. CI posts that table as a job summary and a sticky PR
comment on every push.

**The merge rule this enables:** an iteration is successful iff CI is green,
the metric the issue promised to move actually moved, and no other metric
regressed.

## Why a frozen fixture, not live data

`build_scorecard` (`app/services/scorecard.py`) is a pure function over an
existing `ScreeningResponse` — no DB access, no I/O, no wall-clock time. Two
runs of the same commit against the same fixture produce byte-identical JSON.
If the scorecard read live prices instead, the numbers would move between two
runs of the same commit and the delta would become noise. `run_screening`
and `analyze` accept `today` and `engine` seams precisely so a caller can pin
both.

The fixture lives at `tests/fixtures/scorecard/universe.json`, is committed,
and was produced once by `scripts/generate_scorecard_fixture.py` with a fixed
seed and a fixed "today" (`2026-03-01`). It spans six securities and every
decision the engine can reach:

| Ticker | Decision | Why |
| --- | --- | --- |
| `TRENDCORE` | trade | Clean fundamentals + technicals, no warnings. |
| `VALUEWATCH` | watchlist | Checklist score (7/11) lands in the watchlist band. |
| `STALESIGNAL` | watchlist | Trade-worthy score, but 30-day-stale prices cap it. |
| `GATEWATCH` | watchlist | Fewer than the 200 bars the data gate requires. |
| `RISKYCALL` | no_trade | RSI-overbought veto. |
| `STEADYHOLD` | no_trade | No fundamentals or technicals on file; score too low. |

`scripts/scorecard.py` seeds this fixture into a disposable `*_test`
database (the same safety convention `tests/conftest.py` uses), calls
`run_screening(engine=..., today=2026-03-01)`, and hands the response to
`build_scorecard`. **No network access, no API keys, no LLM calls**: nothing
in this path calls ingestion, Alpha Vantage, or Google Cloud, so the
scorecard runs green with `ALPHA_VANTAGE_API_KEY` unset and no Google
credentials present.

## Metrics and their direction

Metric direction lives in a module-level lookup table in
`app/services/scorecard.py`, not an if/else chain, and is exposed only
through `ScorecardDelta.improvements` / `.regressions` / `.has_regressions` —
callers never inspect raw dicts to decide whether an iteration helped.

| Metric | Direction | Why |
| --- | --- | --- |
| `failed_count` | lower is better | An exception while analyzing a ticker is always a bug. |
| `zero_confidence_count` | lower is better | A confidence of exactly 0.0 means the engine had nothing to say. |
| `warning_counts.*` (`no_earnings_data`, `no_fundamentals`, `no_technical_features`, `stale_price_data`, `insufficient_price_data`, …) | lower is better | Every warning marks missing or stale input data — a pure diagnostic that should only ever shrink. |
| `trade_count` / `watchlist_count` / `no_trade_count` | neutral | ibudasov/forseti#25 will legitimately change this split once earnings data exists. A gate that fires on those counts would block the very work it is meant to protect. |
| `universe_size` / `analyzed_count` | neutral | Track the size of the fixture, not the quality of the analysis. |

## Moving the baseline

`docs/scorecard-baseline.json` is committed so deltas survive across PRs — a
regression is caught even if it arrives in two separate merges. It is
**never** rewritten by the `scorecard` target itself. To move it
deliberately:

```
make scorecard-baseline
git diff docs/scorecard-baseline.json
```

Review the diff like any other change before committing it. If the diff
touches a metric you didn't expect to move, that's the scorecard doing its
job — investigate before accepting it.

## Out of scope (and who owns extending this)

This issue only measures; it does not change analyzer, screening, or
ingestion behaviour. Two metrics are deliberately **not** part of this
scorecard because the fields they need don't exist yet, and reaching for them
with `getattr` fallbacks would be worse than not having them:

- **ibudasov/forseti#22** (honest count of real vs. fabricated trace steps)
  must add its own metric to `app/services/scorecard.py` as part of its own
  PR, once `ScreeningItem`/trace data actually carries that information.
- **ibudasov/forseti#23** (`ScreeningItem.debug_reason`) must add its own
  metric to `app/services/scorecard.py` as part of its own PR, once that
  field exists.

## CI wiring

`.github/workflows/test.yml` runs `make scorecard` after the test/coverage
step, appends the rendered table to the Actions job summary, and — on pull
requests — posts (or updates in place) a single sticky comment on the PR,
keyed by an HTML marker comment so a second push refreshes the same comment
instead of adding a new one. The step requires no secret: it only needs the
disposable test database the `test` job already provisions.
