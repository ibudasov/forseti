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

## Example request

POST `/items/` with JSON:

```json
{
  "name": "widget",
  "price": 9.99,
  "quantity": 3
}
```
