"""Regression tests for PostgreSQL audit_logs.success schema drift."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from alembic.script import ScriptDirectory

pytestmark = pytest.mark.regression

REVISION = "20260918_002_normalize_audit_log_success"
MIGRATION = Path(__file__).parents[2] / f"migrations/versions/{REVISION}.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("audit_log_success_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_upgrade_normalizes_legacy_postgresql_integer_success(monkeypatch):
    """Dropping the INTEGER-to-BOOLEAN repair must fail this test."""
    migration = _load_migration()
    execute = MagicMock()
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
            execute=execute,
        ),
    )

    migration.upgrade()

    sql = " ".join(execute.call_args.args[0].split())
    assert "ALTER COLUMN success TYPE BOOLEAN" in sql
    assert "WHEN success IS NULL THEN NULL" in sql
    assert "WHEN success = 0 THEN FALSE" in sql
    assert "ALTER COLUMN success SET DEFAULT TRUE" in sql


def test_upgrade_leaves_sqlite_integer_boolean_storage_unchanged(monkeypatch):
    migration = _load_migration()
    execute = MagicMock()
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
            execute=execute,
        ),
    )

    migration.upgrade()

    execute.assert_not_called()


def test_repair_is_reachable_from_a_single_migration_head():
    """Pointing the repair at a stale ancestor must create a second head.

    Deliberately not asserting the head *is* this revision: the next migration
    to land would then have to edit this unrelated file. Assert the invariant
    that matters — one head, and this repair on the path to it.
    """
    script = ScriptDirectory(str(MIGRATION.parent.parent))

    heads = script.get_heads()
    assert len(heads) == 1

    ancestry = {rev.revision for rev in script.iterate_revisions(heads[0], "base")}
    assert REVISION in ancestry
