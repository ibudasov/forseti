# Golden cases

Golden cases freeze three things for one representative analysis:

1. the database inputs the deterministic engine reads
2. the run parameters (`ticker`, frozen `today`, and optional cassette)
3. the expected deterministic output fields

They live under `tests/fixtures/golden/<case-name>/` and are deliberately small:

```text
tests/fixtures/golden/
  clear_trade/
    case.json
    inputs.json
    expected.json
    cassette/003-event-000.json
  clear_no_trade/
  ambiguous/
```

## File responsibilities

- `case.json` explains why the scenario belongs in the suite and why each frozen
  number matters.
- `inputs.json` freezes the single-security dataset:
  `security`, `price_bar`, `technical_feature`, `fundamental`,
  `earnings_event`, and `macro_daily`.
- `expected.json` contains only the deterministic fields the harness compares:
  `decision`, `entry_range`, `stop_loss`, `take_profit`, `risk_reward`,
  `position_size_eur`, `confidence`, `engine_version`, `warnings`, and
  `reasons`.
- `cassette/` contains the recorded ADK event files used to replay the agent
  layer without a live model call.

## Synthetic price bars

`tests/fixtures/golden/price_series.py` owns the deterministic series builders.
Use them instead of pasting hundreds of literal bars:

- `build_trade_ready_series(...)` for a tradeable swing-high pullback
- `build_uptrend_series(...)` for a clean monotonic history

The helpers are seeded by explicit numeric arguments, so they produce identical
bars on every machine.

## Adding or updating a case

1. Add or edit `case.json`, `inputs.json`, and `expected.json`.
2. Seed the fixture in a disposable test database.
3. Run the current deterministic engine against the fixture.
4. Compare the produced values with the intended scenario and update the human
   rationale in `case.json`.
5. Only then commit the reviewed `expected.json`.

## Regenerating `expected.json` is deliberate

Do not auto-refresh `expected.json` when a test goes red. A red golden test
means one of three things changed: the engine logic, the fixture inputs, or the
assertion harness. Investigate first, explain the change, and regenerate the
expected file only as an explicit reviewed decision.
