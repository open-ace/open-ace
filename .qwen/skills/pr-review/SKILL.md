---
name: pr-review
description: Review a GitHub PR by analyzing the branch diff against the linked issue requirements, launch parallel analysis agents (context + security), then post structured findings as PR comments.
source: auto-skill
extracted_at: '2026-06-03T11:17:16.695Z'
---

# PR Review Workflow

## 1. Gather context

```bash
# Get issue details (if PR references an issue)
gh issue view <issue-number> --repo <owner>/<repo> --json title,body,author,labels,state,comments

# Get PR details
gh pr view <pr-number> --repo <owner>/<repo> --json title,body,headRefName,baseRefName,state,comments

# See what changed
git diff main...HEAD --stat
git diff main...HEAD --name-only
git log main...HEAD --oneline
```

## 2. Read full diff and each changed file

```bash
git diff main...HEAD
```

Then use `read_file` to read surrounding context in each changed file — not just the diff hunks, but the functions/classes they belong to. This reveals edge cases the diff alone cannot show.

## 3. Launch parallel analysis agents

Launch background agents simultaneously for deeper analysis while you continue reading code:

- **Context agent**: Trace variable origins, function signatures, table schemas, and call chains for each changed area. Specifically trace where untrusted input (request body, query params) flows.
- **Security agent (attacker mindset)**: For every change, ask "what can an unauthenticated or malicious caller do with this?" Check auth decorators, trust boundaries, error handling paths that silently swallow failures.
- **Cross-cutting agent** (if needed): Grep for new identifiers (stream types, event names, table columns) to verify they're consumed correctly everywhere.

**Critical**: Do NOT consider the review complete until every agent's findings are posted to the PR. Late-returning agents are not optional — check their output and post a follow-up comment if they find unique issues.

## 4. First-pass: map changes to issue requirements

Create a coverage table:

| # | Requirement | Status |
|---|-------------|--------|
| 1 | ... | Done / Partial / Missing |

Then list observations as potential follow-ups with specific file/line references.

## 5. Post reviews

For long review bodies with markdown code blocks, `gh pr comment --body "$(cat <<...")` may fail on argument parsing. Use `--body-file` instead:

```bash
# Write review to temp file first
write_file("/tmp/pr<N>-review.md", content)

# Post it
gh pr comment <pr-number> --repo <owner>/<repo> --body-file /tmp/pr<N>-review.md
```

Post the first review as soon as it's ready, then post a follow-up security/context review once all agents complete.

## 6. Deeper code analysis (integrated with agent findings)

Focus on:

- **Error handling paths** — what happens when `except` blocks catch errors? Are they logged or silently swallowed? If the failure produces the exact bug the PR is fixing, severity is higher.
- **Input validation** — are there unvalidated parameters that flow from untrusted sources to the frontend or database?
- **Authentication/authorization** — check which endpoints are protected (`@login_required`, `_exact_exempt` sets) and which trust the caller. Flag if the PR extends the impact surface of an unauthenticated endpoint.
- **Cross-cutting concerns** — grep for the new identifiers to verify they're consumed correctly everywhere.

## 7. Key principles

- **Reference specific lines** with GitHub permalinks (`file#LNN-LL`) so the author can jump straight to the code.
- **Distinguish blocking vs follow-up vs pre-existing** — be explicit about severity and whether the issue is introduced by the PR or pre-existing.
- **Acknowledge what's correct** — not just what's wrong. Positive signals help the author know what to preserve.
- **Verify claims before asserting** — grep/read the code rather than guessing how a function behaves.
- **Post ALL agent findings** — never discard late-returning agent results without checking for unique issues.

## 8. Second-pass checklist (after author addresses feedback)

When the author pushes a new commit responding to review feedback, re-review:

1. **Verify each suggestion was actually implemented** — don't just trust the reply comment. Read the new diff and trace the code path to confirm the fix works end-to-end.
2. **Field whitelist / allowlist check** — If the PR calls `update_*` or `set_*` methods with new field names, verify those fields are in the target function's allowlist (e.g., `ALLOWED_UPDATE_FIELDS`, safe column lists, DTO schemas). A missing allowlist entry means the update silently does nothing — a particularly dangerous class of bug because the code *looks* correct.
3. **Singleton reuse** — If the PR introduces a new call to a factory/getter (`get_remote_agent_manager()`, `get_db_connection()`) in the same function where one already exists, flag it as a minor cleanup opportunity.
4. **Return value handling** — If `update_session_fields()` or similar returns a `bool` indicating success, check whether the caller ignores it when it shouldn't.
