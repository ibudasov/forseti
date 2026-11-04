"""Replays the docs/demo.md API steps against the private Cloud Run API as an ID-token caller."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_TICKERS = ("NVDA", "LMT", "OKLO")
DECISIONS = ("trade", "watchlist", "no_trade")
REQUEST_TIMEOUT_SECONDS = 300
TOKEN_TIMEOUT_SECONDS = 120

ApiCall = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]


class DemoCheckError(Exception):
    """A demo step failed; the message names the step and never includes response bodies."""


def identity_token(audience: str) -> str:
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
            check=True, capture_output=True, text=True, timeout=TOKEN_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DemoCheckError("gcloud could not create a service identity token") from error
    token = result.stdout.strip()
    if not token:
        raise DemoCheckError("gcloud returned an empty service identity token")
    return token


def authenticated_api_call(base_url: str, token: str) -> ApiCall:
    def call(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        request = Request(f"{base_url.rstrip('/')}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, None
        except (OSError, URLError, TimeoutError, ValueError) as error:
            raise DemoCheckError("Cloud Run API request failed") from error

    return call


def check_ticker(call: ApiCall, ticker: str) -> str:
    """Analyze one ticker, then confirm the run is persisted with a trace. Returns the decision."""
    status, analysis = call("POST", "/analyze?include_trace=true", {"ticker": ticker})
    if status != 200 or analysis is None:
        raise DemoCheckError(f"{ticker}: POST /analyze returned HTTP {status}")
    if analysis.get("decision") not in DECISIONS:
        raise DemoCheckError(f"{ticker}: /analyze returned an unknown decision")
    if not analysis.get("trace"):
        raise DemoCheckError(f"{ticker}: /analyze with include_trace=true returned no trace")
    run_id = analysis.get("trace_id")
    if not run_id:
        raise DemoCheckError(f"{ticker}: /analyze returned no run identifier")
    status, persisted = call("GET", f"/runs/{run_id}", None)
    if status != 200 or not persisted:
        raise DemoCheckError(f"{ticker}: GET /runs/{{run_id}} returned HTTP {status}, run not persisted")
    if not persisted.get("steps"):
        raise DemoCheckError(f"{ticker}: persisted run has no trace steps")
    return str(analysis["decision"])


def run_demo(call: ApiCall, tickers: tuple[str, ...]) -> dict[str, str]:
    return {ticker: check_ticker(call, ticker) for ticker in tickers}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Cloud Run service URL")
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    arguments = parser.parse_args(argv)
    tickers = tuple(ticker.strip() for ticker in arguments.tickers.split(",") if ticker.strip())
    try:
        decisions = run_demo(authenticated_api_call(arguments.url, identity_token(arguments.url)), tickers)
    except DemoCheckError as error:
        print(f"Demo check failed: {error}")
        return 1
    for ticker, decision in decisions.items():
        print(f"{ticker}: {decision}, run persisted with trace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
