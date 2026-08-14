"""Regression tests for the forward repair of misclassified initial admins."""

from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration, pytest.mark.issue(2661)]


@pytest.fixture
def connection():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(sa.text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                tenant_id INTEGER
            )
        """))
    with engine.connect() as conn:
        yield conn
    engine.dispose()


@pytest.fixture
def migration():
    return importlib.import_module("migrations.versions.20260814_003_repair_initial_platform_admin")


def _insert_user(connection, user_id: int, username: str, role: str, tenant_id: int | None):
    connection.execute(
        sa.text("""
            INSERT INTO users (id, username, role, tenant_id)
            VALUES (:id, :username, :role, :tenant_id)
        """),
        {"id": user_id, "username": username, "role": role, "tenant_id": tenant_id},
    )
    connection.commit()


def _role(connection, user_id: int) -> str:
    return connection.execute(
        sa.text("SELECT role FROM users WHERE id = :id"), {"id": user_id}
    ).scalar_one()


def test_repairs_initial_user_misclassified_as_tenant_admin(connection, migration):
    _insert_user(connection, 1, "initial", "tenant_admin", 1)

    repaired = migration._repair_misclassified_initial_admins(connection, [])
    connection.commit()

    assert repaired == 1
    assert _role(connection, 1) == "platform_admin"


def test_does_not_promote_ordinary_tenant_admin(connection, migration):
    _insert_user(connection, 2, "tenant-owner", "tenant_admin", 1)

    repaired = migration._repair_misclassified_initial_admins(connection, [])
    connection.commit()

    assert repaired == 0
    assert _role(connection, 2) == "tenant_admin"


def test_repairs_explicitly_configured_initial_admin(connection, migration):
    _insert_user(connection, 2, "known-initial", "tenant_admin", 1)

    repaired = migration._repair_misclassified_initial_admins(connection, ["known-initial"])
    connection.commit()

    assert repaired == 1
    assert _role(connection, 2) == "platform_admin"


def test_is_noop_for_already_correct_platform_admin(connection, migration):
    _insert_user(connection, 1, "initial", "platform_admin", 1)

    repaired = migration._repair_misclassified_initial_admins(connection, [])
    connection.commit()

    assert repaired == 0
    assert _role(connection, 1) == "platform_admin"


def test_reads_trimmed_nonempty_whitelist(monkeypatch, migration):
    monkeypatch.setenv("OPENACE_INITIAL_PLATFORM_ADMINS", " first, ,second ")

    assert migration._get_initial_platform_admins() == ["first", "second"]
