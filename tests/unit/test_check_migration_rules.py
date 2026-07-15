"""Unit tests for scripts/lint/check_migration_rules.py (Issue #1704)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

LINT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "lint"
sys.path.insert(0, str(LINT_DIR))

import check_migration_rules as rules  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_migration(tmp_path: Path, body: str, name: str = "rev_test.py") -> Path:
    """Write a migration body to tmp_path/name and return its path."""
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# MIG001: no runtime app.* imports
# ---------------------------------------------------------------------------


class TestMig001NoAppImports:
    def test_clean_migration_passes(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            from alembic import op
            import sqlalchemy as sa

            revision = "rev_test"
            down_revision = None

            def upgrade():
                op.add_column("t", sa.Column("c", sa.Integer()))
            """,
        )
        assert rules.check_file(path) == []

    def test_import_app_top_level(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            import app
            from alembic import op

            revision = "rev_test"
            down_revision = None
            """,
        )
        violations = rules.check_file(path)
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == "MIG001"
        assert v.line == 2
        assert "app" in v.message

    def test_from_app_submodule(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            from alembic import op
            from app.repositories.database import Database

            revision = "rev_test"
            down_revision = None
            """,
        )
        violations = rules.check_file(path)
        assert len(violations) == 1
        assert violations[0].rule == "MIG001"
        assert violations[0].line == 3

    def test_type_checking_guard_allowed(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            from typing import TYPE_CHECKING
            from alembic import op

            if TYPE_CHECKING:
                from app.models import Workflow

            revision = "rev_test"
            down_revision = None
            """,
        )
        assert rules.check_file(path) == []

    def test_typing_attribute_guard_allowed(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            import typing
            from alembic import op

            if typing.TYPE_CHECKING:
                import app.something

            revision = "rev_test"
            down_revision = None
            """,
        )
        assert rules.check_file(path) == []

    def test_non_app_imports_allowed(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            from alembic import op
            import sqlalchemy as sa
            from migrations.baseline import table_exists

            revision = "rev_test"
            down_revision = None
            """,
        )
        assert rules.check_file(path) == []

    def test_multiple_app_violations_all_reported(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            import app
            from app.repositories import user_repo

            revision = "rev_test"
            down_revision = None
            """,
        )
        violations = rules.check_file(path)
        assert {v.rule for v in violations} == {"MIG001"}
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# MIG002: CONCURRENTLY policy
# ---------------------------------------------------------------------------


class TestMig002ConcurrentlyPolicy:
    def test_approved_create_index_pattern_passes(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def upgrade():
                if _is_pg():
                    with op.get_context().autocommit_block():
                        op.create_index("idx", "t", ["c"], postgresql_concurrently=True)
                else:
                    op.create_index("idx", "t", ["c"])

            def _is_pg():
                return True
            """,
        )
        assert rules.check_file(path) == []

    def test_approved_drop_index_pattern_passes(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def downgrade():
                with op.get_context().autocommit_block():
                    op.drop_index("idx", table_name="t", postgresql_concurrently=True)
            """,
        )
        assert rules.check_file(path) == []

    def test_concurrent_kwarg_outside_autocommit_block(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def upgrade():
                op.create_index("idx", "t", ["c"], postgresql_concurrently=True)
            """,
        )
        violations = rules.check_file(path)
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == "MIG002"
        assert "autocommit_block" in v.message

    def test_concurrent_drop_kwarg_outside_autocommit_block(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def downgrade():
                op.drop_index("idx", table_name="t", postgresql_concurrently=True)
            """,
        )
        violations = rules.check_file(path)
        assert len(violations) == 1
        assert violations[0].rule == "MIG002"

    def test_raw_concurrently_via_op_execute(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def upgrade():
                op.execute("CREATE INDEX CONCURRENTLY idx ON t (c)")
            """,
        )
        violations = rules.check_file(path)
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == "MIG002"
        assert "execute()" in v.message or "text()" in v.message

    def test_raw_concurrently_via_connection_execute(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            import sqlalchemy as sa
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def upgrade():
                op.get_bind().execute(sa.text("DROP INDEX CONCURRENTLY idx"))
            """,
        )
        violations = rules.check_file(path)
        assert len(violations) == 1
        assert violations[0].rule == "MIG002"

    def test_concurrently_nested_deeply_in_autocommit_passes(self, tmp_path: Path):
        """autocommit_block may wrap helper calls that create the index."""
        path = _write_migration(
            tmp_path,
            """
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def upgrade():
                if _is_pg():
                    with op.get_context().autocommit_block():
                        _do_create()

            def _do_create():
                op.create_index("idx", "t", ["c"], postgresql_concurrently=True)
            """,
        )
        # Note: _do_create is a sibling function — its create_index node is NOT
        # lexically nested inside the with-block. This documents that the check
        # is lexical (structural), as designed: it catches the direct misuse
        # pattern. Authors must inline the call under autocommit_block().
        violations = rules.check_file(path)
        assert len(violations) == 1
        assert violations[0].rule == "MIG002"

    def test_plain_index_without_concurrently_passes(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def upgrade():
                op.create_index("idx", "t", ["c"])
            """,
        )
        assert rules.check_file(path) == []

    def test_non_concurrent_execute_passes(self, tmp_path: Path):
        path = _write_migration(
            tmp_path,
            """
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def upgrade():
                op.execute("CREATE INDEX idx ON t (c)")
            """,
        )
        assert rules.check_file(path) == []


# ---------------------------------------------------------------------------
# main() and file discovery
# ---------------------------------------------------------------------------


class TestMainAndDiscovery:
    def test_main_passes_for_clean_dir(self, tmp_path: Path, capsys):
        _write_migration(
            tmp_path,
            """
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def upgrade():
                op.add_column("t", __import__("sqlalchemy").Column("c"))
            """,
            name="0001_clean.py",
        )
        rc = rules.main([str(tmp_path)])
        assert rc == 0
        err = capsys.readouterr().err
        assert "pass MIG001/MIG002" in err

    def test_main_fails_on_violation(self, tmp_path: Path, capsys):
        _write_migration(
            tmp_path,
            """
            import app

            revision = "rev_test"
            down_revision = None
            """,
            name="0001_bad.py",
        )
        rc = rules.main([str(tmp_path)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "MIG001" in err

    def test_main_missing_dir(self, tmp_path: Path):
        rc = rules.main([str(tmp_path / "does_not_exist")])
        assert rc == 2

    def test_main_empty_dir(self, tmp_path: Path):
        rc = rules.main([str(tmp_path)])
        assert rc == 2

    def test_main_ignores_dunder_init(self, tmp_path: Path):
        (tmp_path / "__init__.py").write_text("import app\n", encoding="utf-8")
        # No real migration files -> still treated as empty -> exit 2.
        rc = rules.main([str(tmp_path)])
        assert rc == 2


# ---------------------------------------------------------------------------
# Completeness invariant: real migrations/versions/ must always be clean.
# ---------------------------------------------------------------------------


class TestRealMigrationsInvariant:
    def test_all_real_migrations_are_clean(self):
        """Every committed migration must obey MIG001/MIG002.

        Guards against a future migration reintroducing the failure modes that
        broke the autonomous CI-repair workflow (Issue #1704). If this fails,
        fix the offending migration before merging — do not relax this test.
        """
        real_dir = rules.DEFAULT_VERSIONS_DIR
        if not real_dir.is_dir():
            pytest.skip(f"migrations/versions not found at {real_dir}")
        files = [f for f in sorted(real_dir.glob("*.py")) if not f.name.startswith("__")]
        assert files, "expected at least one migration file"
        violations: list[rules.Violation] = []
        for f in files:
            violations.extend(rules.check_file(f))
        assert violations == [], "Committed migrations violate authoring rules:\n  " + "\n  ".join(
            v.format() for v in violations
        )
