# Forseti FastAPI Project

A minimalistic trading advisor

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

## Analyze endpoint

`POST /analyze` accepts a ticker abbreviation and returns a deterministic placeholder recommendation that is persisted when the database lookup and save path are available.

Example request:

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "NVDA",
    "account_size_eur": 10000,
    "risk_pct": 0.01,
    "max_position_size_eur": 500
  }'
```

Example response:

```json
{
  "ticker": "NVDA",
  "decision": "trade",
  "entry_range": [101.9875, 103.0125],
  "stop_loss": 97.9080,
  "take_profit": [108.1188, 110.1719],
  "risk_reward": 1.5,
  "position_size_eur": 500.0,
  "confidence": 0.72,
  "reasons": [
    "Latest close moved higher than the deterministic momentum threshold.",
    "Risk parameters were derived from the latest close using the placeholder engine."
  ],
  "warnings": [],
  "engine_version": "v1.placeholder.0",
  "created_at": "2026-01-02T00:00:00Z",
  "trace_id": "00000000-0000-0000-0000-000000000000"
}
```

This is a deterministic placeholder engine for the current phase. It validates ticker-only input and produces stable structured output while leaving room for richer rules, ingestion, and future RAG-backed explanations.
## Ticker endpoint

### `GET /ticker/{symbol}`

Returns the stored profile, market-data coverage, and data-freshness status for a given ticker symbol.

**Path parameter:** `symbol` — validated and normalized (strip, uppercase; must match `[A-Z0-9.-]`, ≤10 chars, ≤5 alpha chars, no URL-like strings).

**Example request:**
```bash
curl -s http://127.0.0.1:8000/ticker/NVDA | python3 -m json.tool
```

**Example 200 response (abridged):**
```json
{
  "ticker": "NVDA",
  "name": "NVIDIA Corporation",
  "exchange": "NASDAQ",
  "sector_tag": "ai",
  "currency": "USD",
  "is_active": true,
  "latest_price_bar": { "bar_date": "2026-01-02", "close": 102.5, "volume": 1100000 },
  "price_bars_stored": 2,
  "data_freshness": {
    "latest_price_bar_date": "2026-01-02",
    "price_data_age_days": 1,
    "stale_threshold_days": 7,
    "is_price_data_stale": false
  },
  "latest_technical_features": { "as_of_date": "2026-01-02", "rsi_14": 58.1234 },
  "latest_fundamentals": { "as_of_date": "2025-12-31", "revenue_growth": 0.62 },
  "next_earnings_date": "2026-02-25",
  "warnings": []
}
```

**Error responses:**

| Case | Status | Body |
|---|---|---|
| Symbol fails validation | 422 | `{"detail": "<message>"}` |
| Valid symbol, not in DB | 404 | `{"detail": "ticker_not_found: NVDA"}` |

**Warning vocabulary** (snake_case strings in `warnings` list):

- `no_price_data` — zero price bars stored
- `stale_price_data` — latest bar older than 7 days
- `no_technical_features` — no technical feature row
- `no_fundamentals` — no fundamental row
- `no_earnings_data` — no earnings events at all
- `security_inactive` — `Security.is_active` is `false`
