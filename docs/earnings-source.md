# Earnings source

Alpha Vantage's `EARNINGS_CALENDAR` endpoint is premium. A free-tier API key
cannot read it.

Alpha Vantage may return HTTP 200 for a refusal. The JSON body indicates the
reason:

- `Error Message`: invalid API key or request.
- `Information`: unavailable or premium endpoint.
- `Note`: rate limit reached.

Forseti treats these bodies as source failures. Logs include
`earnings_ingestion_failed` rather than a successful zero-row ingestion. A
missing or placeholder API key reports `earnings_missing_api_key`, and the
earnings ingestion command exits with status 1.

A genuinely empty CSV calendar has its `symbol` header but matches no active
securities. This also logs `reason=no_rows_matched`. Refusal messages instead
include the Alpha Vantage reason.

When copying the key into `.env`, remove surrounding whitespace. In particular,
CRLF line endings can leave a trailing carriage return in a copied key; Forseti
strips it before making the request.
