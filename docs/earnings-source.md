# Earnings source behavior (Alpha Vantage)

`function=EARNINGS_CALENDAR` is a **premium** Alpha Vantage endpoint.

## HTTP-200 refusal payloads

Alpha Vantage can refuse requests with HTTP 200 and JSON payloads:

- `{"Information": "...premium endpoint..."}`: key is valid, but endpoint requires premium access.
- `{"Note": "...rate limit..."}`: key is accepted, but daily/request quota is exhausted.
- `{"Error Message": "..."}`: invalid request or invalid key.

## How to read logs

- Refusal or malformed payloads now raise source failure and return `earnings_source`.
- Missing/placeholder key now returns `earnings_missing_api_key` and exits non-zero.
- A payload that parses but matches zero active tickers is treated as failure (`reason=no_rows_matched`).

This is different from a genuinely empty calendar; empty-or-invalid source payloads are now explicit failures.

## `.env` CRLF key trap

If an API key is copied from a CRLF file, trailing `\r` can be included in the value.
Ingestion now trims surrounding whitespace (including CR/LF) before sending the key.

## Exit code behavior

When the key is missing/refused or payload is unusable, earnings ingestion returns a non-empty failure list, so:

- `python -m app.ingestion.run --source earnings` exits `1`.
