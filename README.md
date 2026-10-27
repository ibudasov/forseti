# Forseti

[![CI Tests](https://github.com/ibudasov/forseti/actions/workflows/test.yml/badge.svg)](https://github.com/ibudasov/forseti/actions/workflows/test.yml)
[![Coverage Badge](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/ibudasov/c379641f823db1522632b363883f81f4/raw/coverage.json)](https://github.com/ibudasov/forseti/actions/workflows/test.yml)

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

- [Trust report](docs/trust-report.md)

## Screening endpoint

`GET /screening` runs the existing deterministic analyzer across the active universe and returns a compact, read-only batch result sorted by actionability: trade first, then watchlist, then no-trade, then errors.

```bash
curl -s http://127.0.0.1:8000/screening | python3 -m json.tool
```

The endpoint is intentionally write-free: it does not persist recommendations and it bypasses the agentic workflow. For per-ticker detail or audit trail, use `POST /analyze`.

## Dependencies

`pyproject.toml` is the only place dependencies are declared: runtime packages in
`[project.dependencies]`, development tools in `[project.optional-dependencies].dev`
(`test` is an alias). `requirements.txt` (runtime) and `requirements-dev.txt`
(runtime + dev) are hash-pinned lock files generated from it.

To add a package: edit `pyproject.toml`, run `make lock`, and commit both lock files.
CI runs `deptry`, `pip-audit` and `pip check` to catch unused, missing or vulnerable packages.

## Container runtime

The image and lock-generation environment use Python 3.12. Compose builds with
`INSTALL_DEV=true` for development and `make test`. For deployment:

```bash
docker build --build-arg INSTALL_DEV=false -t forseti:cloud .
```

This image excludes development dependencies and compilers, runs as uid 10001,
and honors `PORT` (default 8000). TLS verification is on; only restricted build
environments should supply `--build-arg PIP_TRUSTED_HOSTS="pypi.org files.pythonhosted.org"`.
Forwarded headers are trusted for Cloud Run's ingress proxy; do not expose the
container directly to untrusted clients on another platform.

Use `.env.cloud.example` as the cloud configuration reference, inject secrets at
runtime, and use the service's managed identity for Google credentials. `APP_ENV=cloud`
rejects unsafe debug/override switches, silent RAG fallback, missing projects, the
local database default, and invalid agent modes/contracts before serving requests
or running jobs. Environment files are never copied into the image.

API requests and jobs share one bounded database pool per URL per process; pools
are disposed on API shutdown and job exit. See [database configuration and connection
budget](docs/database.md#connection-reuse-and-cloud-sql) before increasing concurrency
or replica limits. Local ADC files mounted by Compose must be readable by uid 10001.
Make targets that write migrations or bind-mounted reports run with the host
UID/GID so their output remains writable locally; the deployment image stays uid 10001.

## Test

```bash
make test
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

- `EDGAR_USER_AGENT` (optional, default: `Forseti/0.1 (gazer-flair9o@icloud.com)`)
- `ALPHA_VANTAGE_API_KEY` (optional; when unset earnings ingestion is skipped)
- `INGEST_PRICE_PERIOD` (optional, default: `2y`)

The ingestion commands are idempotent and can be safely re-run.

## Evaluation engine

## Agent workflow

Forseti supports two execution modes behind the same analysis service. The
default `PIPELINE_MODE=linear` runs the deterministic rules and risk pipeline.
`PIPELINE_MODE=agentic` adds a Google ADK supervisor with parallel-minded
structured-data and retrieval steps, fundamental and technical specialists, a
decision synthesizer, and a critic/guardrail. The deterministic rules and risk
engine remain authoritative in both modes; an agent can only downgrade a
decision or confidence and cannot change trade numbers. This includes the
Fundamental Analyst: positive evidence may support its narrative, but its score
adjustment is limited to `[-2, 0]`. See [ADR 008](docs/adr/008-agent-authority.md)
for the authority matrix.

Agentic runs persist an ordered execution trace. The API documentation describes
how to request and retrieve that trace. Configure the mode and model through
the corresponding variables in `.env.example`.

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
