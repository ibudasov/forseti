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

Environment variables:

- `EDGAR_USER_AGENT` (optional, default: `Forseti/0.1 (forseti-dev@example.com)`)
- `ALPHA_VANTAGE_API_KEY` (optional; when unset earnings ingestion is skipped)
- `INGEST_PRICE_PERIOD` (optional, default: `2y`)

The ingestion commands are idempotent and can be safely re-run.