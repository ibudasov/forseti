# Trading Analyst

Reference ticker API for the Forseti swing-trade analyst foundation.

## Endpoints

- `GET /health`
- `GET /ticker/{symbol}`

## Local setup

```bash
pip install -e ".[dev]"
alembic upgrade head
uvicorn trading_analyst.main:app --reload
```

Integration tests use in-memory SQLite as a v1 convenience. CI should later exercise the same flows against Postgres.
