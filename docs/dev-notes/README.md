# dev-notes — process write-up archive

This directory holds **process** documents: fix retrospectives, CI incident
analyses, implementation summaries, progress snapshots, hand-off notes, and
per-issue design drafts — written by humans and agents alike.

Archive policy (2026-09-29 docs governance):

- Process documents live here, never in the repository root.
- The archive is **English-only going forward**; historical files keep their
  original language.
- Curated long-lived documentation (architecture, runbooks, API contracts)
  belongs under `docs/` (`guide/`, `dev/`, `contracts/`, `security/`), not
  here. See `docs/README.md` for the placement rules.
- Short notes that are tightly coupled to code should be code comments or
  docstrings, not standalone files.

## Why this directory exists

Between 2026-08-05 and 2026-08-12 the autonomous development pipeline
committed 23 such documents (2,554 lines) straight to the repository root —
`CI_FIX_ROUND2.md`, `CI_FIX_FINAL_VERIFICATION.md`, `IMPLEMENTATION_SUMMARY_2327.md`,
`FINAL_STATUS.md`, and friends. They referenced each other, but nothing else in
the repo referenced them. The root `.md` count went from 5 to 29, so the first
screen an outside visitor saw was 20+ CI repair logs instead of a product
introduction — during the project's first window of external visitors.

The documents had value; only their location was wrong. So they got a proper
home.

## Conventions

- Name files `<issue-number>-<slug>.md` (e.g. `2179-tenant-admin-permissions.md`) or
  `<date>-<slug>.md` (e.g. `2026-09-13-sudoers-audit.md`).
- Do not open a new `ROUND2` / `FINAL` / `FINAL_VERIFICATION` file for the same
  topic — update the existing one.
- Reference docs that were demoted from `docs/` during governance keep a
  leading blockquote explaining where the durable content now lives (e.g.
  `2179-tenant-admin-permissions.md`, `2026-09-29-gh-cli-version-policy.md`).

## Markdown allowed in the repository root

`README.md` / `README_EN.md` / `CHANGELOG.md` / `CONTRIBUTING.md` /
`CODE_OF_CONDUCT.md` / `ROADMAP.md` / `SECURITY.md` / `GOVERNANCE.md` /
`MAINTAINERS.md` / `AUTHORS.md` / `LICENSE.md`, plus AI tool instruction files
(`CLAUDE.md` / `AGENTS.md` / `GEMINI.md` / `QWEN.md`).

## How this is enforced

Three layers, loosest first:

1. `.gitignore` — root-level `CI_*.md`, `*_SUMMARY.md`, `FINAL_*.md` names are
   not picked up by `git add` by default.
2. `scripts/lint/check_root_docs.py` — a pre-commit hook that catches
   `git add -f`, renames, and other `.gitignore` bypasses.
3. `CLAUDE.md` — tells agents where to write (pipeline agents read it).

To add a new permanent root-level document, add the filename to `ALLOWED` in
`scripts/lint/check_root_docs.py`.
