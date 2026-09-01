# Before calling the work done

- run the tests locally
- `make up` and try to execute the newly introduced endpoint
- make sure there are tests for the new code introduced, and the relevant tests are adjusted
- the pipeline is green, meaning all the checks have passed and the project builds fine
- if there are any problems -- read the logs, find a mistake, re-do the check

**Remote mode.** When the author has no local Docker environment, the requirement to run `make up` and call the endpoint is satisfied by a green `Smoke` workflow run whose run URL and artifact are linked in the PR body. Every other item in this document applies unchanged.