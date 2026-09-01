- do not update readme file with endpoints information, we have /docs page for this
- after any code change (new code, edits, refactors, or migrations), you MUST run `make test` before finishing work unless the user explicitly tells you not to run tests
- if no code changes were made, state that tests were not run because no code changed
- in the final response, always report the exact test command executed and whether it passed or failed

## Working agreement for the unattended loop

**Revert first.** If `main` goes red, open a revert PR for the offending merge immediately. Do not fix forward. A revert is always correct and always reviewable; a speculative fix authored while nobody can run the code is not.

**One issue per PR, no scope creep.** A PR may only touch files listed in its issue's "Files touched" section. Anything beyond that requires a comment in the PR explaining why, and an edit to the issue. Unrelated refactors, dependency bumps, and drive-by renames are rejected on sight.

**Evidence is mandatory.** The PR body must contain: the exact commands run and their pass/fail (already required above), the rendered scorecard table, and the result of every numbered manual-validation step from the issue — executed by the author, with observed output, not restated as intentions.

**Work-in-progress limit.** At most two open agent PRs at a time, and never two PRs touching the same module. Merge or close before starting a third.

**No live external calls in tests or CI.** No Gemini, no Vertex, no Alpha Vantage, no network of any kind in `make test` or in any workflow triggered by `pull_request`. Deterministic fixtures and cassettes only. The single exception is `Smoke`, run in `mode: agentic`, which is manual-only and deliberate.

**Coverage integrity.** The coverage gate (`--cov-fail-under` in the `Makefile`) exists to force tests, not to be satisfied. Forbidden: adding `# pragma: no cover`, lowering `--cov-fail-under`, adding `-k` exclusions or `skip`/`xfail` markers to make a suite pass, and writing tests that execute code without asserting behaviour. If a line genuinely cannot be tested, say so in the PR and explain why.

**Typing integrity.** No new `# type: ignore`. No additions to the mypy override list in `pyproject.toml` — that list may only shrink.

**Ask, don't improvise.** If a fact stated in an issue is stale, or two issues contradict each other, stop and report in a comment.