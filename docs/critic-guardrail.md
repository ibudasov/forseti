# Critic guardrail

The Critic is the last agentic step. It may downgrade confidence, downgrade the
final decision, or request one bounded rewrite of the memo, but it may never
invent risk numbers or upgrade the deterministic baseline.

## What the Critic checks

The deterministic layer in `app/services/critic_rules.py` emits six objection
families before the model verdict is considered:

| Rule | Trigger | Category | Default effect |
| --- | --- | --- | --- |
| `risk_number_mismatch` | Draft money field differs from the deterministic baseline | `risk_number_mismatch` | Restore baseline decision and confidence |
| `decision_upgrade` | Draft outranks the deterministic baseline | `internal_inconsistency` | Restore baseline decision and confidence |
| `stale_or_incomplete_data` | Hard-blocker warning such as `stale_price_data` | `stale_or_incomplete_data` | Cap at `watchlist` |
| `analyst_contradiction` | Fundamental analyst is negative on a trade, or analysts point in opposite directions | `analyst_contradiction` | Cap at `watchlist` and apply the contradiction penalty |
| `missing_trade_field` | Trade draft has a missing risk field | `internal_inconsistency` | Force `no_trade` |
| `unsupported_claim` | Medium/high material finding has no metric or chunk citation | `unsupported_claim` | Confidence penalty only |

## Precedence

`app/services/critic_policy.py` applies deterministic actions in a fixed order.
The first matching action wins:

| Priority | Condition | Action |
| --- | --- | --- |
| 1 | `risk_number_mismatch` | Restore deterministic baseline |
| 2 | `decision_upgrade` | Restore deterministic baseline |
| 3 | `missing_trade_field` | Force `no_trade` |
| 4 | `stale_or_incomplete_data` | Cap at `watchlist` |
| 5 | `analyst_contradiction` | Cap at `watchlist` + contradiction penalty |
| 6 | Accepted model verdict when mode is not `off` | Apply downgrade-only reject/revise logic |

Medium-severity objections add `0.05` confidence penalty each, clamped by the
request's `max_confidence_penalty`. The model can propose an additional penalty,
but it can never set a target confidence.

## Authority limits

- downgrade-only: `trade -> watchlist -> no_trade`
- deterministic rules fail closed and outrank the model
- the model fails open: invalid/failed critique output records
  `assessment_invalid` and preserves the deterministic answer
- money fields (`entry_range`, `stop_loss`, `take_profit`, `risk_reward`,
  `position_size_eur`) are never rewritten by the Critic
- at most one bounded revision is allowed (`MAX_CRITIC_REVISIONS = 1`)

## Modes

| Mode | Workflow behavior |
| --- | --- |
| `off` | The workflow skips the Critic entirely and returns the current response unchanged. |
| `shadow` | The workflow runs the Critic, persists `critic_effect`, and records the trace step, but does not mutate the API response. |
| `enforced` | The workflow runs the Critic and applies allowed downgrades or one bounded revision. |

The application default remains `CRITIC_MODE=off`.

## Revision loop bound

A `revise` verdict sets `revisions_requested=1`. The workflow can call the
Decision Synthesizer one extra time, never more. If the rewrite fails, the run
keeps the first draft and adds the warning `critic_revision_failed`.

## Reason codes

The persisted `critic_effect.reason_codes` field may contain these policy codes:

- `guardrail_rejected: risk_number_mismatch`
- `guardrail_rejected: decision_upgrade_attempt`
- `guardrail_forced_no_trade`
- `guardrail_capped_watchlist`
- `guardrail_analyst_contradiction`
- `guardrail_confidence_penalty`
- `assessment_invalid`
- `critic_requested_revision`
- `critic_reject_forced_no_trade`
- `critic_reject_applied`
- `critic_reject_invalid_proposed_decision`

Validation failures from the model layer are preserved alongside those codes,
for example `provider_error:timeout`, `unknown_chunk_citation`, or
`unknown_metric_citation`.

## Warning strings

The workflow warning strings that matter operationally are:

- `critic_revision_failed` — the bounded rewrite could not be parsed or failed
  validation
- `guardrail_rejected` — the upstream Decision Synthesizer attempted to mutate
  deterministic risk math before the Critic stage

## Reading `critic_effect`

`critic_effect` is persisted on the run and exposed by the API. It records the
mode, acceptance status, objection counts, deterministic/model objection IDs,
baseline decision, final decision, confidence penalty, revision counts, and
version stamp. For the trace-step summary fields and SQL examples, see
[`docs/agent-trace.md`](agent-trace.md).
