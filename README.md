# Forseti 

A minimalistic trading advisor: accepts a ticker (e.g.: MSFT) and gived you an advise on either execute on the traid or refrain from it.
Very basic UI is here http://127.0.0.1:8000/docs

## Quick start (only for OSX)

1. Create a virtual environment:

```bash
./setup.sh
```

2. Open http://127.0.0.1:8000 in your browser.

## Docs

http://127.0.0.1:8000/docs

## Test

```bash
python3 -m pytest
```

## Data ingestion

Run the full ingestion pipeline:

```bash
make ingest
```

Run a single source:

```bash
python -m app.ingestion.run --source prices
python -m app.ingestion.run --source fundamentals --ticker NVDA
```

Sources:

- prices: yfinance OHLCV daily bars (2y by default)
- vix: yfinance `^VIX` close values
- fundamentals: SEC EDGAR company facts
- earnings: Alpha Vantage earnings calendar CSV
- features: computed technical indicators (RSI-14, SMA-50/200, volume trend)

Environment variables:

- `EDGAR_USER_AGENT` (optional, default: `Forseti/0.1 (forseti-dev@example.com)`)
- `ALPHA_VANTAGE_API_KEY` (optional; when unset earnings ingestion is skipped)
- `INGEST_PRICE_PERIOD` (optional, default: `2y`)

The ingestion commands are idempotent and can be safely re-run.

## Evaluation engine

The deterministic evaluation engine analyzes securities in a six-step pipeline:

### Decision making overview

1. **Data Gate** — Checks freshness and completeness of stored data (warnings: `no_price_data`, `stale_price_data`, `security_inactive`, `insufficient_price_data`, `no_technical_features`, `no_fundamentals`, `no_earnings_data`)
2. **Hard Vetoes** — Applies strict rules that block trade decisions (RSI overbought > 70, VIX panic > 30, earnings too close within 7 days, price deep below SMA200)
3. **Checklist Scoring** — Evaluates 9 fundamental and technical rules (max 11 points):
   - Revenue growth > 0.15 YoY (+2)
   - Free cash flow > 0 (+2)
   - Debt/equity < 1.0 (+1)
   - EPS trend > 0 (+1)
   - Close > SMA50 (+1)
   - Close > SMA200 (+1)
   - RSI between 45–65 (+1)
   - Volume trend > 1.0 (+1)
   - VIX < 25 (+1)
4. **Decision Thresholds** — Maps score to decision:
   - Score ≥ 8 → `trade`
   - Score 5–7 → `watchlist`
   - Score ≤ 4 → `no_trade`
5. **Risk Math** (trade only) — Calculates entry range, stop loss, take profit levels, position sizing, and validates risk/reward ≥ 1.5
6. **Confidence** — Clamps `(score / 11) - (0.05 × warning_count)` to [0, 1]

### Features

- **Pure functions** for all calculations: vetoes, checklist, risk math are unit-testable without database or network
- **Deterministic**: identical inputs produce identical decisions
- **Fully persisted** via `Recommendation` table for audit and backtesting
- **Frozen constants** (not settings): all thresholds are module-level constants; only account capital and risk percentage are configurable via environment
