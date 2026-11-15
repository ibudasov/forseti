# GitHub activity dashboard

Requires Python 3.12+ on macOS/Linux and an authenticated, recent GitHub CLI
with `gh agent-task list` support (preview). It uses your existing `gh` login;
it never approves, cancels, or modifies anything.

From this repository:

```sh
python3 scripts/gh_dashboard.py
python3 scripts/gh_dashboard.py --interval 1
python3 scripts/gh_dashboard.py --repo ibudasov/forseti --once
```

The default is a full-screen terminal dashboard. Press **q** to quit and
restore the terminal, or use arrows/Page Up/Page Down to scroll. Refreshes
start five seconds after the previous fetch finishes; requests do not
overlap. `--interval` accepts any finite positive number of seconds.
Quitting also cancels an in-flight `gh` request. Individual requests time out
after 20 seconds. Errors appear in their section, not as empty successful
results; `--once` exits nonzero if any section failed.

Actions are scoped to the selected repository. Agent sessions are scoped to
the **signed-in user across all repositories**, including sessions without
a repository. Only `in_progress` sessions are shown, not idle or completed
sessions. These are the sessions reported by GitHub, not arbitrary local
processes.

## Equivalent read-only commands

### 1. Failed Actions created in the last two hours

Generate a portable UTC cutoff, then list failed workflow runs:

```sh
SINCE=$(python3 -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)-timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))')
gh run list --repo ibudasov/forseti --status failure \
  --created ">=$SINCE" --limit 1000 \
  --json databaseId,workflowName,url
```

This filters **run creation time**, not failure completion time. Runs created
earlier but failing recently are outside this view. Cancelled and timed-out
runs are not classified as `failure`. The dashboard recomputes the cutoff
on every refresh and warns when its 1,000-run limit is reached.

### 2. Actively running agent sessions

```sh
gh agent-task list --limit 1000 \
  --json id,name,state,repository,pullRequestUrl \
  --jq '.[] | select(.state == "in_progress")'
```

The dashboard warns if 1,000 sessions were returned: older active sessions
may be outside that limit. Older `gh` versions without this preview command
produce an explicit section error.

### 3. Actions waiting for approval

First list waiting runs:

```sh
gh api repos/ibudasov/forseti/actions/runs --method GET \
  -f status=waiting -F per_page=100 --paginate \
  --jq '.workflow_runs[] | {id, name, html_url}'
```

For each run, check whether **you** can approve its pending environments:

```sh
gh api repos/ibudasov/forseti/actions/runs/RUN_ID/pending_deployments \
  --jq '.[] | select(.current_user_can_approve) | .environment.name'
```

The dashboard automates both commands. A `waiting` run alone is not proof
that your approval is required: it may be waiting on a timer or another
reviewer. Only environments with `current_user_can_approve: true` are shown.

Also list workflow-level action/approval candidates, such as fork workflows:

```sh
gh api repos/ibudasov/forseti/actions/runs --method GET \
  -f status=action_required -F per_page=100 --paginate \
  --jq '.workflow_runs[] | {id, name, html_url}'
```

GitHub does not provide the environment eligibility flag for these runs.
The dashboard labels them **candidates; eligibility unknown** and includes
the run URL for inspection. No approval is performed. Run queries are
paginated; GitHub caps filtered searches at 1,000 results, and the
dashboard warns at that cap.

## Validation

`make test` runs the offline dashboard tests with the existing Docker Compose
suite. The tests replace GitHub responses with deterministic fixtures;
they never contact GitHub or external model services.
