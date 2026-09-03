# Before calling the work done

- run the tests locally
- `make up` and try to execute the newly introduced endpoint
- make sure there are tests for the new code introduced, and the relevant tests are adjusted
- the pipeline is green, meaning all the checks have passed and the project builds fine
- **check the actual GitHub Actions workflow runs for the PR's head commit, not just the PR checks summary.** A PR check can show green while the underlying Actions run is still queued, cancelled, or failed for an unrelated reason (flaky job, infra hiccup). Open the PR's **Checks**/**Actions** tab (or use `actions_list`/`get_job_logs`), confirm every required workflow run is `completed`/`success` for the latest commit, and read the job logs when anything is red, yellow, or missing.
- if there are any problems -- read the logs, find a mistake, re-do the check

**Remote mode.** When the author has no local Docker environment, the requirement to run `make up` and call the endpoint is satisfied by a green `Smoke` workflow run whose run URL and artifact are linked in the PR body. Every other item in this document applies unchanged.