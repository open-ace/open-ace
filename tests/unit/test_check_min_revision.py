#!/usr/bin/env python3
"""Tests for the minimum-supported-revision guard (Issue #1215)."""

from __future__ import annotations

import sqlite3
import sys

import pytest

from migrations.baseline import ACTIVE_MIGRATIONS_DIR, BASELINE_REVISION
from scripts.check_min_revision import collect_active_revision_ids, is_supported_revision


@pytest.fixture(autouse=True)
def _clean_argv(monkeypatch):
    """main() parses sys.argv; strip pytest's args so argparse doesn't choke."""
    monkeypatch.setattr(sys, "argv", ["check_min_revision.py"])


@pytest.fixture(scope="session")
def post_baseline_revision() -> str:
    """A real post-baseline revision, derived at runtime from the live migrations dir.

    Avoids hard-coding (and then maintaining) a head revision constant that
    drifts every time a new migration lands.
    """
    revisions = collect_active_revision_ids()
    assert revisions, "expected at least one post-baseline migration"
    return next(iter(revisions))


def _stamp_sqlite(db_path, revision: str | None) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE alembic_version (
            version_num TEXT PRIMARY KEY
        )
        """
    )
    if revision is not None:
        conn.execute("INSERT INTO alembic_version(version_num) VALUES (?)", (revision,))
    conn.commit()
    conn.close()


def test_collect_active_revision_ids_is_non_empty_and_excludes_baseline():
    """The allowlist is derived from the live migrations dir."""
    active_ids = collect_active_revision_ids()
    assert active_ids  # post-baseline lineage exists
    # The baseline pins its revision to a symbol, so it is unioned in by the
    # caller rather than surfaced by the collector.
    assert BASELINE_REVISION not in active_ids


def test_collect_covers_every_non_baseline_migration():
    """Completeness invariant: every migration file must contribute a literal id.

    The baseline migration pins ``revision`` to the ``BASELINE_REVISION`` symbol,
    so the regex collector intentionally skips it (the caller unions it back in).
    Every *other* migration must use a literal revision id; if a future
    post-baseline migration switches to a symbol/constant assignment, the
    collector would silently drop it and the guard would wrongly reject a DB
    stamped on that revision. This assertion surfaces such a regression in CI.
    """
    # Reuse the collector's own source (absolute, __file__-derived) so the
    # assertion is cwd-independent rather than relying on pytest's working dir.
    migration_files = list(ACTIVE_MIGRATIONS_DIR.glob("*.py"))
    # One file (baseline_2026_06_23.py) uses a symbol binding; all others must
    # contribute a literal revision id.
    assert len(collect_active_revision_ids()) == len(migration_files) - 1


def test_is_supported_revision_accepts_baseline_and_successors(post_baseline_revision):
    supported = collect_active_revision_ids() | {BASELINE_REVISION}
    assert is_supported_revision(BASELINE_REVISION, supported) is True
    assert is_supported_revision(post_baseline_revision, supported) is True


def test_is_supported_revision_rejects_pre_baseline_hash():
    supported = collect_active_revision_ids() | {BASELINE_REVISION}
    assert is_supported_revision("7bcf07ee658e", supported) is False


def test_is_supported_revision_rejects_none_and_unknown():
    supported = collect_active_revision_ids() | {BASELINE_REVISION}
    assert is_supported_revision(None, supported) is False
    assert is_supported_revision("some_unknown_revision", supported) is False


def test_main_allows_baseline_revision(tmp_path, monkeypatch, capsys):
    import scripts.check_min_revision as mod

    db_path = tmp_path / "baseline.db"
    _stamp_sqlite(db_path, BASELINE_REVISION)
    monkeypatch.setattr(mod, "_get_db_url", lambda: f"sqlite:///{db_path}")

    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "supported lineage" in out


def test_main_allows_post_baseline_revision(tmp_path, monkeypatch, capsys, post_baseline_revision):
    import scripts.check_min_revision as mod

    db_path = tmp_path / "post_baseline.db"
    _stamp_sqlite(db_path, post_baseline_revision)
    monkeypatch.setattr(mod, "_get_db_url", lambda: f"sqlite:///{db_path}")

    rc = mod.main()
    assert rc == 0


def test_main_rejects_pre_baseline_revision(tmp_path, monkeypatch, capsys):
    import scripts.check_min_revision as mod

    db_path = tmp_path / "legacy.db"
    _stamp_sqlite(db_path, "7bcf07ee658e")
    monkeypatch.setattr(mod, "_get_db_url", lambda: f"sqlite:///{db_path}")

    rc = mod.main()
    err = capsys.readouterr().err
    assert rc == 1
    assert BASELINE_REVISION in err
    assert "below the minimum" in err


def test_main_allows_fresh_database_without_version_table(tmp_path, monkeypatch, capsys):
    import scripts.check_min_revision as mod

    db_path = tmp_path / "fresh.db"
    # Create an empty database with no alembic_version table.
    sqlite3.connect(db_path).close()
    monkeypatch.setattr(mod, "_get_db_url", lambda: f"sqlite:///{db_path}")

    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Fresh database" in out


def test_main_rejects_empty_version_table(tmp_path, monkeypatch, capsys):
    """A version table with no rows reports a dedicated message (not 'None')."""
    import scripts.check_min_revision as mod

    db_path = tmp_path / "empty_rows.db"
    _stamp_sqlite(db_path, None)
    monkeypatch.setattr(mod, "_get_db_url", lambda: f"sqlite:///{db_path}")

    rc = mod.main()
    err = capsys.readouterr().err
    assert rc == 1
    assert "no revision row" in err
