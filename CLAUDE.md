# CLAUDE.md

Guidance for Claude Code and human contributors.

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
