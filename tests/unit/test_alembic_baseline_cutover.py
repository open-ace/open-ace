#!/usr/bin/env python3
"""Tests for legacy-to-baseline Alembic cutover."""

from __future__ import annotations

import sqlite3

import sqlalchemy as sa

from migrations.baseline import BASELINE_REVISION, read_current_revision
from scripts.cutover_alembic_baseline import cutover_database


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
                row[1]
                for row in connection.execute(sa.text("PRAGMA table_info(users)")).fetchall()
            }
            session_columns = {
                row[1]
                for row in connection.execute(sa.text("PRAGMA table_info(session_messages)")).fetchall()
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


def test_cutover_skips_active_revision(tmp_path):
    db_path = tmp_path / "active.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL
        );
        CREATE TABLE alembic_version (
            version_num TEXT PRIMARY KEY
        );
        INSERT INTO alembic_version(version_num) VALUES ('{BASELINE_REVISION}');
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
    assert actions == [f"already on active revision {BASELINE_REVISION}"]
