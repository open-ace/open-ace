"""Integration tests for tenant-scoped project APIs."""

from __future__ import annotations

from flask import Flask

import app.routes.projects as projects_module
from app.repositories.project_repo import ProjectRepository
from app.repositories.user_repo import UserRepository


def _ensure_tenant(tmp_db, tenant_id: int) -> None:
    tmp_db.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug, quota) VALUES (?, ?, ?, ?)",
        (tenant_id, f"Tenant {tenant_id}", f"tenant-{tenant_id}", "{}"),
    )


def _insert_user(tmp_db, username: str, tenant_id: int, role: str = "user") -> int:
    _ensure_tenant(tmp_db, tenant_id)
    cursor = tmp_db.execute(
        """
        INSERT INTO users (username, email, password_hash, role, tenant_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (username, f"{username}@example.com", "hashed_pw", role, tenant_id),
    )
    return int(cursor.lastrowid)


def _login_as(monkeypatch, user_id: int) -> None:
    monkeypatch.setattr(projects_module, "_extract_token", lambda: "session-token")
    monkeypatch.setattr(projects_module, "_load_user_from_token", lambda token: {"id": user_id})


def _make_app(tmp_db, monkeypatch) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(projects_module.projects_bp, url_prefix="/api")
    monkeypatch.setattr(projects_module, "project_repo", ProjectRepository(db=tmp_db))
    monkeypatch.setattr(projects_module, "user_repo", UserRepository(db=tmp_db))
    return app


def test_projects_list_excludes_other_tenant_shared_projects(tmp_db, monkeypatch):
    app = _make_app(tmp_db, monkeypatch)
    repo = ProjectRepository(db=tmp_db)

    tenant_one_user = _insert_user(tmp_db, "tenant_one_user", tenant_id=1)
    tenant_two_user = _insert_user(tmp_db, "tenant_two_user", tenant_id=2)

    own_project_id = repo.create_project(
        path="/projects/tenant-one-visible",
        name="Tenant One Visible",
        created_by=tenant_one_user,
        is_shared=True,
        tenant_id=1,
    )
    repo.create_project(
        path="/projects/tenant-two-hidden",
        name="Tenant Two Hidden",
        created_by=tenant_two_user,
        is_shared=True,
        tenant_id=2,
    )

    _login_as(monkeypatch, tenant_one_user)
    client = app.test_client()
    response = client.get("/api/projects")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert [project["id"] for project in payload["projects"]] == [own_project_id]


def test_project_detail_returns_404_for_other_tenant_project(tmp_db, monkeypatch):
    app = _make_app(tmp_db, monkeypatch)
    repo = ProjectRepository(db=tmp_db)

    tenant_one_user = _insert_user(tmp_db, "tenant_one_reader", tenant_id=1)
    tenant_two_user = _insert_user(tmp_db, "tenant_two_owner", tenant_id=2)

    foreign_project_id = repo.create_project(
        path="/projects/tenant-two-secret",
        name="Tenant Two Secret",
        created_by=tenant_two_user,
        tenant_id=2,
    )

    _login_as(monkeypatch, tenant_one_user)
    client = app.test_client()
    response = client.get(f"/api/projects/{foreign_project_id}")

    assert response.status_code == 404
    assert response.get_json()["error"] == "Project not found"
