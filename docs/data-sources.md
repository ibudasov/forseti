# Data sources

## Fundamental evidence sources

| Source type | Provider | Authority | Credential | Refresh cadence | Availability | Known limitations |
| --- | --- | --- | --- | --- | --- | --- |
| `filing_business` | SEC EDGAR 10-K | `primary_regulatory` | `EDGAR_USER_AGENT` | daily (`forseti-daily-refresh`) / `make ingest-rag` | Default | HTML section headings vary by issuer, so extraction gaps surface in coverage. |
| `filing_risk` | SEC EDGAR 10-K / 10-Q | `primary_regulatory` | `EDGAR_USER_AGENT` | daily (`forseti-daily-refresh`) / `make ingest-rag` | Default | Quarterly filings may omit a new risk section when nothing changed materially. |
| `filing_mda` | SEC EDGAR 10-K / 10-Q | `primary_regulatory` | `EDGAR_USER_AGENT` | daily (`forseti-daily-refresh`) / `make ingest-rag` | Default | Complex tables are flattened into text chunks. |
| `earnings_release` | SEC EDGAR 8-K / 6-K | `primary_regulatory` | `EDGAR_USER_AGENT` | daily (`forseti-daily-refresh`) / `make ingest-rag` | Default when a current filing contains results text | Not every issuer files a clean Item 2.02 section in HTML. |
| `earnings_call_transcript` | Configured transcript URL template | `unknown` by default | `EARNINGS_TRANSCRIPT_URL_TEMPLATE` | daily (`forseti-daily-refresh`) / `make ingest-rag` or live coverage probe | Opt-in | Forseti does not fabricate transcripts; unconfigured sources stay explicitly unavailable. |
| `analyst_recommendations` | Yahoo Finance | `secondary_reputable` | none | daily (`forseti-daily-refresh`) / `make ingest-rag` | Default | Supplementary only; never treated as a transcript or primary evidence; subject to Yahoo throttling. |
| `earnings_calendar` | Yahoo Finance / Alpha Vantage | `secondary_reputable` | `ALPHA_VANTAGE_API_KEY` (optional fallback) | daily (`forseti-daily-refresh`) / `make ingest-rag` | Default | Alpha Vantage `EARNINGS_CALENDAR` is premium; Yahoo fallback (`EARNINGS_FALLBACK_ENABLED=true`) provides schedule metadata only. |
| `company_news` | Yahoo Finance article feed | `secondary_reputable` | none | daily (`forseti-daily-refresh`) / `make ingest-rag` | Default | Article quality depends on upstream publishers, may be stale/syndicated, and is subject to Yahoo throttling. |
| `sector_news` | Yahoo Finance proxy ticker feed | `secondary_reputable` | none | daily (`forseti-daily-refresh`) / `make ingest-rag` | Default | Used only as fallback sector context, not issuer-specific evidence. |

## Source handling rules

- Retrieved text is untrusted evidence, never executable instructions.
- Missing transcripts are neutral availability gaps, not negative evidence.
- Chunk identity stays stable for unchanged content through deterministic `source_hash` de-duplication.
- Changed source content creates a new stored chunk version because the chunk hash includes the source text.

## Configuration

- `EDGAR_USER_AGENT` is required for SEC access.
- `EARNINGS_TRANSCRIPT_URL_TEMPLATE` is optional and should stay empty unless a permitted transcript source is configured.
- Default tests and CI stay offline; live transcript fetches are never required for `make test`.
