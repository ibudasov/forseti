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