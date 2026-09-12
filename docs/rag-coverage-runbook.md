# RAG coverage runbook

## Stored coverage

Inspect persisted RAG coverage for one ticker:

```bash
make rag-coverage ticker=NVDA
```

The report groups stored chunks by source type and quality tier, with:

- chunk count
- distinct document count
- latest published timestamp
- latest ingested timestamp

Missing stored rows report `status=missing`.

## Live gap probe

Add a live availability probe when you need to see current source gaps:

```bash
make rag-coverage ticker=NVDA live=1
```

The live probe reports source-level statuses such as:

- `available`
- `missing`
- `extraction_failed`
- `provider_error`
- `unavailable`

`earnings_call_transcript_unavailable` means no configured legal transcript source exists. Treat it as neutral coverage information, not a bearish signal.

## Expected manual workflow

1. Run `make ingest-rag ticker=NVDA`.
2. Run `make rag-coverage ticker=NVDA`.
3. If coverage still looks incomplete, run `make rag-coverage ticker=NVDA live=1`.
4. Investigate any `provider_error` or `extraction_failed` status before trusting the evidence pack.
