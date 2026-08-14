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

    def test_refresh_materialized_view_concurrently_flagged(self, tmp_path: Path):
        """REFRESH MATERIALIZED VIEW CONCURRENTLY is caught.

        This statement cannot run inside a transaction and has no Alembic
        ``op.*`` helper, so it can only be issued via raw ``op.execute()`` —
        exactly the pattern MIG002(a) targets. The DDL-verb-anchored regex must
        include REFRESH in its verb set.
        """
        path = _write_migration(
            tmp_path,
            """
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def upgrade():
                op.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_summary")
            """,
        )
        violations = rules.check_file(path)
        assert len(violations) == 1
        assert violations[0].rule == "MIG002"

    def test_concurrently_in_sibling_helper_is_flagged(self, tmp_path: Path):
        """A create_index delegated to a sibling helper is flagged.

        ``_do_create`` is a sibling function — its ``create_index`` node is NOT
        lexically nested inside the ``with`` block. MIG002(b) is a lexical
        (structural) check, so it cannot see the runtime call relationship and
        flags this: authors must inline the ``op.create_index`` call directly
        under ``autocommit_block()`` rather than delegating to a helper.
        """
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

    def test_concurrently_as_data_substring_not_flagged(self, tmp_path: Path):
        """MIG002(a) matches the CONCURRENTLY keyword, not a bare substring.

        A data backfill whose SQL happens to contain the word "concurrently" in
        a value/predicate must not be a false positive — only the DDL keyword
        (CREATE/DROP/REINDEX ... CONCURRENTLY) is the signal.
        """
        path = _write_migration(
            tmp_path,
            """
            from alembic import op

            revision = "rev_test"
            down_revision = None

            def upgrade():
                op.execute("UPDATE t SET flagged=1 WHERE note LIKE '%concurrently%'")
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
        # --skip-released-check: this fixture is a one-file tree, not a full
        # migrations/versions/, so MIG003 would (correctly) report every real
        # revision id as missing.
        rc = rules.main([str(tmp_path), "--skip-released-check"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "pass MIG001/MIG002" in err

    def test_main_runs_mig003_by_default(self, tmp_path: Path, capsys, monkeypatch):
        """The released-id check is on unless explicitly skipped."""
        _write_migration(
            tmp_path,
            """
            revision = "rev_test"
            down_revision = None

            def upgrade():
                pass
            """,
            name="0001_clean.py",
        )
        # Pin the baseline so the assertion does not depend on the checkout's
        # git state -- an earlier version of this test accepted rc in (0, 1)
        # and "MIG003 or MIG003: skipped", which no implementation could fail.
        monkeypatch.setattr(rules, "_baseline_commit", lambda ref: "deadbeef")
        monkeypatch.setattr(
            rules, "_revision_ids_at_ref", lambda ref: {"only_on_main": "migrations/versions/m.py"}
        )

        rc = rules.main([str(tmp_path)])

        err = capsys.readouterr().err
        assert rc == 1
        assert "MIG003" in err
        assert "only_on_main" in err

    def test_skip_flag_turns_mig003_off(self, tmp_path: Path, capsys, monkeypatch):
        _write_migration(
            tmp_path,
            """
            revision = "rev_test"
            down_revision = None

            def upgrade():
                pass
            """,
            name="0001_clean.py",
        )
        monkeypatch.setattr(rules, "_baseline_commit", lambda ref: "deadbeef")
        monkeypatch.setattr(
            rules, "_revision_ids_at_ref", lambda ref: {"only_on_main": "migrations/versions/m.py"}
        )

        rc = rules.main([str(tmp_path), "--skip-released-check"])

        err = capsys.readouterr().err
        assert rc == 0
        assert "MIG003" not in err

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


# ---------------------------------------------------------------------------
# MIG003: a revision id that shipped must never disappear
# ---------------------------------------------------------------------------


class TestMig003ReleasedRevisionIds:
    """The rule that would have caught the 20260731_003 renumbering.

    ``20260731_003_add_proxy_token_terminated_fields`` shipped on main, was
    later renumbered to ``..._004_...`` so a newer migration could take the 003
    slot, and every database stamped with the old id started failing
    ``upgrade head`` with "Can't locate revision". Alembic keys history off the
    id, not the filename.
    """

    @pytest.fixture(autouse=True)
    def _stub_baseline_commit(self, monkeypatch):
        """Pin the baseline commit so these tests do not depend on git state.

        check_released_revision_ids resolves merge-base(ref, HEAD) before it
        reads any ids. CI checks out shallow and has no origin/main, so an
        unpinned _baseline_commit returns None there, the check short-circuits
        to "skip", and every stubbed expectation below silently passes for the
        wrong reason. Tests that exercise _baseline_commit itself override this.
        """
        monkeypatch.setattr(rules, "_baseline_commit", lambda ref: "baseline_sha")

    def _tree(self, tmp_path: Path, ids: dict[str, str]) -> Path:
        for name, rev in ids.items():
            _write_migration(
                tmp_path,
                f"""
                revision = "{rev}"
                down_revision = None

                def upgrade():
                    pass
                """,
                name=name,
            )
        return tmp_path

    @staticmethod
    def _baseline(**ids: str):
        """Stub for _revision_ids_at_ref: revision id -> path."""
        return lambda ref: dict(ids)

    def test_revision_id_parsed_from_annotated_assignment(self):
        source = 'revision: str = "abc123"\ndown_revision: str | None = None\n'
        assert rules._revision_id_from_source(source) == "abc123"

    def test_revision_id_parsed_from_plain_assignment(self):
        assert rules._revision_id_from_source('revision = "abc123"\n') == "abc123"

    def test_revision_id_absent_returns_none(self):
        assert rules._revision_id_from_source("x = 1\n") is None

    def test_dynamic_revision_id_returns_none(self):
        """A computed id cannot be compared statically; do not guess."""
        assert rules._revision_id_from_source('revision = "a" + "b"\n') is None

    def test_missing_baseline_id_is_reported(self, tmp_path: Path, monkeypatch):
        tree = self._tree(tmp_path, {"new.py": "rev_b"})
        monkeypatch.setattr(
            rules, "_revision_ids_at_ref", self._baseline(rev_a="migrations/versions/old.py")
        )

        violations = rules.check_released_revision_ids(tree, "origin/main")

        assert len(violations) == 1
        assert violations[0].rule == "MIG003"
        assert "rev_a" in violations[0].message

    def test_renaming_the_file_but_keeping_the_id_passes(self, tmp_path: Path, monkeypatch):
        """Filenames are cosmetic to Alembic; only the id is history."""
        tree = self._tree(tmp_path, {"renamed_to_something_else.py": "rev_a"})
        monkeypatch.setattr(
            rules, "_revision_ids_at_ref", self._baseline(rev_a="migrations/versions/old.py")
        )

        assert rules.check_released_revision_ids(tree, "origin/main") == []

    def test_adding_new_ids_passes(self, tmp_path: Path, monkeypatch):
        tree = self._tree(tmp_path, {"old.py": "rev_a", "new.py": "rev_b"})
        monkeypatch.setattr(
            rules, "_revision_ids_at_ref", self._baseline(rev_a="migrations/versions/old.py")
        )

        assert rules.check_released_revision_ids(tree, "origin/main") == []

    def test_retired_id_allowlist_suppresses_the_violation(self, tmp_path: Path, monkeypatch):
        tree = self._tree(tmp_path, {"new.py": "rev_b"})
        monkeypatch.setattr(
            rules, "_revision_ids_at_ref", self._baseline(rev_a="migrations/versions/old.py")
        )
        monkeypatch.setattr(rules, "RETIRED_REVISION_IDS", frozenset({"rev_a"}))

        assert rules.check_released_revision_ids(tree, "origin/main") == []

    def test_unreadable_baseline_skips_instead_of_failing(self, tmp_path: Path, monkeypatch):
        """A shallow clone or unfetched remote must not manufacture a failure."""
        tree = self._tree(tmp_path, {"new.py": "rev_b"})
        monkeypatch.setattr(rules, "_revision_ids_at_ref", lambda ref: None)

        assert rules.check_released_revision_ids(tree, "origin/main") == []

    def test_empty_baseline_skips(self, tmp_path: Path, monkeypatch):
        tree = self._tree(tmp_path, {"new.py": "rev_b"})
        monkeypatch.setattr(rules, "_revision_ids_at_ref", lambda ref: {})

        assert rules.check_released_revision_ids(tree, "origin/main") == []

    def test_unresolvable_baseline_commit_skips(self, tmp_path: Path, monkeypatch):
        tree = self._tree(tmp_path, {"new.py": "rev_b"})
        monkeypatch.setattr(rules, "_baseline_commit", lambda ref: None)

        assert rules.check_released_revision_ids(tree, "origin/main") == []

    def test_baseline_is_the_merge_base_not_the_branch_tip(self, tmp_path: Path, monkeypatch):
        """A branch that merely lags behind main must not be failed.

        Comparing against the tip of origin/main flags every migration merged
        after the fork point as "missing", and the obvious way to silence that
        is to add the id to RETIRED_REVISION_IDS -- exactly the mistake MIG003
        exists to prevent. Verified by asserting the ids come from the
        merge-base commit, not the ref.
        """
        tree = self._tree(tmp_path, {"forked.py": "rev_at_fork"})
        asked_for: list[str] = []

        def fake_ids(ref):
            asked_for.append(ref)
            # merge-base has only the forked id; the tip also has a later one.
            return (
                {"rev_at_fork": "m1.py"}
                if ref == "fork_sha"
                else {"rev_at_fork": "m1.py", "rev_merged_after_fork": "m2.py"}
            )

        monkeypatch.setattr(rules, "_baseline_commit", lambda ref: "fork_sha")
        monkeypatch.setattr(rules, "_revision_ids_at_ref", fake_ids)

        assert rules.check_released_revision_ids(tree, "origin/main") == []
        assert asked_for == ["fork_sha"]


class TestMig003AgainstRealMigrations:
    """MIG003 on the committed tree, using git for real rather than stubs."""

    def test_committed_tree_is_clean(self):
        violations = rules.check_released_revision_ids(rules.DEFAULT_VERSIONS_DIR)
        assert (
            violations == []
        ), "Committed migrations dropped a released revision id:\n  " + "\n  ".join(
            v.format() for v in violations
        )

    def test_removing_a_released_migration_is_caught(self, tmp_path: Path):
        """Copy the real tree, delete a migration that predates this branch."""
        import shutil

        victim = "20260731_004_add_proxy_token_terminated_fields.py"
        source = rules.DEFAULT_VERSIONS_DIR / victim
        if not source.exists():
            pytest.skip(f"{victim} not present")

        tree = tmp_path / "versions"
        shutil.copytree(rules.DEFAULT_VERSIONS_DIR, tree)
        (tree / victim).unlink()

        violations = rules.check_released_revision_ids(tree)

        if not violations:
            pytest.skip("no git baseline available in this checkout")
        assert any(
            "20260731_004_add_proxy_token_terminated_fields" in v.message for v in violations
        )

    def test_renaming_only_the_file_is_allowed(self, tmp_path: Path):
        """Alembic keys off the id; the filename is cosmetic."""
        import shutil

        victim = "20260731_004_add_proxy_token_terminated_fields.py"
        source = rules.DEFAULT_VERSIONS_DIR / victim
        if not source.exists():
            pytest.skip(f"{victim} not present")

        tree = tmp_path / "versions"
        shutil.copytree(rules.DEFAULT_VERSIONS_DIR, tree)
        (tree / victim).rename(tree / "zzz_renamed_file.py")

        assert rules.check_released_revision_ids(tree) == []
