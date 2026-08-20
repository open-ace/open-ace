"""Integration tests for project name validation in project APIs (Issue #2897)."""

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


def test_create_project_rejects_xss_in_name(tmp_db, monkeypatch):
    """POST /api/projects with XSS in name should return 400."""
    app = _make_app(tmp_db, monkeypatch)
    user_id = _insert_user(tmp_db, "testuser", tenant_id=1)
    _login_as(monkeypatch, user_id)

    client = app.test_client()
    response = client.post(
        "/api/projects",
        json={
            "path": "/projects/test-xss",
            "name": "<script>alert('xss')</script>",
        },
    )

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_create_project_rejects_path_separator_in_name(tmp_db, monkeypatch):
    """POST /api/projects with path separator in name should return 400."""
    app = _make_app(tmp_db, monkeypatch)
    user_id = _insert_user(tmp_db, "testuser", tenant_id=1)
    _login_as(monkeypatch, user_id)

    client = app.test_client()
    response = client.post(
        "/api/projects",
        json={
            "path": "/projects/test-path",
            "name": "project/../../../etc/passwd",
        },
    )

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_create_project_rejects_whitespace_only_name(tmp_db, monkeypatch):
    """POST /api/projects with whitespace-only name should return 400."""
    app = _make_app(tmp_db, monkeypatch)
    user_id = _insert_user(tmp_db, "testuser", tenant_id=1)
    _login_as(monkeypatch, user_id)

    client = app.test_client()
    response = client.post(
        "/api/projects",
        json={
            "path": "/projects/test-ws",
            "name": "   ",
        },
    )

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_create_project_accepts_valid_name(tmp_db, monkeypatch):
    """POST /api/projects with a valid name should not return 400 for name reasons."""
    app = _make_app(tmp_db, monkeypatch)
    user_id = _insert_user(tmp_db, "testuser", tenant_id=1)
    _login_as(monkeypatch, user_id)

    client = app.test_client()
    response = client.post(
        "/api/projects",
        json={
            "path": "/projects/test-valid",
            "name": "My Project 2024",
        },
    )

    # 400 would only come from name validation failure; other status codes
    # (e.g. 409 for duplicate) are acceptable — we only assert it's not a
    # name-validation 400.
    if response.status_code == 400:
        assert "letters, numbers" not in response.get_json().get("error", "")


def test_update_project_rejects_xss_in_name(tmp_db, monkeypatch):
    """PUT /api/projects/{id} with XSS in name should return 400."""
    app = _make_app(tmp_db, monkeypatch)
    repo = ProjectRepository(db=tmp_db)
    user_id = _insert_user(tmp_db, "testuser", tenant_id=1)
    _login_as(monkeypatch, user_id)

    project_id = repo.create_project(
        path="/projects/test-update",
        name="Original Name",
        created_by=user_id,
        tenant_id=1,
    )

    client = app.test_client()
    response = client.put(
        f"/api/projects/{project_id}",
        json={"name": "<img src=x onerror=alert('xss')>"},
    )

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_update_project_rejects_path_separator_in_name(tmp_db, monkeypatch):
    """PUT /api/projects/{id} with path separator in name should return 400."""
    app = _make_app(tmp_db, monkeypatch)
    repo = ProjectRepository(db=tmp_db)
    user_id = _insert_user(tmp_db, "testuser", tenant_id=1)
    _login_as(monkeypatch, user_id)

    project_id = repo.create_project(
        path="/projects/test-update-path",
        name="Original Name",
        created_by=user_id,
        tenant_id=1,
    )

    client = app.test_client()
    response = client.put(
        f"/api/projects/{project_id}",
        json={"name": "project/backslash\\name"},
    )

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_update_project_rejects_whitespace_only_name(tmp_db, monkeypatch):
    """PUT /api/projects/{id} with whitespace-only name should return 400."""
    app = _make_app(tmp_db, monkeypatch)
    repo = ProjectRepository(db=tmp_db)
    user_id = _insert_user(tmp_db, "testuser", tenant_id=1)
    _login_as(monkeypatch, user_id)

    project_id = repo.create_project(
        path="/projects/test-update-ws",
        name="Original Name",
        created_by=user_id,
        tenant_id=1,
    )

    client = app.test_client()
    response = client.put(
        f"/api/projects/{project_id}",
        json={"name": "   "},
    )

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_update_project_accepts_none_name(tmp_db, monkeypatch):
    """PUT /api/projects/{id} without name field should not trigger name validation."""
    app = _make_app(tmp_db, monkeypatch)
    repo = ProjectRepository(db=tmp_db)
    user_id = _insert_user(tmp_db, "testuser", tenant_id=1)
    _login_as(monkeypatch, user_id)

    project_id = repo.create_project(
        path="/projects/test-update-none",
        name="Original Name",
        created_by=user_id,
        tenant_id=1,
    )

    client = app.test_client()
    response = client.put(
        f"/api/projects/{project_id}",
        json={"description": "Updated description only"},
    )

    # Should not be 400 from name validation
    if response.status_code == 400:
        assert "letters, numbers" not in response.get_json().get("error", "")
