# CLAUDE.md

Guidance for Claude Code and human contributors.

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
