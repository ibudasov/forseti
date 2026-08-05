# Forseti FastAPI Project

A minimal FastAPI application scaffold with `pedantic` request validation.

## Quick start

1. Create a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
uvicorn app.main:app --reload
```

4. Open http://127.0.0.1:8000 in your browser.

## Run PostgreSQL with Docker Compose

You can run a development Postgres instance using Docker Compose. The compose file reads variables from `.env` by default, so you can set `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` there (or rely on the defaults).

Start the database:

```bash
docker compose up -d db
```

Check the logs and health:

```bash
docker compose ps
docker compose logs -f db
```

Stop and remove the database (including the volume):

```bash
docker compose down -v
```

Once the DB is running, update `.env`/`DATABASE_URL` if needed and start the app locally (see Quick start).

## Example request

POST `/items/` with JSON:

```json
{
  "name": "widget",
  "price": 9.99,
  "quantity": 3
}
```
