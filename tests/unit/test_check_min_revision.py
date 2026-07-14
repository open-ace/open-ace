#!/usr/bin/env python3
"""Tests for the minimum-supported-revision guard (Issue #1215)."""

from __future__ import annotations

import sqlite3
import sys

import pytest

from migrations.baseline import BASELINE_REVISION, HEAD_REVISION
from scripts.check_min_revision import collect_active_revision_ids, is_supported_revision


@pytest.fixture(autouse=True)
def _clean_argv(monkeypatch):
    """main() parses sys.argv; strip pytest's args so argparse doesn't choke."""
    monkeypatch.setattr(sys, "argv", ["check_min_revision.py"])


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


def test_collect_active_revision_ids_includes_post_baseline_head():
    """The allowlist is derived from the live migrations dir."""
    active_ids = collect_active_revision_ids()
    assert HEAD_REVISION in active_ids
    # The baseline pins its revision to a symbol, so it is unioned in by the
    # caller rather than surfaced by the collector.
    assert BASELINE_REVISION not in active_ids


def test_is_supported_revision_accepts_baseline_and_successors():
    supported = collect_active_revision_ids() | {BASELINE_REVISION}
    assert is_supported_revision(BASELINE_REVISION, supported) is True
    assert is_supported_revision(HEAD_REVISION, supported) is True


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


def test_main_allows_head_revision(tmp_path, monkeypatch, capsys):
    import scripts.check_min_revision as mod

    db_path = tmp_path / "head.db"
    _stamp_sqlite(db_path, HEAD_REVISION)
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
    """A version table with no rows is treated as unsupported, not fresh."""
    import scripts.check_min_revision as mod

    db_path = tmp_path / "empty_rows.db"
    _stamp_sqlite(db_path, None)
    monkeypatch.setattr(mod, "_get_db_url", lambda: f"sqlite:///{db_path}")

    rc = mod.main()
    assert rc == 1
