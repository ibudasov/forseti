# Vertex AI Cloud Validation Runbook

Validate the RAG pipeline (embeddings + Gemini synthesis) against live GCP
Vertex AI, before starting the ADK agentic workflow stage.

## 1. Prerequisites

These steps are manual — run them yourself, not via the agent/CI.

```bash
gcloud services enable aiplatform.googleapis.com --project=<PROJECT_ID>
gcloud auth application-default login
```

- Billing must be enabled on the GCP project (Vertex AI returns
  `PERMISSION_DENIED` otherwise).
- The authenticated principal needs role `roles/aiplatform.user` on the
  project.
- Region: the default `us-central1` is correct — both `text-embedding-004`
  and `gemini-2.0-flash-001` are available there.
- `gcloud auth application-default login` writes Application Default
  Credentials (ADC) to `${HOME}/.config/gcloud/application_default_credentials.json`.
  `docker-compose.yml` mounts this exact path read-only into the `app`
  container. Run this command **before** `docker compose up` — if the file is
  missing, Docker creates an empty directory at the mount point and auth
  fails with a confusing error.

## 2. Configure

Copy the example environment file:

```bash
cp .env.example .env
```

Set the following in `.env`:

- `VERTEX_AI_PROJECT` — your GCP project ID.
- `GOOGLE_CLOUD_PROJECT` — same project ID (used by the later ADK agentic
  workflow stage).

## 3. Run ingestion

```bash
docker compose up --build
docker compose exec app python -m app.rag.cli --ticker NVDA --sector ai
```

Expected log signals:

- **No** `VERTEX_AI_PROJECT not set — using MockEmbeddingClient` line.
- **No** `Embedding failed ... retrying` warnings.

With `RAG_FAIL_LOUD=true`, an auth/permission problem must instead abort the
CLI with a non-zero exit and the original exception, instead of silently
falling back.

## 4. Verify embeddings

Connect to Postgres and confirm non-zero vectors were stored:

```sql
SELECT ticker, COUNT(*) FROM document_chunks GROUP BY ticker;
SELECT id, left(embedding::text, 80) FROM document_chunks LIMIT 3;
```

All-zero vectors mean the mock client or the zero-vector fallback ran —
treat this as a failure.

## 5. Verify synthesis

```bash
curl http://localhost:8000/ticker/NVDA/evidence
```

Expected: `status: "ok"` with non-empty `bullish_drivers` / `bearish_risks`
whose items carry `chunk_ids`.

`status: "insufficient_data"` is a failure to investigate — check the
container logs.

## 6. Troubleshooting

| Symptom | Cause |
| --- | --- |
| `PERMISSION_DENIED` | Vertex AI API not enabled, billing off, or missing `roles/aiplatform.user` |
| `DefaultCredentialsError` | ADC missing; run `gcloud auth application-default login` and re-create the container so the mount resolves |
| `404` / model not found | Wrong `VERTEX_AI_LOCATION` or model name typo |
| `429` | Quota exceeded; the retry/back-off in `embedding.py` handles transient cases — re-run if it persists |
| Embedding dimension mismatch on insert | `EMBEDDING_DIM` (768) must match both the model output and the `document_chunks.embedding` column |
