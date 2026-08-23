#!/usr/bin/env bash
# Soak-tests POST /analyze across a set of tickers and reports non-200 responses.
set -uo pipefail

URL="${URL:-http://127.0.0.1:8000/analyze?include_trace=true}"
TICKERS="${TICKERS:-FSLR NVDA AMD PLTR MSFT GOOGL IBM RTX ENPH IONQ}"

fail=0
for ticker in $TICKERS; do
  body=$(cat <<JSON
{"ticker":"$ticker","account_size_eur":10000,"risk_percentage":1,"max_position_size_eur":2000,"as_of_date":"$(date +%Y-%m-%d)"}
JSON
)
  response=$(curl -s -w '\n%{http_code}' -X POST "$URL" -H 'Content-Type: application/json' -d "$body")
  code=$(printf '%s' "$response" | tail -1)
  payload=$(printf '%s' "$response" | sed '$d')
  if [ "$code" = "200" ]; then
    echo "OK   $ticker $(printf '%s' "$payload" | head -c 120)"
  else
    fail=$((fail + 1))
    echo "FAIL $ticker ($code) $payload"
  fi
done

echo "failures=$fail"
exit "$fail"
