# Demo script

Goal: show the private, agent-enforced cloud API end to end in about ten
minutes. Run it from a Codespace where `make gcloud-login` has been done.

1. **Proxy the private API and open Swagger.**
   `make cloud-proxy`, then open <http://127.0.0.1:8080/docs>. The proxy
   attaches your identity token; anonymous callers get `403`.
2. **Analyze three tickers** with `POST /analyze?include_trace=true`
   (`make cloud-analyze ticker=NVDA` does the same from the shell). Pick
   tickers that show each outcome: one `trade`, one `watchlist`, one
   `no_trade` (the defaults checked by `scripts/demo_check.py` are
   `NVDA`, `LMT` and `OKLO`; replace them with whatever the current data
   gives, the check only requires persistence and traces).
3. **Walk through each response:**
   - `decision` and `confidence`;
   - the deterministic `entry_range`, `stop_loss`, `take_profit` and
     `position_size_eur`: agents never change these (ADR 002 and ADR 008);
   - the cited `memo` and `reasons`, and every entry in `warnings`.
4. **Show the trace.** The `include_trace=true` response carries the agent
   steps. Copy `trace_id` and call `GET /runs/{run_id}` in Swagger to show the
   same run was persisted.
5. **Cloud Trace, then the dashboard.** In the console open **Trace** and
   search for the run's trace ID (see `docs/observability.md`), then open the
   **Forseti** Cloud Monitoring dashboard for latency, cost and downgrade rates.

## Automated version

`scripts/demo_check.py` runs steps 2 and 4 against the live service with an ID
token for the calling identity (`forseti-verifier` in CI):

```bash
python -m scripts.demo_check --url "$SERVICE_URL" [--tickers NVDA,LMT,OKLO]
```

For each ticker it requires HTTP 200, a known decision, a non-empty trace and
a `GET /runs/{run_id}` that returns the persisted run with steps. It is the
paid scorecard check `exit.demo`, run by `/verify full` on #121.
