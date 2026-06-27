#!/usr/bin/env python3
"""Tests for legacy-to-baseline Alembic cutover."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import time
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

from migrations.baseline import BASELINE_REVISION, HEAD_REVISION, read_current_revision
from migrations.version_table import VERSION_NUM_LENGTH
from scripts.cutover_alembic_baseline import collect_active_revision_ids, cutover_database


def _postgres_server_binaries_available() -> bool:
    if not all(
        shutil.which(binary) is not None for binary in ("initdb", "pg_ctl", "createdb", "psql")
    ):
        return False
    # initdb and Postgres peer auth require the current uid to resolve to a real
    # OS user. In restricted sandboxes the running uid may have no passwd entry,
    # which makes initdb fail at runtime ("could not look up effective user ID").
    import pwd

    try:
        pwd.getpwuid(os.getuid())
    except KeyError:
        return False
    return True


@contextmanager
def _temporary_postgres_database(tmp_path):
    if not _postgres_server_binaries_available():
        pytest.skip("PostgreSQL server binaries are not usable in this environment")

    cluster_dir = tmp_path / "pg-cluster"
    log_path = tmp_path / "postgres.log"
    port = "55435"
    env = os.environ.copy()
    env.update({"LC_ALL": "C", "LANG": "C"})

    subprocess.run(
        ["initdb", "-D", str(cluster_dir), "-A", "trust", "--username=postgres"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    subprocess.run(
        ["pg_ctl", "-D", str(cluster_dir), "-l", str(log_path), "-o", f"-p {port}", "start"],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        for _ in range(30):
            result = subprocess.run(
                [
                    "psql",
                    "-h",
                    "127.0.0.1",
                    "-p",
                    port,
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                    "-c",
                    "SELECT 1",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("temporary PostgreSQL server did not become ready")

        db_name = "cutover_test"
        subprocess.run(
            ["createdb", "-h", "127.0.0.1", "-p", port, "-U", "postgres", db_name],
            check=True,
            capture_output=True,
            text=True,
        )
        yield f"postgresql://postgres@127.0.0.1:{port}/{db_name}"
    finally:
        subprocess.run(
            ["pg_ctl", "-D", str(cluster_dir), "-m", "fast", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )


def test_cutover_stamps_legacy_sqlite_and_backfills_latest_baseline_artifacts(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            linux_account TEXT
        );
        CREATE TABLE agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL
        );
        CREATE TABLE session_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL
        );
        CREATE TABLE alembic_version (
            version_num TEXT PRIMARY KEY
        );
        INSERT INTO users(username, linux_account) VALUES ('tester', 'svc-openace');
        INSERT INTO alembic_version(version_num) VALUES ('7bcf07ee658e');
        """
    )
    conn.commit()
    conn.close()

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            changed, actions = cutover_database(connection)
            revision = read_current_revision(connection)
            has_compliance = connection.execute(
                sa.text(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'compliance_reports'
                    """
                )
            ).scalar()
            has_mapping_rules = connection.execute(
                sa.text(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'tool_account_mapping_rules'
                    """
                )
            ).scalar()
            user_columns = {
                row[1] for row in connection.execute(sa.text("PRAGMA table_info(users)")).fetchall()
            }
            session_columns = {
                row[1]
                for row in connection.execute(
                    sa.text("PRAGMA table_info(session_messages)")
                ).fetchall()
            }
    finally:
        engine.dispose()

    assert changed is True
    assert any(action.startswith("created compliance_reports") for action in actions)
    assert any(action.startswith("created tool_account_mapping_rules") for action in actions)
    assert any(action.startswith("backfilled users.system_account") for action in actions)
    assert any(action.startswith("backfilled session_messages.source") for action in actions)
    assert any(action.startswith("backfilled users.auto_mapping_enabled") for action in actions)
    assert revision == BASELINE_REVISION
    assert has_compliance == 1
    assert has_mapping_rules == 1
    assert "system_account" in user_columns
    assert "auto_mapping_enabled" in user_columns
    assert "source" in session_columns


@pytest.mark.parametrize("revision", [BASELINE_REVISION, HEAD_REVISION])
def test_cutover_skips_active_revision_when_schema_complete(tmp_path, revision):
    """A DB already on baseline or any post-baseline revision is a no-op."""
    db_path = tmp_path / "active.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            system_account TEXT,
            auto_mapping_enabled INTEGER DEFAULT 1
        );
        CREATE TABLE agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            cli_session_id TEXT DEFAULT ''
        );
        CREATE TABLE session_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            metadata TEXT,
            milestone_id TEXT DEFAULT '',
            source TEXT DEFAULT '',
            source_timestamp TIMESTAMP,
            external_message_id TEXT DEFAULT '',
            content_blocks TEXT
        );
        CREATE INDEX idx_session_messages_external_message_id
        ON session_messages(session_id, external_message_id);
        CREATE INDEX idx_session_messages_source
        ON session_messages(session_id, source);
        CREATE TABLE tool_account_mapping_rules (id INTEGER PRIMARY KEY);
        CREATE TABLE compliance_reports (id INTEGER PRIMARY KEY);
        CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY);
        INSERT INTO alembic_version(version_num) VALUES ('{revision}');
        """
    )
    conn.commit()
    conn.close()

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            changed, actions = cutover_database(connection)
    finally:
        engine.dispose()

    assert changed is False
    assert actions == [f"already on active revision {revision}"]


def test_cutover_active_revision_backfills_newly_absorbed_objects(tmp_path):
    """A DB stamped on an earlier baseline still gets objects absorbed later,
    since the baseline content can evolve without minting a new revision."""
    db_path = tmp_path / "active_legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            system_account TEXT,
            auto_mapping_enabled INTEGER DEFAULT 1
        );
        CREATE TABLE agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT
        );
        CREATE TABLE session_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            metadata TEXT,
            milestone_id TEXT DEFAULT '',
            source TEXT DEFAULT ''
        );
        CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY);
        INSERT INTO alembic_version(version_num) VALUES ('{BASELINE_REVISION}');
        """
    )
    conn.commit()
    conn.close()

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            changed, actions = cutover_database(connection)
            sm_cols = {
                row[1]
                for row in connection.execute(
                    sa.text("PRAGMA table_info(session_messages)")
                ).fetchall()
            }
            as_cols = {
                row[1]
                for row in connection.execute(
                    sa.text("PRAGMA table_info(agent_sessions)")
                ).fetchall()
            }
    finally:
        engine.dispose()

    assert changed is True
    assert any("cli_session_id" in a for a in actions)
    assert any("transcript" in a for a in actions)
    assert "external_message_id" in sm_cols
    assert "cli_session_id" in as_cols


def test_cutover_rejects_unknown_revision(tmp_path):
    db_path = tmp_path / "unknown.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL
        );
        CREATE TABLE alembic_version (
            version_num TEXT PRIMARY KEY
        );
        INSERT INTO alembic_version(version_num) VALUES ('unknown_revision');
        """
    )
    conn.commit()
    conn.close()

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            with pytest.raises(RuntimeError, match="unknown revision"):
                cutover_database(connection)
    finally:
        engine.dispose()


def test_collect_active_revision_ids_includes_post_baseline_lineage():
    """The active-lineage allowlist must cover post-baseline revisions.

    Regression guard for the failure where the cutover refusal guard hard-coded
    only the baseline revision: DBs already migrated to head were rejected.
    The collector scans the live migrations/versions directory, so new
    post-baseline migrations are picked up without editing the cutover script.
    """
    active_ids = collect_active_revision_ids()

    # The post-baseline head (added by the auto_provision_users fix) must be
    # recognised as an active revision so a head-stamped DB is a cutover no-op.
    assert HEAD_REVISION in active_ids
    # The baseline pins its revision to the BASELINE_REVISION symbol rather
    # than a literal, so the collector does not surface it; the caller unions
    # it in explicitly. Legacy ids must never leak into the active set.
    assert BASELINE_REVISION not in active_ids


def test_cutover_dry_run_reports_actions_without_mutating_schema(tmp_path):
    db_path = tmp_path / "dry-run.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            linux_account TEXT
        );
        CREATE TABLE agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL
        );
        CREATE TABLE session_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL
        );
        CREATE TABLE alembic_version (
            version_num TEXT PRIMARY KEY
        );
        INSERT INTO alembic_version(version_num) VALUES ('7bcf07ee658e');
        """
    )
    conn.commit()
    conn.close()

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            changed, actions = cutover_database(connection, dry_run=True)
            revision = read_current_revision(connection)
            user_columns = {
                row[1] for row in connection.execute(sa.text("PRAGMA table_info(users)")).fetchall()
            }
            session_columns = {
                row[1]
                for row in connection.execute(
                    sa.text("PRAGMA table_info(session_messages)")
                ).fetchall()
            }
            has_compliance = connection.execute(
                sa.text(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'compliance_reports'
                    """
                )
            ).scalar()
    finally:
        engine.dispose()

    assert changed is True
    assert "would backfill users.system_account" in actions
    assert "would backfill session_messages.source" in actions
    assert "would backfill users.auto_mapping_enabled" in actions
    assert "would create tool_account_mapping_rules" in actions
    assert "would create compliance_reports" in actions
    assert f"would stamp {BASELINE_REVISION}" in actions
    assert revision == "7bcf07ee658e"
    assert "system_account" not in user_columns
    assert "auto_mapping_enabled" not in user_columns
    assert "source" not in session_columns
    assert has_compliance is None


def test_cutover_skips_empty_database_without_recognizable_schema(tmp_path):
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            changed, actions = cutover_database(connection)
    finally:
        engine.dispose()

    assert changed is False
    assert actions == ["database has no recognizable application schema; skipping cutover"]


def test_cutover_postgres_backfills_legacy_schema(tmp_path):
    with _temporary_postgres_database(tmp_path) as database_url:
        engine = sa.create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    """
                    CREATE TABLE users (
                        id SERIAL PRIMARY KEY,
                        username TEXT NOT NULL,
                        linux_account TEXT
                    )
                    """
                )
                connection.exec_driver_sql(
                    """
                    CREATE TABLE agent_sessions (
                        id SERIAL PRIMARY KEY,
                        session_id TEXT NOT NULL
                    )
                    """
                )
                connection.exec_driver_sql(
                    """
                    CREATE TABLE session_messages (
                        id SERIAL PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL
                    )
                    """
                )
                for table_name in (
                    "agent_tokens",
                    "ai_agent_settings",
                    "autonomous_workflows",
                    "email_notification_logs",
                    "registration_tokens",
                    "smtp_settings",
                    "workflow_events",
                    "workflow_milestones",
                ):
                    connection.exec_driver_sql(f"CREATE TABLE {table_name} (id SERIAL PRIMARY KEY)")
                connection.exec_driver_sql(
                    """
                    CREATE TABLE alembic_version (
                        version_num VARCHAR(64) PRIMARY KEY
                    )
                    """
                )
                connection.execute(
                    sa.text("INSERT INTO alembic_version(version_num) VALUES ('7bcf07ee658e')")
                )

            with engine.begin() as connection:
                changed, actions = cutover_database(connection)
                revision = read_current_revision(connection)
                user_columns = {
                    row[0]
                    for row in connection.execute(
                        sa.text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'users'
                            """
                        )
                    ).fetchall()
                }
                session_columns = {
                    row[0]
                    for row in connection.execute(
                        sa.text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'session_messages'
                            """
                        )
                    ).fetchall()
                }
                has_mapping_rules = connection.execute(
                    sa.text(
                        """
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'tool_account_mapping_rules'
                        """
                    )
                ).scalar()
                has_compliance = connection.execute(
                    sa.text(
                        """
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'compliance_reports'
                        """
                    )
                ).scalar()
        finally:
            engine.dispose()

    assert changed is True
    assert any(action.startswith("backfilled users.system_account") for action in actions)
    assert any(action.startswith("backfilled session_messages.source") for action in actions)
    assert any(action.startswith("backfilled users.auto_mapping_enabled") for action in actions)
    assert any(action.startswith("created tool_account_mapping_rules") for action in actions)
    assert any(action.startswith("created compliance_reports") for action in actions)
    assert revision == BASELINE_REVISION
    assert "system_account" in user_columns
    assert "auto_mapping_enabled" in user_columns
    assert "source" in session_columns
    assert has_mapping_rules == 1
    assert has_compliance == 1


def test_cutover_postgres_widens_narrow_version_num_column(tmp_path):
    """A legacy varchar(32) alembic_version column must be widened to 64.

    Older databases created ``alembic_version.version_num`` as ``varchar(32)``
    (Alembic's historical default). The post-baseline lineage uses revision ids
    longer than 32 chars (e.g. ``20260626_001_add_run_timeline_tables``), which
    overflow the column and abort ``alembic upgrade head``. The cutover widens
    the column to ``VERSION_NUM_LENGTH`` even when the DB is already on an
    active revision, so this stamps head and asserts the column grows.
    """
    long_revision = "20260626_001_add_run_timeline_tables"
    with _temporary_postgres_database(tmp_path) as database_url:
        engine = sa.create_engine(database_url)
        try:
            with engine.begin() as connection:
                # Minimal schema so cutover's formal-table gate passes.
                for table_name in (
                    "agent_tokens",
                    "ai_agent_settings",
                    "autonomous_workflows",
                    "compliance_reports",
                    "email_notification_logs",
                    "registration_tokens",
                    "smtp_settings",
                    "tool_account_mapping_rules",
                    "workflow_events",
                    "workflow_milestones",
                ):
                    connection.exec_driver_sql(f"CREATE TABLE {table_name} (id SERIAL PRIMARY KEY)")
                # Legacy narrow version column stamped with a short revision.
                connection.exec_driver_sql(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
                connection.execute(
                    sa.text("INSERT INTO alembic_version(version_num) VALUES ('7bcf07ee658e')")
                )

            with engine.begin() as connection:
                changed, actions = cutover_database(connection)
                width = connection.execute(
                    sa.text(
                        """
                        SELECT character_maximum_length
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'alembic_version'
                          AND column_name = 'version_num'
                        """
                    )
                ).scalar()
                # Prove the column is now wide enough to hold a long
                # post-baseline revision (the exact failure this fixes).
                connection.execute(
                    sa.text(f"UPDATE alembic_version SET version_num = '{long_revision}'")
                )
                revision = read_current_revision(connection)
        finally:
            engine.dispose()

    assert any("widened alembic_version.version_num" in a for a in actions)
    assert changed is True
    assert width == VERSION_NUM_LENGTH
    # The widened column now holds a long revision id without truncation.
    assert revision == long_revision


def test_cutover_dry_run_reports_version_column_widening(tmp_path):
    """Dry-run must report the would-widen action for a narrow column."""
    with _temporary_postgres_database(tmp_path) as database_url:
        engine = sa.create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
                connection.execute(
                    sa.text("INSERT INTO alembic_version(version_num) VALUES ('7bcf07ee658e')")
                )
            with engine.begin() as connection:
                changed, actions = cutover_database(connection, dry_run=True)
                width = connection.execute(
                    sa.text(
                        """
                        SELECT character_maximum_length
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'alembic_version'
                          AND column_name = 'version_num'
                        """
                    )
                ).scalar()
        finally:
            engine.dispose()

    assert any("would widen alembic_version.version_num" in a for a in actions)
    assert changed is True
    # Dry-run must not mutate the schema.
    assert width == 32
