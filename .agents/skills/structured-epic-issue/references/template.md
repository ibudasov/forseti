# Structured Epic / Remediation Issue Template

```markdown
Part of #<epic>. Related completed work: #<id> (<topic>), implemented in PR #<pr>.

## Check and scorecard evidence

Cloud scorecard on #<epic> (<comment-url>), refreshed <timestamp> at commit `<sha>`.

| Status | Check | Detail |
| --- | --- | --- |
| ❌ | `<check.id>` <description> | <redacted detail> |

Related context: <adjacent passing checks>. Those checks do not establish <unproven invariant>.

## Independent investigation and verified RCA

Read-only diagnostics succeeded through an existing authorized session. Responses were captured privately and reduced to counts and booleans; no identities, connection details, secret values, application data, or raw logs are included here.

| Observation | Result |
| --- | --- |
| <metric or predicate 1> | <count or true/false> |
| <metric or predicate 2> | <count or true/false> |

The differing invariant is verified: <exact predicate>. At `<sha>`, `<function>` (`<permalink>`) <what it does>.

The implementation intent differs from this assumption:
- `<file-1>`: <intended behavior>
- `<file-2>`: <intended behavior>
- `<test-file>`: <gap in current fixtures>

Not established: <list unproven invariants, e.g., creation history, inherited memberships, effective privileges>. Do not declare <boundary> healthy from these counts alone.

## Impact

- <Operational impact of false red / broken signal.>
- <Risk of unsafe naive remediation.>
- <Separate unverified invariant that must not be conflated.>

## Narrowly scoped remediation

1. Correct `<check-or-component>` to <expected behavior>. Continue rejecting missing required items and unexpected additions; do not replace exact checks with permissive count-only checks.
2. Preserve <existing predicate>. Report separate redacted booleans so future failures identify the failing predicate without disclosing names.
3. Add deterministic offline fixtures for <observed real shape>, <missing required cases>, and <unexpected addition>. Assert both status and redacted detail. Update directly related documentation.
4. Independently verify <security/privilege boundary> before claiming it is safe. No resource deletion, IAM/auth changes, password rotation, or `terraform apply` is authorized by this diagnosis.

## Actionable follow-up diagnostics

Reuse an already authorized read-only session. Run `<read-only command with privately-resolved placeholders>` and publish only counts and booleans.

An owner/operator with an existing authorized session can run this metadata-only query using privately bound parameters:

```sql
-- Metadata-only query returning booleans, no application tables read
```

If no existing session is available, explicitly leave this validation unverified and request the redacted result from an authorized operator.

## Verification and completion evidence

- [x] Identify the failing predicate with read-only evidence. Evidence: scorecard link and sanitized observations above.
- [ ] Future implementation: `make lint typecheck test` and `make infra-check test-infra` pass inside the repository's Docker Compose setup; record exact commands, results, and CI links. Tests and PR workflows must remain offline.
- [ ] Owner/operator reports the redacted <privilege/metadata> result above, or explicitly records that it remains unavailable.
- [ ] After an approved fix, refresh `/verify` on #<epic> and link evidence showing `<check.id>` passes for the intended inventory; verify fixtures still reject missing required items and unexpected additions.
- [ ] Confirm no required runtime/default resource was removed to satisfy the check, no application data was read, and no sensitive details were published.

Existing-issue search: <summary of searches>.
Commands executed: <exact read-only commands, private args withheld>. No code or cloud resources were changed.
```
