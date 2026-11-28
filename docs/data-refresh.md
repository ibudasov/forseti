# Daily Data Refresh (`forseti-daily-refresh`)

Forseti refreshes structured market data and RAG documents automatically after each US trading day via the private Cloud Run Job `forseti-daily-refresh`, triggered by Cloud Scheduler.

## Schedule and Calendar Gate

- **Cloud Scheduler**: `forseti-daily-refresh` runs **daily** at `30 18 * * *` (`America/New_York`, DST-aware) with `retry_count = 1`. Non-trading days only emit a `market_closed` heartbeat, which the missing-refresh alert counts as healthy (see [observability](observability.md)).
- **Target**: `POST` to the Cloud Run v2 `forseti-daily-refresh:run` endpoint using `forseti-scheduler` OAuth credentials.
- **Trading-calendar gate**: `python -m app.jobs.daily_refresh` checks the `XNYS` calendar (`exchange-calendars`) for today's `America/New_York` date. On US market holidays or weekends, the job logs `market_closed`, emits `daily_refresh_status` (`status="market_closed"`), and exits `0` without ingesting. Passing `--force` overrides this gate.

## Pipeline Stages

1. **Universe & Structured Ingestion** (`app.ingestion.run`):
   - Seeds the active security universe (`seed_universe()`).
   - Runs `prices`, `vix`, `fundamentals`, `earnings`, and `features`.
   - Writes the coverage summary to `INGEST_REPORT_PATH` (`/mnt/artifacts/reports/ingest/coverage-latest.json` in Cloud Run).
2. **RAG Ingestion & Raw Archive** (`app.rag.cli` / `app.rag.pipeline`):
   - Skipped when `--skip-rag` is passed.
   - Fetches SEC EDGAR filings, Yahoo Finance news, and earnings materials for each active security.
   - Before chunking, when `RAW_DOCUMENT_DIR` is set (`/mnt/artifacts/raw` in Cloud Run), writes the original payload to `${RAW_DOCUMENT_DIR}/{source_type}/{ticker}/{YYYY-MM-DD}/{source_hash}.{ext}` idempotently. Archive I/O errors log a warning and do not fail ingestion.

## Failure Policy

- `INGEST_MAX_FAILED_TICKER_RATIO` defaults to `0.1` (10%).
- Any source-level failure (`failed_sources`) or any source whose `len(failed_tickers) / attempted_tickers > INGEST_MAX_FAILED_TICKER_RATIO` exits nonzero (`1`).
- Partial ticker failures at or below `INGEST_MAX_FAILED_TICKER_RATIO` log an explicit warning and exit `0`.
- Each run writes `${REPORT_DIR}/ingest/YYYY-MM-DD.json` with per-source row counts, attempted-ticker counts, failed tickers, failed sources, stored chunks, stage durations, and exit status, and emits one structured `daily_refresh_status` log event (`status="success"|"failed"|"market_closed"`).

## Reruns and Backfill

- **Routine rerun (owner-only)**: Comment `/rerun-refresh` on tracking issue #121. `ops.yml` executes `forseti-daily-refresh` as `forseti-verifier` without `--force` and posts a sanitized result.
- **Forced backfill (owner-authorized)**: Execute with `--force` (and optionally `--skip-rag`) via `gcloud run jobs execute forseti-daily-refresh --args="--force"`.

## Troubleshooting & Upstream Limitations

- **Yahoo Finance (`yfinance`) throttling**: Transient HTTP 429/rate-limit errors surface in `failed_tickers_by_source` for `prices` or `company_news`/`sector_news`.
- **SEC EDGAR**: Requires a valid contact `EDGAR_USER_AGENT`; missing or malformed HTML sections surface as `extraction_failed` in RAG coverage rather than fabricated text.
- **Alpha Vantage (`EARNINGS_CALENDAR`)**: `EARNINGS_CALENDAR` is a premium endpoint; with `EARNINGS_FALLBACK_ENABLED=true`, Yahoo Finance earnings fallback is used when Alpha Vantage is unavailable or rate-limited.
