Execution plan for a coding agent to implement `GET /ticker/{symbol}` in [ibudasov/forseti](https://github.com/ibudasov/forseti). This is the last open task of the <b>Weeks 1–2 — Python + service foundation</b> phase. Every step below is written to be executed mechanically, in order, without design decisions left to the agent.

> 🎯 Mission: add a read-only endpoint `GET /ticker/{symbol}` that returns the stored profile and data-availability snapshot for one ticker, reusing existing validation, repository, and testing conventions. No analysis logic, no writes, no new dependencies, no DB migration.

## 1. Verified current state of the repository

These facts were verified against `main` on 2026-08-06. Trust them; do not re-investigate unless a file has changed since.

| File | What exists today |
| --- | --- |
| `app/main.py` | FastAPI app; `GET /`, `GET /healthz`, `POST /analyze`; dependency `get_analysis_engine()` returns `get_engine()` |
| `app/schemas/analyze.py` | `AnalyzeRequest` (validates ticker via `validate_and_normalize_ticker`), `AnalyzeResponse` (floats at the API boundary) |
| `app/services/analyzer.py` | `validate_and_normalize_ticker(ticker: str) -> str` raising `ValueError` on bad input; `ENGINE_VERSION = "v1.placeholder.0"`; placeholder engine |
| `app/db/models.py` | SQLModel tables: `Security`, `PriceBar`, `Fundamental`, `EarningsEvent`, `MacroDaily`, `TechnicalFeature`, `Recommendation` |
| `app/db/repository.py` | `get_security(ticker, engine=None)`, `get_latest_bars(ticker, n, engine=None, as_of_date=None)` (desc order), `upsert_*`, `save_recommendation` |
| `app/db/session.py` | `get_engine()`, `get_session(engine=None)` |
| `app/settings.py` | pydantic-settings, `DATABASE_URL`, cached `get_settings()` |
| `tests/test_db.py` | `db_engine` fixture (testcontainers Postgres or `TEST_DATABASE_URL`); repository tests |
| `tests/test_integration.py` | `TestClient`; `db_client` fixture overriding `get_analysis_engine` via `app.dependency_overrides`; class-based test grouping |
| `Makefile` | `make test` (pytest in container), `make migrate` |
| `.github/instructions/codestyle.instructions.md` | Clean Code + Object Calisthenics rules — <b>binding for all code produced here</b> |
| `.github/workflows/` | CI runs pytest on Python 3.x — all tests must pass before finishing |

Relevant model details (exact, from `app/db/models.py`):

- `Security`: `id`, `ticker` (unique), `name`, `exchange`, `sector_tag` (`ai|defence|nuclear|green_energy|quantum|robotics|space`), `currency`, `is_active`, `created_at`
- `PriceBar`: PK (`security_id`, `bar_date`); `open/high/low/close` `Numeric(14,4)`; `volume` BigInteger
- `TechnicalFeature`: PK (`security_id`, `as_of_date`); nullable `rsi_14`, `sma_50`, `sma_200`, `volume_trend`
- `Fundamental`: `id` PK; `security_id`, `as_of_date`; nullable `revenue_growth`, `fcf`, `debt_to_equity`, `eps_trend`, `margins`; `raw_payload` JSONB
- `EarningsEvent`: PK (`security_id`, `report_date`); `confirmed` bool

## 2. Endpoint contract (exact specification)

### Request

- `GET /ticker/{symbol}` — path parameter `symbol: str`, no query parameters, no body.
- `symbol` is validated and normalized with the <b>existing</b> `validate_and_normalize_ticker` (strip, uppercase; rejects empty, URL-like, >10 chars, >5-alpha-chars, non `[A-Z0-9.-]`). Do not modify that function.

### Response 200 — `TickerProfileResponse`

```json
{
  "ticker": "NVDA",
  "name": "NVIDIA Corporation",
  "exchange": "NASDAQ",
  "sector_tag": "ai",
  "currency": "USD",
  "is_active": true,
  "latest_price_bar": {
    "bar_date": "2026-01-02",
    "open": 101.0,
    "high": 104.0,
    "low": 100.0,
    "close": 102.5,
    "volume": 1100000
  },
  "price_bars_stored": 2,
  "data_freshness": {
    "latest_price_bar_date": "2026-01-02",
    "price_data_age_days": 1,
    "stale_threshold_days": 7,
    "is_price_data_stale": false
  },
  "latest_technical_features": {
    "as_of_date": "2026-01-02",
    "rsi_14": 58.1234,
    "sma_50": 99.5,
    "sma_200": 88.0,
    "volume_trend": 1.05
  },
  "latest_fundamentals": {
    "as_of_date": "2025-12-31",
    "revenue_growth": 0.62,
    "fcf": 21000000000.0,
    "debt_to_equity": 0.41,
    "eps_trend": 0.18,
    "margins": 0.55
  },
  "next_earnings_date": "2026-02-25",
  "warnings": []
}
```

Field rules:

- All `Decimal` values are converted to `float` at the schema boundary (same convention as `AnalyzeResponse`). Dates serialize as ISO `YYYY-MM-DD`.
- `latest_price_bar`, `latest_technical_features`, `latest_fundamentals` are `null` when no rows exist; `next_earnings_date` is `null` when no future earnings event exists; `price_bars_stored` is `0` when none.
- When there is no price bar at all: `data_freshness.latest_price_bar_date = null`, `price_data_age_days = null`, `is_price_data_stale = true`.
- `warnings` is a list of machine-readable snake\_case strings, consistent with existing warnings (`insufficient_price_data`, `market_data_unavailable`). Exact warning vocabulary for this endpoint:
  - `no_price_data` — zero price bars stored
  - `stale_price_data` — latest bar older than `stale_threshold_days`
  - `no_technical_features` — no `technical_feature` row
  - `no_fundamentals` — no `fundamental` row
  - `no_earnings_data` — no earnings events at all (past or future)
  - `security_inactive` — `Security.is_active` is `false`

### Error responses

| Case | Status | Body |
| --- | --- | --- |
| Symbol fails validation | 422 | `{"detail": "<message from validate_and_normalize_ticker>"}` |
| Valid symbol, not in `security` table | 404 | `{"detail": "ticker_not_found: NVDA"}` |

> 📐 Explicit decisions (do not reconsider): 422 is raised via `HTTPException(status_code=422, ...)` — the simple `{"detail": ...}` shape is accepted even though it differs from FastAPI's native validation array. The endpoint is strictly read-only: no DB writes, no external calls, no `trace_id`/`engine_version` in the response. Staleness threshold is a module-level constant `STALE_PRICE_DATA_THRESHOLD_DAYS = 7`, not a setting (no speculative configuration).

## 3. Architecture (aligned with the repo codestyle instructions)

Thin route → service (pure assembly) → repository (I/O). Business rules (staleness, warnings) live in the service and are unit-testable without HTTP.

1. `app/db/repository.py` — add four query functions (I/O only)
2. `app/schemas/ticker.py` — new file, response models only
3. `app/services/ticker_profile.py` — new file, assembly + warning rules
4. `app/main.py` — add validation dependency + route (parsing, status mapping only)
5. Tests, then README, then verification

## 4. Execution steps

### Phase 0 — Baseline

- [ ] `git checkout main && git pull` — create branch `feat/get-ticker-endpoint`
- [ ] Read `AGENTS.md` and `.github/instructions/codestyle.instructions.md`; they are binding
- [ ] Run `make test` (or `python3 -m pytest` in the venv from `./setup.sh`) — must be green before any change; if red, stop and report

### Phase 1 — Repository queries (`app/db/repository.py`)

Append these functions. Follow the existing style exactly: `engine = engine or get_engine()`, `with get_session(engine) as session`, `select` from sqlmodel, `_normalize_ticker` for ticker inputs.

- [ ] `count_price_bars(ticker: str, engine=None) -> int` — `select(func.count()).select_from(PriceBar).join(Security).where(Security.ticker == ...)`; import `func` from `sqlmodel` (or `sqlalchemy`); return `session.exec(stmt).one()`
- [ ] `get_latest_technical_feature(ticker: str, engine=None) -> Optional[TechnicalFeature]` — join `Security`, order `TechnicalFeature.as_of_date.desc()`, `limit 1`, `.first()`
- [ ] `get_latest_fundamental(ticker: str, engine=None) -> Optional[Fundamental]` — join `Security`, order `Fundamental.as_of_date.desc()`, `limit 1`, `.first()`
- [ ] `get_next_earnings_event(ticker: str, on_or_after: date, engine=None) -> Optional[EarningsEvent]` — join `Security`, where `EarningsEvent.report_date >= on_or_after`, order `report_date.asc()`, `limit 1`, `.first()`
- [ ] Do <b>not</b> add a `get_latest_price_bar` wrapper — reuse existing `get_latest_bars(ticker, 1, engine=engine)` in the service
- [ ] Update the import block from `app.db.models` to include `Fundamental`, `TechnicalFeature`; `date` is already imported

### Phase 2 — Response schemas (`app/schemas/ticker.py`, new file)

- [ ] Create the file with `from __future__ import annotations`, pydantic `BaseModel`s, `Optional`, `date`. Exact models:

```py
class PriceBarSnapshot(BaseModel):
    bar_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int

class TechnicalFeaturesSnapshot(BaseModel):
    as_of_date: date
    rsi_14: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    volume_trend: Optional[float] = None

class FundamentalsSnapshot(BaseModel):
    as_of_date: date
    revenue_growth: Optional[float] = None
    fcf: Optional[float] = None
    debt_to_equity: Optional[float] = None
    eps_trend: Optional[float] = None
    margins: Optional[float] = None

class DataFreshness(BaseModel):
    latest_price_bar_date: Optional[date] = None
    price_data_age_days: Optional[int] = None
    stale_threshold_days: int
    is_price_data_stale: bool

class TickerProfileResponse(BaseModel):
    ticker: str
    name: str
    exchange: str
    sector_tag: str
    currency: str
    is_active: bool
    latest_price_bar: Optional[PriceBarSnapshot] = None
    price_bars_stored: int
    data_freshness: DataFreshness
    latest_technical_features: Optional[TechnicalFeaturesSnapshot] = None
    latest_fundamentals: Optional[FundamentalsSnapshot] = None
    next_earnings_date: Optional[date] = None
    warnings: list[str]
```

- [ ] No validators needed here — validation happens at the route boundary via the shared dependency

### Phase 3 — Service (`app/services/ticker_profile.py`, new file)

- [ ] Module constant: `STALE_PRICE_DATA_THRESHOLD_DAYS = 7`
- [ ] Public function signature (exact):

```py
def build_ticker_profile(
    symbol: str,
    engine: Optional["Engine"] = None,
    today: Optional[date] = None,
) -> Optional["TickerProfileResponse"]:
```

- Returns `None` when `get_security` finds no row (route maps this to 404). `today` defaults to `datetime.now(timezone.utc).date()` and is injectable for deterministic tests.
- `symbol` arrives already normalized from the route; do not re-validate.

- [ ] Assembly order: `get_security` → `get_latest_bars(symbol, 1)` → `count_price_bars` → `get_latest_technical_feature` → `get_latest_fundamental` → `get_next_earnings_event(symbol, on_or_after=today)` → build warnings → build response
- [ ] Warning rules, in this evaluation order, each a small predicate or guard (one level of indentation, no `else` chains — per codestyle):
  1. `not security.is_active` → `security_inactive`
  2. `price_bars_stored == 0` → `no_price_data` (skip staleness check in this case)
  3. `latest bar older than threshold` → `stale_price_data`
  4. no technical feature row → `no_technical_features`
  5. no fundamental row → `no_fundamentals`
  6. no earnings event rows at all (past or future) → `no_earnings_data` — implement by checking the latest earnings event regardless of date: add repository call `get_next_earnings_event(symbol, on_or_after=date.min)` reuse or a direct check; simplest correct approach: if `next_earnings is None`, query with `on_or_after=date.min` to distinguish “no future” from “none at all”
- [ ] Staleness: `age = (today - latest_bar.bar_date).days`; stale iff `age > STALE_PRICE_DATA_THRESHOLD_DAYS`
- [ ] Private mapping helpers, each doing one thing, following the naming style already used in `analyzer.py` (`_to_float`, `_quantize` etc. live there — write small local equivalents here, do not import privates across modules):
  - `_to_price_bar_snapshot(bar: PriceBar) -> PriceBarSnapshot`
  - `_to_technical_features_snapshot(feature: TechnicalFeature) -> TechnicalFeaturesSnapshot`
  - `_to_fundamentals_snapshot(fundamental: Fundamental) -> FundamentalsSnapshot`
  - `_to_data_freshness(latest_bar: Optional[PriceBar], today: date) -> DataFreshness`
  - `_to_float(value: Optional[Decimal]) -> Optional[float]`
- [ ] Add `logger = logging.getLogger(__name__)` and one `logger.info("ticker_profile_served", extra={"ticker": symbol, ...})` on the happy path; log nothing on 404 (the route handles it)

### Phase 4 — Route (`app/main.py`)

- [ ] Add imports: `HTTPException`, `Path` from fastapi; `build_ticker_profile` from the new service; `TickerProfileResponse` from the new schema; `validate_and_normalize_ticker` from `app.services.analyzer`
- [ ] Add the validation dependency next to `get_analysis_engine`:

```py
def validated_symbol(symbol: str = Path(...)) -> str:
    try:
        return validate_and_normalize_ticker(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

- [ ] Add the route (thin controller — parse, delegate, map):

```py
@app.get("/ticker/{symbol}", response_model=TickerProfileResponse)
def read_ticker_profile(symbol: str = Depends(validated_symbol), engine=Depends(get_analysis_engine)):
    profile = build_ticker_profile(symbol, engine=engine)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"ticker_not_found: {symbol}")
    return profile
```

- [ ] Reusing `get_analysis_engine` as the engine dependency is deliberate: the existing `db_client` test fixture overrides it, so the new endpoint is covered by the same test harness with zero fixture changes

### Phase 5 — Tests

All new tests must follow existing conventions: class-based grouping in `tests/test_integration.py` using the `db_client`/`db_engine` fixtures; plain functions in `tests/test_db.py`. Seed data via `Session(db_engine)` exactly like `TestAnalyzeEndpoint` does.

- [ ] `tests/test_db.py` — repository tests:
  - [ ] `test_count_price_bars` — seed security + 2 bars → `2`; unknown ticker → `0`
  - [ ] `test_get_latest_technical_feature_returns_latest` — two rows, assert the later `as_of_date` wins
  - [ ] `test_get_latest_fundamental_returns_latest` — same pattern
  - [ ] `test_get_next_earnings_event_skips_past` — one past + one future event relative to `on_or_after` → returns the future one; only past → `None`
- [ ] `tests/test_integration.py` — new class `TestTickerEndpoint`:
  - [ ] `test_get_ticker_happy_path_full_data` — seed security (NVDA), 2 bars, technical feature, fundamental, future earnings → 200; assert every contract field, `warnings == []`, floats (e.g. `body["latest_price_bar"]["close"] == 102.5`), `price_bars_stored == 2`
  - [ ] `test_get_ticker_normalizes_symbol` — request `/ticker/%20nvda%20` (or `" nvda "` encoded) → 200 and `body["ticker"] == "NVDA"`
  - [ ] `test_get_ticker_without_market_data` — security only → 200; `latest_price_bar is None`, `price_bars_stored == 0`, `is_price_data_stale is True`, warnings contain `no_price_data`, `no_technical_features`, `no_fundamentals`, `no_earnings_data`
  - [ ] `test_get_ticker_with_stale_price_data` — bar dated 30 days before today → warning `stale_price_data`, `price_data_age_days == 30`
  - [ ] `test_get_ticker_unknown_returns_404` — valid but unseeded symbol → 404, `detail == "ticker_not_found: MSFT"`
  - [ ] `test_get_ticker_invalid_symbol_returns_422` — parametrized: `""` (use `/ticker/%20` or call the dependency directly if routing rejects empty), URL-like `https%3A%2F%2Fbroker.example%2FNVDA`, `TOOLONGTICKER` (>10), `NVIDIA CORPORATION` (>5 alpha + space), `NV$DA` — each → 422
  - [ ] `test_get_ticker_inactive_security_warns` — `is_active=False` → 200 with `security_inactive` warning and `is_active == false`
  - [ ] `test_get_ticker_response_contains_no_analysis_fields` — assert `engine_version`, `trace_id`, `decision` are <b>absent</b> (read-only contract)

### Phase 6 — Documentation

- [ ] `README.md` — add a `## Ticker endpoint` section after the analyze section: method/path, one `curl` example, one abridged 200 JSON example, the 404/422 behavior, and the warning vocabulary list
- [ ] No changes to `docs/database.md` (no schema change); no Alembic migration — if you find yourself writing one, stop: the design is wrong

### Phase 7 — Verification and delivery

- [ ] `python3 -m pytest` (or `make test`) — 100% green, including all pre-existing tests
- [ ] Run the linter/formatter configured in `.github/workflows/` if any (inspect `python-app.yml` and `test.yml`; match CI exactly)
- [ ] Manual smoke check against the running app (`docker-compose up` or the `setup.sh` venv):

```bash
curl -s http://127.0.0.1:8000/ticker/NVDA | python3 -m json.tool
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/ticker/UNKNOWNX   # expect 404
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8000/ticker/https%3A%2F%2Fx"  # expect 422
```

- [ ] Confirm `http://127.0.0.1:8000/docs` renders the endpoint with the response model
- [ ] Codestyle self-check against `.github/instructions/codestyle.instructions.md` review checklist (small functions, guard clauses, meaningful names, no swallowed errors, route contains no business logic)
- [ ] Commit in small, logical commits following the repo's existing message style (inspect `git log --oneline` first); push branch and open a PR titled e.g. `feat: add GET /ticker/{symbol} endpoint`

## 5. Acceptance criteria (definition of done)

- [ ] `GET /ticker/{symbol}` returns the exact contract in §2 for a fully seeded ticker
- [ ] Unknown valid symbol → 404 with `ticker_not_found: <TICKER>`; invalid symbol → 422
- [ ] Warning vocabulary matches §2 exactly; absent data → `null` fields, never fabricated values
- [ ] Endpoint performs zero writes (assertable by code review: no `session.add/commit` on the request path)
- [ ] All new repository, service, and route behavior covered by the tests in Phase 5; entire suite green
- [ ] README documents the endpoint; Swagger UI shows it
- [ ] No changes to `POST /analyze` behavior, no new dependencies in `requirements.txt`, no migrations

## 6. Non-goals and guardrails

- No analysis or recommendation logic in this endpoint (that is `POST /analyze` and the Weeks 3–4 rules engine)
- No external market-data calls; this endpoint reads only what is already stored
- Do not refactor `analyzer.py`, `repository.py` internals, or existing tests (Boy Scout Rule applies only to lines you must touch)
- Do not rename the endpoint or change the response field names — the contract above is frozen for this task
- If any verified fact in §1 turns out to be stale (file moved, signature changed), stop and report the discrepancy instead of improvising

## 7. Why this shape (context for reviewers)

This endpoint is the read side of the service foundation: it exposes universe membership (`security`), market-data coverage (`price_bar`), and computed-feature availability (`technical_feature`, `fundamental`, `earnings_event`) per ticker. The Weeks 3–4 rules engine needs exactly this freshness/completeness signal ("data freshness and completeness check" in the roadmap), so the `data_freshness` block and warning vocabulary are designed to be reused by the rules engine later rather than re-derived.

---

_Migrated from Notion on 2026-11-12._
