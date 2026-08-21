"""Regression: unit tests must not share a polluted ``app.db`` (#2869).

The full-suite CI lane runs many test files in one pytest process. Any test that
goes through ``create_app`` replays the authoritative schema onto whatever DB
``DATABASE_URL`` resolves to. Before the per-test isolation fixture, that was a
single workspace-level ``app.db`` shared across the whole session; a table left
by an earlier test with a column shape that doesn't match the snapshot made a
later ``CREATE INDEX`` raise ``no such column``, crashing an unrelated test's
setup at random (``test_tenant_keywords_api`` was the first casualty).

These tests (a) prove the pollution failure mode is real and (b) prove the
``tests/unit`` autouse isolation fixture shields ``create_app`` from it.
"""

import os
import sqlite3

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2869)]


def test_isolation_fixture_points_at_a_private_sqlite_db():
    """The autouse fixture makes the test DB choice explicit and per-test.

    Not a silent inheritance of the developer's ambient ``DATABASE_URL`` — the
    URL is a throwaway sqlite file under this test's own tmp dir (#2869 AC:
    "不引入对开发机 DATABASE_URL 的静默依赖").
    """
    url = os.environ.get("DATABASE_URL", "")
    assert url.startswith("sqlite:///")
    assert url.endswith("/unit-test.db")


def test_shared_db_pollution_does_not_reach_isolated_create_app(tmp_path, monkeypatch):
    """A mismatched-shape table must not break an isolated ``create_app``.

    Reproduces the exact cross-session failure, then shows a fresh per-test DB
    (what the conftest fixture hands every unit test) bootstraps cleanly even
    while the polluted DB still exists on disk.
    """
    from app import create_app
    from app.repositories.schema_init import ensure_all_tables

    # Pollute a DB the way an earlier session did: ``tenant_quotas`` exists but
    # is missing ``tenant_id``, so the authoritative schema's
    # ``CREATE UNIQUE INDEX ... ON tenant_quotas (tenant_id)`` raises. This table
    # is NOT in ``_backfill_missing_columns`` hardcoded list, so the backfill
    # can't silently paper over it — the crash is deterministic.
    polluted = tmp_path / "polluted.db"
    conn = sqlite3.connect(str(polluted))
    conn.execute("CREATE TABLE tenant_quotas (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    # (1) Reproduce #2869: pointed at the polluted DB, schema bootstrap raises.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{polluted}")
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        ensure_all_tables()

    # (2) The fix: a fresh isolated DB — exactly what the autouse fixture hands
    #     each test — bootstraps create_app cleanly, unaffected by the pollution.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/isolated.db")
    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret-key"})
    assert app is not None


def test_create_app_uses_the_isolated_db_not_the_shared_default(client):
    """Going through the shared ``client`` fixture must land on the isolated DB.

    The ``client`` fixture builds ``create_app`` via the ``app`` fixture, which
    depends on the isolation fixture; a healthy client proves ``create_app``
    bootstrapped its schema on the per-test DB without tripping over any shared
    state. (401 without auth confirms the app is wired and serving.)
    """
    assert os.environ["DATABASE_URL"].endswith("/unit-test.db")
    response = client.get("/api/tenants/1/sensitive-keywords")
    assert response.status_code in (401, 403)
