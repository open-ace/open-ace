"""Integration tests for tenant-scoped governance audit APIs."""

from __future__ import annotations

from flask import Flask

import app.auth.decorators as auth_module
import app.routes.governance as governance_module
from app.modules.governance.audit_logger import AuditLogger


def _ensure_tenant(tmp_db, tenant_id: int) -> None:
    tmp_db.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug, quota) VALUES (?, ?, ?, ?)",
        (tenant_id, f"Tenant {tenant_id}", f"tenant-{tenant_id}", "{}"),
    )


def _insert_admin(tmp_db, username: str, tenant_id: int) -> int:
    _ensure_tenant(tmp_db, tenant_id)
    cursor = tmp_db.execute(
        """
        INSERT INTO users (username, email, password_hash, role, tenant_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (username, f"{username}@example.com", "hashed_pw", "admin", tenant_id),
    )
    return int(cursor.lastrowid)


def _login_as(monkeypatch, user_id: int, tenant_id: int, username: str) -> None:
    monkeypatch.setattr(auth_module, "_extract_token", lambda: "session-token")
    monkeypatch.setattr(
        auth_module,
        "_load_user_from_token",
        lambda token: {"id": user_id, "username": username, "role": "admin", "tenant_id": tenant_id},
    )


def _make_app(tmp_db, monkeypatch) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(governance_module.governance_bp, url_prefix="/api")
    monkeypatch.setattr(governance_module, "audit_logger", AuditLogger(db=tmp_db))
    return app


def test_audit_logs_route_returns_only_current_tenant_records(tmp_db, monkeypatch):
    tenant_one_admin = _insert_admin(tmp_db, "tenant_one_admin", tenant_id=1)
    tenant_two_admin = _insert_admin(tmp_db, "tenant_two_admin", tenant_id=2)

    audit_logger = AuditLogger(db=tmp_db)
    audit_logger.log(action="login", user_id=tenant_one_admin, username="tenant_one_admin")
    audit_logger.log(action="login_failed", user_id=tenant_two_admin, username="tenant_two_admin")

    app = _make_app(tmp_db, monkeypatch)
    _login_as(monkeypatch, tenant_one_admin, tenant_id=1, username="tenant_one_admin")
    client = app.test_client()

    response = client.get("/api/audit/logs")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 1
    assert [row["username"] for row in payload["logs"]] == ["tenant_one_admin"]
