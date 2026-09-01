# Product scorecard

CI answers "does the code run?". The scorecard answers "did the product get
better?". Those are different questions, and only the second one matters when
deciding whether to merge an agent-authored change.

**The merge rule this enables:** an iteration is successful iff CI is green,
the metric the issue promised to move actually moved, and no other metric
regressed.

## Running it

```
make scorecard
```

Seeds the frozen fixture universe (`tests/fixtures/scorecard/universe.json`)
into a disposable database, runs the existing screening pipeline against the
frozen `today` recorded in that fixture, prints the resulting scorecard as a
Markdown table, compares it against the committed baseline
(`docs/scorecard-baseline.json`), and exits `1` if any metric regressed.

## Metrics and their direction

| Metric | Direction | Why |
|---|---|---|
| `universe_size` | Neutral | Size of the fixture universe; only changes when the fixture itself is edited. |
| `analyzed_count` | Higher is better | Tickers the pipeline actually produced an item for. |
| `failed_count` | Lower is better | Tickers the pipeline could not analyze at all. |
| `trade_count` | Neutral | See rationale below — this split is expected to move. |
| `watchlist_count` | Neutral | See rationale below. |
| `no_trade_count` | Neutral | See rationale below. |
| `zero_confidence_count` | Lower is better | Items that scored exactly `0.0` — a sign the checklist found nothing useful, not that the ticker is a genuine no-trade. |
| `warning_counts.*` (e.g. `no_earnings_data`, `no_fundamentals`, `stale_price_data`, `no_technical_features`) | Lower is better | Each is a diagnostic signal that upstream data is missing or stale, independent of the trading decision. |

### Why `trade_count` / `watchlist_count` / `no_trade_count` are neutral

ibudasov/forseti#25 will legitimately change the trade/watchlist split once
earnings data exists. A gate that fires on those counts would block the very
work it is meant to protect. The diagnostic metrics (`zero_confidence_count`
and the warning histogram) are the ones that must only ever improve — they
measure data completeness, not trading judgement.

## The frozen-fixture rule

The scorecard **must** run against seeded fixture data and a frozen `today`.
If it reads live prices, the numbers move between two runs of the same
commit and the delta becomes noise. `run_screening(engine=..., today=...)`
already accepts both seams, so the scorecard entrypoint (`scripts/scorecard.py`)
seeds `tests/fixtures/scorecard/universe.json` and passes the fixture's own
`today` date through — nothing is read from a live clock or a live market
data provider.

The scorecard also requires **no network, no API keys, no LLM calls**. It
produces identical output with `ALPHA_VANTAGE_API_KEY` unset and no Google
credentials present, because it never calls ingestion or the RAG/LLM layer —
only `run_screening` against already-seeded rows.

The fixture is committed, not generated at runtime, by
`scripts/generate_scorecard_fixture.py` (run once, with a fixed seed, to
produce `tests/fixtures/scorecard/universe.json`). This keeps the scorecard
entrypoint free of any dependency on test code.

## Moving the baseline

`docs/scorecard-baseline.json` is a committed snapshot so deltas survive
across PRs and a regression is caught even when it arrives in two separate
merges. Never hand-edit it. If a change legitimately moves a metric (for
example, fixing a data-completeness bug), regenerate it explicitly:

```
make scorecard-baseline
```

This is a separate target from `make scorecard` on purpose: rewriting the
baseline is always an explicit, reviewable diff in its own commit, never a
side effect of a normal `make scorecard` run.

## Out of scope (and who owns extending it)

This scorecard only measures the fields that exist today on `ScreeningItem`
and `ScreeningResponse`. It deliberately does not reach for fields that don't
exist yet via `getattr` fallbacks:

- `ScreeningItem.debug_reason` does not exist yet. ibudasov/forseti#23 is
  required to extend `app/services/scorecard.py` with a metric built on that
  field as part of its own PR.
- An honest count of real vs. fabricated trace steps does not exist yet.
  ibudasov/forseti#22 is required to extend `app/services/scorecard.py` with
  that metric as part of its own PR.

This issue only measures; it does not change analyzer, screening, or
ingestion behaviour.
