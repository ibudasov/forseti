---
name: structured-epic-issue
description: >-
  Use this skill when drafting, reviewing, or structuring epics and remediation
  issues (especially cloud, infrastructure, or scorecard-backed work) with
  verified root-cause analysis, strict redaction boundaries, actionable
  follow-up diagnostics, and rigorous verification checklists (scorecard
  /verify, offline Docker Compose gates, and separate privilege checks).
---

# Structured Epic & Remediation Issue Guide

Use this structure when creating epics or follow-up issues so every issue is
evidence-backed, narrowly scoped, safe to execute, and unambiguous to verify.

For a copy-paste skeleton, see [the issue template](./references/template.md).

## Core Principles

1. **Evidence before claims**: Cite exact commits, file line ranges, scorecard
   comment URLs, and observed outputs. Separate what is *verified* from what is
   *not established*.
2. **Redaction by default**: Never paste raw cloud API responses, identities,
   connection strings, secrets, or application table data into public issues.
   Reduce observations to counts, category booleans, and redacted summaries.
3. **Explicit authorization boundaries**: State which mutations are *not*
   authorized (e.g., no resource deletion, IAM/auth changes, secret rotation, or
   `terraform apply`).
4. **No false confidence from adjacent checks**: Passing health checks or
   execution logs do not prove privilege boundaries or inventory invariants.

## Required Sections

### 1. Header & Lineage
- Title format: `<area-or-check-id>: <concise symptom or goal>; <secondary invariant>`
- Link the parent epic (`Part of #...`) and related completed issues/PRs.

### 2. Check & Scorecard Evidence
- Link the exact scorecard comment URL (e.g. `#121#issuecomment-...`), its
  refresh timestamp, and the scored commit SHA.
- Include the failing/relevant scorecard table row(s) (`Status | Check | Detail`).
- Note related passing checks and explicitly state what they do *not* prove.

### 3. Independent Investigation & Verified RCA
- Summarize read-only observations in a table of counts and booleans.
- Pinpoint the exact differing invariant and link the code at the scored commit.
- Contrast actual implementation intent (Terraform, jobs, app code, test
  fixtures) against the failing check or assumption.
- Add a **Not established** paragraph listing invariants that remain unproven.

### 4. Impact
- Explain why the failure matters (e.g., false red scorecard, drift blindness).
- Call out **unsafe naive remediations** (e.g., deleting a required runtime
  user just to satisfy a count-of-one check).

### 5. Narrowly Scoped Remediation
Break remediation into numbered, testable steps:
1. **Fix the invariant**: Validate against explicit known categories/contracts,
   not inferred provenance or permissive count-only checks.
2. **Preserve & surface predicates**: Report separate redacted booleans/counts
   in check output so future failures identify the exact failing predicate
   without leaking names.
3. **Offline fixtures, negative tests & docs**: Require deterministic offline
   fixtures matching real API shapes (including absent fields), negative cases
   (missing required items, unexpected additions), assertions on both status and
   redacted detail, and updates to directly related docs.
4. **Separate security/privilege verification**: Verify privilege boundaries
   independently rather than conflating them with inventory checks.

### 6. Actionable Follow-up Diagnostics
- Provide exact read-only commands or metadata-only queries using privately
  bound parameters (`<privately-resolved-...>`, `:'param'`) that return
  booleans/counts rather than names or application data.
- Specify the fallback: if no authorized session is available, **do not** start
  login, retrieve secrets, or alter auth—leave the item unverified and request
  the redacted boolean result from an authorized operator.

### 7. Verification & Completion Evidence
Use a checklist distinguishing already-verified evidence (`[✓]`) from required
implementation gates (`[ ]`):
- `[✓]` Root-cause evidence captured from read-only diagnostics / scorecard.
- `[ ]` **Offline test & infra gates**: `make lint typecheck test` and
  `make infra-check test-infra` pass inside the repository's Docker Compose
  setup; record exact commands, results, and CI links.
- `[ ]` **Separate privilege/metadata check**: Owner/operator reports the
  redacted boolean result, or explicitly records that it remains unavailable.
- `[ ]` **Scorecard `/verify` refresh**: After merge, run `/verify` (or
  `/verify full` if paid checks apply) on the tracking epic (e.g., `#121`) and
  link the updated scorecard showing the target check passes while negative
  fixtures still reject missing/unexpected items.
- `[ ]` **Safety & redaction audit**: Confirm no required runtime/default
  resources were removed to satisfy the check, no application data was read,
  and no sensitive details were published.

End the issue with a short **Investigation audit trail**: existing-issue search
scope, exact commands executed, and an explicit statement of what changed (or
that no code/cloud resources changed and no tests were run).
