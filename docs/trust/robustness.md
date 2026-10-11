# Trust 5/6 robustness review

- Issue: #97
- Branch: `copilot/ibudasov-92-parent-session`
- Offline suite: `tests/trust/test_failure_injection.py`
- Live runner: `make trust-consistency CONFIRM_COST=yes [REPEAT=5]`

## What was completed offline

The new failure-injection suite covers:

- Fundamental Analyst fallback safety for timeout, malformed JSON, schema validation failure, and forbidden-field payloads
- Technical Analyst exception handling and empty-output safety
- Decision Synthesizer exception handling and risk-number tampering rejection
- Critic fallback safety for provider failure and malformed output
- Retriever-unavailable and zero-chunk paths
- Agentic-mode structured-data safety for stale prices, missing price data, and missing fundamentals

Each case asserts the final response keeps deterministic risk numbers, never upgrades the deterministic decision, and records the degradation via warnings, reason codes, or trace steps.

## Offline results

| Check | Result | Notes |
| --- | --- | --- |
| `python -m pytest tests/trust/test_failure_injection.py` | Passed | 15 tests passed offline in Docker Compose. |
| Fundamental fallback safety | Passed | Invalid/failed specialist output stayed neutral with adjustment `0`. |
| Decision/critic downgrade-only safety | Passed | Failures preserved or downgraded the deterministic answer only. |
| Data-gate trade blocking | Passed | Stale prices, missing price data, and missing fundamentals never yielded `trade` in agentic mode. |

## Live consistency runner status

The repeat-run consistency tooling was extended in `scripts/trust_e2e.py` and exposed as `make trust-consistency`. The runner now:

- accepts `--repeat N`
- groups runs by ticker
- reports decision counts, confidence mean/std-dev, and observed fundamental adjustments
- compares completed runs against the repeatability thresholds from `docs/trust/criteria.md`
- writes `trust-consistency-*.json` reports under `reports/trust/`

## Environment blocker

An attempted live run is still blocked in this sandbox because Vertex/Gemini credentials are unavailable. The existing environment probe remains consistent with Trust 2/6:

- `~/.config/gcloud/application_default_credentials.json` is not a readable ADC JSON file in this environment
- live agentic runs fail with `GoogleAuthError`
- after `make migrate`, `make trust-consistency CONFIRM_COST=yes REPEAT=5` wrote `reports/trust/trust-consistency-off-20261011T193023Z.json` and marked all 35 runs as `blocked`

Because of that blocker, this issue does **not** claim real variance numbers yet, and no repeatability threshold outcome is asserted from this environment.

## Remaining work once credentials are available

1. Run `make trust-consistency CONFIRM_COST=yes REPEAT=5`.
2. Inspect the newest `reports/trust/trust-consistency-*.json`.
3. Record the actual flip-rate, confidence-variance, and fundamental-adjustment spread results.
4. If any threshold fails, open a follow-up issue and link it here before closing #97.
