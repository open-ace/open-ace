from __future__ import annotations

from datetime import datetime

import pytest

from app.modules.governance.audit_logger import AuditLogger
from app.modules.workspace import session_manager as sm_mod
from app.modules.workspace.session_manager import SessionManager
from app.repositories.database import Database
from app.repositories.project_repo import ProjectRepository
from app.repositories.schema_init import load_schema_from_file
from app.repositories.usage_repo import UsageRepository


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    """SQLite database with the authoritative schema loaded."""
    monkeypatch.setattr(sm_mod, "is_postgresql", lambda: False)
    db_path = tmp_path / "tenant_boundaries.db"
    db_url = f"sqlite:///{db_path}"
    load_schema_from_file(db_url=db_url, dialect="sqlite")
    return db_path, Database(db_url=db_url)


def _session_manager(db_path) -> SessionManager:
    return SessionManager(db_path=str(db_path))


def _insert_user(db: Database, user_id: int, username: str, tenant_id: int) -> None:
    db.execute(
        """
        INSERT INTO users (id, username, email, password_hash, role, tenant_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            username,
            f"{username}@example.com",
            "hash",
            "user",
            tenant_id,
            datetime.utcnow().isoformat(),
        ),
    )


def test_session_updates_are_scoped_by_tenant(sqlite_db):
    """A tenant-scoped write must not mutate a session owned by another tenant."""
    db_path, _ = sqlite_db
    manager = _session_manager(db_path)

    manager.create_session("codex", session_id="tenant-one-session", tenant_id=1)
    manager.create_session("codex", session_id="tenant-two-session", tenant_id=2, title="Tenant 2")

    assert (
        manager.update_session_fields("tenant-two-session", {"title": "wrong tenant"}, tenant_id=1)
        is False
    )
    assert (
        manager.increment_session_usage(
            "tenant-two-session", request_delta=5, total_tokens_delta=500, tenant_id=1
        )
        is False
    )
    assert manager.get_session("tenant-two-session", tenant_id=2).title == "Tenant 2"
    assert manager.get_session("tenant-two-session", tenant_id=2).request_count == 0

    tenant_two = manager.get_session("tenant-two-session", tenant_id=2)
    assert tenant_two is not None
    tenant_two.title = "Tenant 2 updated"
    assert manager.update_session(tenant_two) is True

    assert manager.get_session("tenant-one-session", tenant_id=1) is not None
    assert manager.get_session("tenant-two-session", tenant_id=2).title == "Tenant 2 updated"


def test_session_delete_and_messages_are_scoped_by_tenant(sqlite_db):
    """Deleting or reading with the wrong tenant must not cross the tenant boundary."""
    db_path, db = sqlite_db
    manager = _session_manager(db_path)

    manager.create_session("codex", session_id="tenant-one-session", tenant_id=1)
    manager.create_session("codex", session_id="tenant-two-session", tenant_id=2)
    db.execute(
        """
        INSERT INTO session_messages (session_id, tenant_id, role, content, timestamp)
        VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?)
        """,
        (
            "tenant-one-session",
            1,
            "assistant",
            "tenant one",
            datetime.utcnow().isoformat(),
            "tenant-two-session",
            2,
            "assistant",
            "tenant two",
            datetime.utcnow().isoformat(),
        ),
    )

    assert manager.get_messages("tenant-two-session", tenant_id=1) == []
    assert manager.complete_session("tenant-two-session", tenant_id=1) is False
    assert manager.delete_session("tenant-two-session", tenant_id=1) is False
    assert manager.get_session("tenant-two-session", tenant_id=2) is not None

    assert manager.delete_session("tenant-one-session", tenant_id=1) is True

    assert manager.get_session("tenant-one-session", tenant_id=1) is None
    assert manager.get_session("tenant-two-session", tenant_id=2) is not None
    messages = manager.get_messages("tenant-two-session", tenant_id=2)
    assert [message.content for message in messages] == ["tenant two"]


def test_project_usage_and_audit_queries_are_tenant_scoped(sqlite_db):
    """Project, daily_usage, and audit_logs queries stay inside the requested tenant."""
    _, db = sqlite_db
    _insert_user(db, 101, "alice", 1)
    _insert_user(db, 202, "bob", 2)

    project_repo = ProjectRepository(db=db)
    project_repo.create_project("/workspace/shared", name="Tenant 1", created_by=101, tenant_id=1)
    project_repo.create_project("/workspace/shared", name="Tenant 2", created_by=202, tenant_id=2)

    assert project_repo.get_project_by_path("/workspace/shared", tenant_id=1).tenant_id == 1
    assert project_repo.get_project_by_path("/workspace/shared", tenant_id=2).tenant_id == 2
    assert {project.tenant_id for project in project_repo.get_all_projects(tenant_id=1)} == {1}

    usage_repo = UsageRepository(db=db)
    usage_repo.save_usage(
        "2026-07-18",
        "codex",
        tokens_used=100,
        request_count=2,
        host_name="host-a",
        tenant_id=1,
    )
    usage_repo.save_usage(
        "2026-07-18",
        "codex",
        tokens_used=900,
        request_count=9,
        host_name="host-b",
        tenant_id=2,
    )
    assert usage_repo.get_request_count_total("2026-07-18", "2026-07-18", tenant_id=1) == 2
    assert usage_repo.get_request_count_total("2026-07-18", "2026-07-18", tenant_id=2) == 9

    audit_logger = AuditLogger(db=db)
    assert audit_logger.log("login", user_id=101, username="alice", tenant_id=1) is True
    assert audit_logger.log("login", user_id=202, username="bob", tenant_id=2) is True
    assert [log.username for log in audit_logger.query(tenant_id=1)] == ["alice"]
    assert audit_logger.count(tenant_id=2) == 1
