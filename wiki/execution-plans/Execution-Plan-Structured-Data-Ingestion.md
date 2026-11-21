Execution plan for a coding agent to implement structured data ingestion in [ibudasov/forseti](https://github.com/ibudasov/forseti). This is <b>guide 1 of 3</b> for the <b>Weeks 3–4 — Structured data + rules engine</b> phase of the roadmap. Scope of this guide: <b>ingestion only</b> — OHLCV prices, VIX, fundamentals, and earnings dates from the selected free sources. Feature computation (RSI, moving averages, volume trend) and the trade / no-trade rules engine are covered by the following guides. Every step below is written to be executed mechanically, in order, without design decisions left to the agent.

> 🎯 Mission: add deterministic, idempotent ingestion jobs that populate `price_bar`, `macro_daily`, `fundamental`, and `earnings_event` for every active ticker in the `security` table, from free public sources, runnable via a single CLI command (`make ingest`), with tests that require no network access.

## 1. Verified current state of the repository

These facts were verified against `main` on 2026-08-06. Trust them; re-inspect a file only if it has changed since.

| File | What exists today |
| --- | --- |
| `app/main.py` | FastAPI app; `GET /`, `GET /healthz`, `POST /analyze`; dependency `get_analysis_engine()` returns `get_engine()` |
| `app/schemas/analyze.py` | `AnalyzeRequest` (validates ticker via `validate_and_normalize_ticker`), `AnalyzeResponse` (floats at the API boundary) |
| `app/services/analyzer.py` | `validate_and_normalize_ticker(ticker: str) -> str` raising `ValueError`; `ENGINE_VERSION = "v1.placeholder.0"`; placeholder engine |
| `app/db/models.py` | SQLModel tables: `Security`, `PriceBar`, `Fundamental`, `EarningsEvent`, `MacroDaily`, `TechnicalFeature`, `Recommendation` |
| `app/db/repository.py` | `get_security(ticker, engine=None)`, `get_latest_bars(ticker, n, engine=None, as_of_date=None)`, `upsert_*` helpers, `save_recommendation` |
| `app/db/session.py` | `get_engine()`, `get_session(engine=None)` |
| `app/settings.py` | pydantic-settings, `DATABASE_URL`, cached `get_settings()` |
| `tests/test_db.py` | `db_engine` fixture (testcontainers Postgres or `TEST_DATABASE_URL`); repository tests |
| `tests/test_integration.py` | `TestClient`; `db_client` fixture overriding `get_analysis_engine` via `app.dependency_overrides` |
| `Makefile` | `make test`, `make migrate` |
| `.github/instructions/codestyle.instructions.md` | Clean Code + Object Calisthenics rules — <b>binding for all code produced here</b> |
| `.github/workflows/` | CI runs pytest — all tests must pass before finishing |

Relevant model details (exact, from `app/db/models.py`):

- `Security`: `id`, `ticker` (unique), `name`, `exchange`, `sector_tag` (`ai|defence|nuclear|green_energy|quantum|robotics|space`), `currency`, `is_active`, `created_at`
- `PriceBar`: PK (`security_id`, `bar_date`); `open/high/low/close` `Numeric(14,4)`; `volume` BigInteger
- `Fundamental`: `id` PK; `security_id`, `as_of_date`; nullable `revenue_growth`, `fcf`, `debt_to_equity`, `eps_trend`, `margins`; `raw_payload` JSONB
- `EarningsEvent`: PK (`security_id`, `report_date`); `confirmed` bool
- `MacroDaily`: exists, but its exact fields were not verified in the previous plan — Phase 0 starts by reading `models.py` and using its fields exactly (expected: a date key plus a VIX close value)
- Before writing any upsert, read the existing `upsert_*` functions in `repository.py` and reuse them; add a new function only if the needed one is missing, following the existing style (`engine = engine or get_engine()`, `with get_session(engine) as session`, sqlmodel)

## 2. Ingestion contract (exact specification)

### Data sources (frozen decisions)

> 📐 Explicit decisions (do not reconsider): OHLCV prices and VIX come from <b>yfinance</b> (no API key). Fundamentals come from <b>SEC EDGAR</b> only (free, official, no key). Earnings dates come from <b>Alpha Vantage </b><b>`EARNINGS_CALENDAR`</b> — one request covers the whole market for a 3-month horizon, so the free quota (\~25 requests/day) is never a bottleneck. Finnhub is documented as a fallback only and is NOT implemented in this guide. No paid data sources.

### Universe seed

Ingestion processes only rows in `security` with `is_active = true`. Provide a seed function with this initial universe (35 US tickers, 5 per sector, all `currency = "USD"`):

| sector\_tag | ticker | name | exchange |
| --- | --- | --- | --- |
| ai | NVDA | NVIDIA Corporation | NASDAQ |
| ai | MSFT | Microsoft Corporation | NASDAQ |
| ai | GOOGL | Alphabet Inc. (Class A) | NASDAQ |
| ai | AMD | Advanced Micro Devices, Inc. | NASDAQ |
| ai | PLTR | Palantir Technologies Inc. | NASDAQ |
| defence | LMT | Lockheed Martin Corporation | NYSE |
| defence | RTX | RTX Corporation | NYSE |
| defence | NOC | Northrop Grumman Corporation | NYSE |
| defence | GD | General Dynamics Corporation | NYSE |
| defence | KTOS | Kratos Defense & Security Solutions, Inc. | NASDAQ |
| nuclear | CEG | Constellation Energy Corporation | NASDAQ |
| nuclear | VST | Vistra Corp. | NYSE |
| nuclear | SMR | NuScale Power Corporation | NYSE |
| nuclear | OKLO | Oklo Inc. | NYSE |
| nuclear | BWXT | BWX Technologies, Inc. | NYSE |
| green\_energy | NEE | NextEra Energy, Inc. | NYSE |
| green\_energy | ENPH | Enphase Energy, Inc. | NASDAQ |
| green\_energy | FSLR | First Solar, Inc. | NASDAQ |
| green\_energy | BE | Bloom Energy Corporation | NYSE |
| green\_energy | PLUG | Plug Power Inc. | NASDAQ |
| quantum | IBM | International Business Machines Corporation | NYSE |
| quantum | IONQ | IonQ, Inc. | NYSE |
| quantum | RGTI | Rigetti Computing, Inc. | NASDAQ |
| quantum | QBTS | D-Wave Quantum Inc. | NYSE |
| quantum | QUBT | Quantum Computing Inc. | NASDAQ |
| robotics | ISRG | Intuitive Surgical, Inc. | NASDAQ |
| robotics | TER | Teradyne, Inc. | NASDAQ |
| robotics | ZBRA | Zebra Technologies Corporation | NASDAQ |
| robotics | SYM | Symbotic Inc. | NASDAQ |
| robotics | SERV | Serve Robotics Inc. | NASDAQ |
| space | RKLB | Rocket Lab USA, Inc. | NASDAQ |
| space | LUNR | Intuitive Machines, Inc. | NASDAQ |
| space | ASTS | AST SpaceMobile, Inc. | NASDAQ |
| space | SPCE | Virgin Galactic Holdings, Inc. | NYSE |
| space | PL | Planet Labs PBC | NYSE |

Seeding must be idempotent: upsert by `ticker`; never duplicate; update `name`/`exchange`/`sector_tag` on drift but <b>preserve </b><b>`is_active`</b> — an operator-deactivated ticker must not be reactivated by a seed run.

### 2.1 Prices → `price_bar` (yfinance)

- Call: `yf.download(ticker, period="2y", interval="1d", auto_adjust=False, progress=False)`
- yfinance may return MultiIndex columns (e.g. `("Open", "NVDA")`) — flatten to plain column names before mapping
- Drop rows where `Close` is NaN (halts / bad data)
- Map each row: `bar_date` = index date; `open/high/low/close` → `Decimal(str(value)).quantize(Decimal("0.0001"))` (column is `Numeric(14,4)`); `volume` → `int`
- Upsert on PK (`security_id`, `bar_date`): ON CONFLICT DO UPDATE — re-running the same day must update, not duplicate
- Process one security at a time; log ticker + rows upserted; a single failing ticker must not abort the batch — collect the error and continue

### 2.2 VIX → `macro_daily` (yfinance)

- Same client and parameters as prices, with the fixed ticker `"^VIX"`
- Store one row per trading day with the VIX close value, using the exact field names found in `models.py` during Phase 0
- Same idempotent upsert semantics; runs inside the same job family as prices

### 2.3 Fundamentals → `fundamental` (SEC EDGAR)

Two steps per run:

- <b>Step A — resolve CIK once per run</b>: `GET https://www.sec.gov/files/company_tickers.json` → build an in-memory `{ticker: cik}` map; zero-pad CIK to 10 digits
- <b>Step B — fetch facts per ticker</b>: `GET https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
- Required header on every SEC request: `User-Agent` from settings `EDGAR_USER_AGENT`, default `"Forseti/0.1 (forseti-dev@example.com)"` (placeholder contact, not a real address) — SEC rejects requests with a default library user agent
- Rate limit: max 5 requests/second (SEC allows 10); sleep 0.2s between `companyfacts` calls
- Extract from `facts["us-gaap"]`, using <b>annual</b> values (form 10-K, `fp == "FY"`):
  - <b>revenue</b>: tag `Revenues`; fallbacks `RevenueFromContractWithCustomerExcludingAssessedTax`, then `SalesRevenueNet`
  - <b>revenue\_growth</b>: `(revenue_latest − revenue_previous) / revenue_previous` from the two most recent annual values; `None` if either is missing or previous is 0
  - <b>fcf</b>: `NetCashProvidedByUsedInOperatingActivities − PaymentsToAcquirePropertyPlantAndEquipment` (latest annual); `None` if either tag is missing
  - <b>debt\_to\_equity</b>: `(LongTermDebtNoncurrent + current-debt tags as available) / StockholdersEquity`; documented fallback `Liabilities / StockholdersEquity` when debt tags are missing; `None` if equity is missing or 0
  - <b>eps\_trend</b>: YoY change of `EarningsPerShareDiluted` (annual); `None` if missing
  - <b>margins</b>: `NetIncomeLoss / Revenues` (latest annual); `None` if missing
- `as_of_date` = the `end` date of the latest annual revenue fact used
- `raw_payload` = the full `companyfacts` JSON for the ticker, stored verbatim (JSONB) for auditability
- Missing tags produce `None` fields — <b>never fabricate or interpolate values</b>
- Upsert keyed by (`security_id`, `as_of_date`): `Fundamental` has a surrogate `id` PK, so the repository function selects an existing row by (`security_id`, `as_of_date`) → update in place if found, insert otherwise

### 2.4 Earnings → `earnings_event` (Alpha Vantage)

- `GET https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&horizon=3month&apikey={key}` — response is <b>CSV text, not JSON</b>
- API key from settings `ALPHA_VANTAGE_API_KEY`; if unset → log a warning, skip earnings ingestion, and exit that source <b>successfully</b> (other sources must still run)
- Parse with `csv.DictReader`; expected columns: `symbol`, `name`, `reportDate`, `fiscalDateEnding`, `estimate`, `currency`
- Keep only rows whose `symbol` is in the active universe; `report_date = date.fromisoformat(reportDate)`
- Set `confirmed = False` for all rows (the calendar is an estimate feed — document this in a code comment)
- Upsert on PK (`security_id`, `report_date`)

### CLI and orchestration

- New package `app/ingestion/` with one module per source: `universe.py`, `prices.py`, `vix.py`, `fundamentals.py`, `earnings.py`, plus `run.py` as the entry point
- Command: `python -m app.ingestion.run --source all|prices|vix|fundamentals|earnings [--ticker NVDA]`
- Makefile: add `make ingest` → `python -m app.ingestion.run --source all`
- `run.py` executes in fixed order: universe seed → prices → vix → fundamentals → earnings
- Per-source failures are isolated (caught and logged with context) and aggregated; the process exits non-zero if any source failed
- End-of-run structured log summary: rows upserted per source, failed tickers, duration

## 3. Architecture (aligned with the repo codestyle)

Thin CLI (`run.py`) → source modules (fetch + parse + map, with parsing kept pure and testable) → repository upserts (the only layer touching the DB). No scoring, no rules, no feature computation in this guide. The Clean Code + Object Calisthenics instructions in `.github/instructions/codestyle.instructions.md` (mirrored in Notion under <i>Coding Agent Clean Code Instructions</i>) are binding: small single-purpose functions, guard clauses over nested conditionals, no swallowed errors, domain primitives wrapped where meaningful, business mapping separated from HTTP/DB I/O.

## 4. Execution steps

### Phase 0 — Baseline

- [ ] `git checkout main && git pull` — create branch `feat/data-ingestion`
- [ ] Read `AGENTS.md` and `.github/instructions/codestyle.instructions.md`; they are binding
- [ ] Run `make test` — must be green before any change; if red, stop and report
- [ ] Read `app/db/models.py` and `app/db/repository.py`; note the exact `MacroDaily` fields and the existing `upsert_*` signatures before writing any code

### Phase 1 — Settings and dependencies

- [ ] `app/settings.py`: add `ALPHA_VANTAGE_API_KEY: str | None = None`; add `EDGAR_USER_AGENT: str = "Forseti/0.1 (forseti-dev@example.com)"`; add `INGEST_PRICE_PERIOD: str = "2y"`
- [ ] `requirements.txt`: add `yfinance` and `httpx`, pinned in the style already used in the file
- [ ] `make test` still green (no behavior change yet)

### Phase 2 — Universe seed (`app/ingestion/universe.py`)

- [ ] Encode the 35-row table from §2 as a frozen data structure (list of small dataclasses — follow repo style)
- [ ] `seed_universe(engine=None) -> int`: insert missing `Security` rows (`is_active=True`, `currency="USD"`); update `name`/`exchange`/`sector_tag` on drift; never reactivate a deactivated ticker; return count of inserted rows
- [ ] Log inserted vs skipped counts

### Phase 3 — Repository functions (`app/db/repository.py`)

- [ ] `list_active_securities(engine=None) -> list[Security]`
- [ ] Reuse or add `upsert_price_bars(bars, engine=None)` — ON CONFLICT (`security_id`, `bar_date`) DO UPDATE
- [ ] Reuse or add `upsert_macro_daily(rows, engine=None)` — same conflict semantics on its date key
- [ ] Reuse or add `upsert_fundamental(fundamental, engine=None)` — select-then-update-or-insert on (`security_id`, `as_of_date`), because `Fundamental` has a surrogate `id` PK
- [ ] Reuse or add `upsert_earnings_events(events, engine=None)` — ON CONFLICT (`security_id`, `report_date`) DO UPDATE
- [ ] Follow the exact existing style: `engine = engine or get_engine()`, `with get_session(engine) as session`

### Phase 4 — Prices (`app/ingestion/prices.py`)

- [ ] `fetch_price_history(ticker) -> DataFrame` — the only function that calls yfinance
- [ ] `to_price_bars(security_id, frame) -> list[PriceBar]` — pure function: flatten columns, drop NaN closes, quantize Decimals, cast volume
- [ ] `ingest_prices(engine=None)` — loop `list_active_securities()`, fetch, map, upsert; per-ticker try/except that logs ticker + exception and continues (never an empty `except`)

### Phase 5 — VIX (`app/ingestion/vix.py`)

- [ ] Same three-function shape as prices, single fixed symbol `"^VIX"`, writes `macro_daily` using the exact model fields from Phase 0

### Phase 6 — Fundamentals (`app/ingestion/fundamentals.py`)

- [ ] `EdgarClient` (httpx): constructor takes `user_agent`; methods `resolve_cik(tickers)` and `fetch_company_facts(cik)`; built-in 0.2s delay between fact requests; raises meaningful exceptions on HTTP errors (include status + CIK)
- [ ] `to_fundamental(security_id, payload) -> Fundamental` — pure mapping with the tag fallbacks from §2.3; `None` on any missing input; stores `raw_payload=payload`
- [ ] `ingest_fundamentals(engine=None)` — same per-ticker isolation as prices

### Phase 7 — Earnings (`app/ingestion/earnings.py`)

- [ ] `fetch_earnings_calendar_csv(api_key) -> str` (httpx GET); `parse_earnings_calendar(csv_text, active_tickers) -> list[EarningsEvent]` as a pure function; `ingest_earnings(engine=None)` that warns-and-skips when the key is unset

### Phase 8 — Entry point and Makefile

- [ ] `app/ingestion/run.py`: argparse with `--source` (choices `all/prices/vix/fundamentals/earnings`, default `all`) and optional `--ticker` filter; seed first, then sources in fixed order; aggregate per-source results; final log line with totals; `sys.exit(1)` if any source failed
- [ ] Makefile: `ingest` target

### Phase 9 — Tests (no network anywhere)

- [ ] `tests/test_db.py`: repository tests for each new upsert — including running each upsert twice and asserting row counts are unchanged (idempotency)
- [ ] `tests/ingestion/test_prices.py`: monkeypatch `yf.download` to return a constructed DataFrame (including a NaN-close row and MultiIndex columns); assert mapping and stored rows
- [ ] `tests/ingestion/test_fundamentals.py`: fixture file `tests/fixtures/edgar_companyfacts_sample.json` (trimmed real payload); assert every derived metric, the fallback-tag behavior, and None-on-missing-tag
- [ ] `tests/ingestion/test_earnings.py`: fixture CSV string; assert filtering to active tickers and `confirmed=False`
- [ ] `tests/ingestion/test_run.py`: earnings source succeeds-with-warning when `ALPHA_VANTAGE_API_KEY` is unset; a failing source does not prevent later sources from running; exit code is non-zero on source failure
- [ ] Follow existing conventions: plain functions in `test_db.py`, class-based grouping in test modules, arrange/act/assert, explicit expected values

### Phase 10 — Documentation and delivery

- [ ] `README.md`: new `## Data ingestion` section — sources table, `make ingest` usage, per-source CLI examples, required/optional env vars, idempotency note, and the run-time recommendation (US close is 22:00 Europe/Amsterdam in summer; run \~23:00)
- [ ] `make test` — 100% green including all pre-existing tests; match CI exactly (inspect `.github/workflows/` for lint/format steps)
- [ ] Manual smoke run against the local Docker stack: `make ingest`, then `curl -s http://127.0.0.1:8000/ticker/NVDA` and confirm price bars, fundamentals, and earnings are populated and freshness warnings are gone
- [ ] Codestyle self-check against the review checklist; commit in small logical commits following the existing message style (inspect `git log --oneline` first); push and open a PR titled e.g. `feat: add structured data ingestion`

## 5. Acceptance criteria (definition of done)

- [ ] `make ingest` populates `price_bar` (\~2 years of daily bars), `macro_daily`, `fundamental` (with `raw_payload`), and `earnings_event` for the full 35-ticker universe
- [ ] Re-running `make ingest` creates zero duplicate rows in any table
- [ ] One failing ticker or one failing source never aborts the rest; failures surface in logs and in the exit code
- [ ] All tests pass with no network access; EDGAR and Alpha Vantage clients are exercised only through fakes/fixtures
- [ ] Missing EDGAR tags produce null metric fields, never fabricated values; a missing Alpha Vantage key only disables earnings
- [ ] README documents sources, env vars, and usage; `GET /ticker/{symbol}` reflects ingested data with no stale/missing warnings for seeded tickers
- [ ] No changes to `POST /analyze` or `GET /ticker/{symbol}` behavior

## 6. Non-goals and guardrails

- No feature computation (RSI, SMA-50/200, volume trend) — that is the next guide
- No rules engine, scoring, or recommendation logic; no LLM usage anywhere in this guide
- No scheduling infrastructure (cron, Cloud Scheduler) — deployment cadence is Week 10; this guide delivers a manually runnable command only
- No new API endpoints; ingestion runs as a CLI, not over HTTP
- No paid data sources and no Finnhub implementation (documented fallback only)
- Do not refactor `analyzer.py`, the ticker endpoint, or existing tests (Boy Scout Rule applies only to lines you must touch)
- If any verified fact in §1 turns out to be stale (file moved, signature changed), stop and report the discrepancy instead of improvising

## 7. Why this shape (context for reviewers)

Ingestion is isolated from analysis so the rules engine (next guide) reads only Postgres and stays deterministic and unit-testable — the core design principle of the roadmap: deterministic code for calculations, LLMs only later for explanation. The chosen sources are all free-tier friendly for a 35-ticker universe: yfinance needs no key, EDGAR is the official source of record and its raw `companyfacts` JSON doubles as an audit trail via `raw_payload`, and Alpha Vantage's whole-market earnings calendar fits in one request per run. Idempotent upserts make every job safely re-runnable, which is what will allow a simple daily after-close cron in Week 10 without any code changes.

---

_Migrated from Notion on 2026-11-12._
