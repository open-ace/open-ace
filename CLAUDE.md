# CLAUDE.md

Guidance for Claude Code and human contributors.

## Where process documents go

Fix write-ups, CI post-mortems, implementation summaries, progress snapshots,
handover notes and per-issue plans go in **`docs/dev-notes/`** — never the
repository root.

- Name them `<issue>-<slug>.md` (`2437-flock-reclaim-plan.md`) or
  `<date>-<slug>.md` (`2026-08-11-ci-lint-fix.md`).
- Do not open a second file for the same effort. There must never be another
  `..._ROUND2.md` / `..._FINAL.md` / `..._FINAL_VERIFICATION.md` chain —
  update the existing note instead.
- Prefer not writing a file at all: a code comment, a docstring, or the PR
  description is usually the better home. Write a dev-note only when the
  reasoning must outlive the PR.
- The repository root keeps only user-facing docs (`README.md`, `CHANGELOG.md`,
  `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `ROADMAP.md`, `SECURITY.md`) plus AI
  tool instruction files. Everything else in the root is rejected by
  `scripts/lint/check_root_docs.py` (pre-commit) and ignored by `.gitignore`.

Why this rule exists: between 2026-08-05 and 08-12 the autonomous pipeline
committed 23 such files (2,554 lines) to the root. They referenced only each
other, nothing else referenced them, and they pushed the product README below a
wall of CI firefighting logs — right as the project received its first external
visitors. See `docs/dev-notes/README.md`.

## Pushing

Do not call `git push` directly. Use `scripts/push.sh [git-push args]`: it runs
the exact CI lint command first, folds formatter autofixes into the commit
being pushed (amending when the commit is not yet on the remote), and aborts
the push on failures autofix cannot resolve.

Why this rule exists: on 2026-08-15 the same failure hit #2712, #2718 and
#2719 (#2205 before them) — black/isort autofixes landed in the worktree but
not in the pushed commit, CI lint went red, and a local `pre-commit run` still
passed because it checks the worktree, not the commit. When such a push slips
through anyway, the `lint-autofix` job in `.github/workflows/ci.yml` pushes
the fixes back and re-triggers CI — but going through `scripts/push.sh` avoids
the wasted red run.

## Test placement and CI semantics

- Choose one canonical location by runtime contract: `tests/unit/`,
  `tests/integration/`, `tests/e2e/`, or
  `tests/performance/`.
- Do not create new `tests/issues/<number>/` directories. That tree is a legacy
  quarantine excluded from default execution while Issue #2429 migration is in
  progress. CI requires it to remain fully collectable, but collection is not
  equivalent to execution.
- Do not create a top-level `tests/regression/` or copy a test across multiple
  directories. Mark bug tests once with `pytest.mark.regression` and
  `pytest.mark.issue(<number>)` in their canonical layer.
- Before claiming a test is a gate, verify the exact CI lane that executes it.
  See `docs/TEST_LAYERS.md` for the directory/lane contract and migration rules.

## Schema snapshots (`schema-sync` CI)

`schema/schema-postgres.sql` and `schema/schema-sqlite.sql` are GENERATED from
the Alembic migrations by `scripts/rebuild_schema_snapshots.py`. The
`schema-sync` CI regenerates them and gates on a byte-exact `git diff`.

- Never hand-edit the `schema/*.sql` files (parens, indent, column order, SQLite
  type-case are derived; a hand edit will not match regeneration).
- When you change anything under `migrations/versions/`, regenerate and commit:
  ```bash
  python scripts/rebuild_schema_snapshots.py --postgres-url postgresql://user:pass@localhost/disposable
  git add schema/schema-postgres.sql schema/schema-sqlite.sql
  ```
- The pre-commit `check-schema-sync` hook is warn-only + structure-only; it can
  pass while the byte-exact CI gate fails. Always regenerate.
