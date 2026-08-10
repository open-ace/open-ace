#!/usr/bin/env python3
"""
Route tests for must-change-password enforcement on the blueprints that
authenticate via a custom ``@<bp>.before_request`` hook instead of the
``@auth_required`` / ``@admin_required`` decorators.

These blueprints historically bypassed ``enforce_password_change_requirement``
because that helper is only wired into the decorators in
``app/auth/decorators.py``. A user flagged with ``must_change_password=True``
could therefore keep using workspace, projects, system, alerts,
project-categories and fs endpoints.

Addresses review findings on #1752.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# One session dict returned by the mocked ``_authenticate`` (session-token
# path used by every before_request hook). ``must_change_password=True`` is
# the forced-password-change state that must be blocked.
MUST_CHANGE_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "must_change_password": True,
}

CLEAR_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "must_change_password": False,
}

# Full user row returned by user_repo.get_user_by_id() for the blueprints that
# re-fetch the user (projects / project_categories / fs). SELECT * includes
# must_change_password.
MUST_CHANGE_USER = {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "tenant_id": None,
    "must_change_password": True,
}

CLEAR_USER = {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "tenant_id": None,
    "must_change_password": False,
}


@pytest.fixture
def app():
    """Register all blueprints under /api."""
    from flask import Flask

    from app.routes.alerts import alerts_bp
    from app.routes.fs import fs_bp
    from app.routes.project_categories import project_categories_bp
    from app.routes.projects import projects_bp
    from app.routes.remote import remote_bp
    from app.routes.run_timeline import run_timeline_bp
    from app.routes.system import system_bp
    from app.routes.workspace import workspace_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    # Mount each blueprint with the SAME url_prefix used in app/__init__.py so
    # the before_request hooks and route paths behave exactly as in production.
    app.register_blueprint(workspace_bp, url_prefix="/api/workspace")
    app.register_blueprint(projects_bp, url_prefix="/api")
    app.register_blueprint(system_bp, url_prefix="/api")
    app.register_blueprint(alerts_bp, url_prefix="/api")
    app.register_blueprint(project_categories_bp, url_prefix="/api")
    app.register_blueprint(fs_bp, url_prefix="/api")
    app.register_blueprint(remote_bp, url_prefix="/api/remote")
    app.register_blueprint(run_timeline_bp, url_prefix="/api/remote")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.mark.parametrize(
    "method,path,user_role",
    [
        ("GET", "/api/workspace/prompts", "user"),
        ("GET", "/api/projects", "user"),
        ("GET", "/api/schedulers", "admin"),
        ("GET", "/api/alerts", "user"),
        ("GET", "/api/project-categories", "user"),
        ("GET", "/api/fs/browse", "user"),
    ],
)
def test_must_change_user_is_blocked_on_every_blueprint(client, method, path, user_role):
    """A forced-reset user must get 403 password_change_required on all six."""
    blocked_session = dict(MUST_CHANGE_SESSION, role=user_role, must_change_password=True)
    blocked_user = dict(MUST_CHANGE_USER, role=user_role, must_change_password=True)

    with patch("app.auth.decorators._authenticate", return_value=(True, blocked_session)):
        with patch("app.routes.projects.user_repo.get_user_by_id", return_value=blocked_user):
            with patch(
                "app.routes.project_categories.user_repo.get_user_by_id",
                return_value=blocked_user,
            ):
                with patch("app.routes.fs.user_repo.get_user_by_id", return_value=blocked_user):
                    resp = client.open(path, method=method, headers={"Authorization": "Bearer t"})

    assert resp.status_code == 403, f"{path} did not block must-change user"
    data = resp.get_json()
    assert data["code"] == "password_change_required"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/workspace/prompts"),
        ("GET", "/api/projects"),
        ("GET", "/api/schedulers"),
        ("GET", "/api/alerts"),
        ("GET", "/api/project-categories"),
        ("GET", "/api/fs/browse"),
    ],
)
def test_clear_user_is_not_over_blocked(client, method, path):
    """A user without must_change_password must not be over-blocked by the gate.

    The gate must let the request through before_request, i.e. the response
    must NOT be the password_change_required 403 (downstream errors from the
    unmocked DB are acceptable and irrelevant to this assertion).
    """
    with patch("app.auth.decorators._authenticate", return_value=(True, CLEAR_SESSION)):
        with patch("app.routes.projects.user_repo.get_user_by_id", return_value=CLEAR_USER):
            with patch(
                "app.routes.project_categories.user_repo.get_user_by_id",
                return_value=CLEAR_USER,
            ):
                with patch("app.routes.fs.user_repo.get_user_by_id", return_value=CLEAR_USER):
                    try:
                        resp = client.open(
                            path, method=method, headers={"Authorization": "Bearer t"}
                        )
                    except sqlite3.OperationalError:
                        # The bare Flask app has no DB schema. The alerts,
                        # system, fs and workspace handlers wrap their DB
                        # access in try/except and return 500; the projects
                        # and project-categories handlers do not, so a
                        # "no such table" error escapes client.open. Reaching
                        # the handler proves the gate let the clear user
                        # through - the only behaviour this test asserts.
                        return

    if resp.status_code == 403:
        body = resp.get_json(silent=True) or {}
        assert (
            body.get("code") != "password_change_required"
        ), f"{path} was over-blocked by the password-change gate for a clear user"


def test_must_change_user_is_blocked_on_remote_blueprint_session_token(client):
    """remote_bp authenticates via load_remote_user (shared with run_timeline_bp).

    A must_change_password user must be blocked at before_request before reaching
    any of the ~40 before_request-only remote endpoints (here GET /machines),
    which have no per-route auth decorator.
    """
    blocked_session = dict(MUST_CHANGE_SESSION, role="user", must_change_password=True)
    with patch("app.auth.decorators._authenticate", return_value=(True, blocked_session)):
        # load_remote_user does not re-fetch via user_repo; g.user comes straight
        # from _load_user_from_token, so no get_user_by_id patch is needed.
        resp = client.get("/api/remote/machines", headers={"Authorization": "Bearer t"})

    assert resp.status_code == 403
    assert resp.get_json()["code"] == "password_change_required"


def test_set_user_from_webui_token_populates_must_change_password():
    """The WebUI-token g.user literal must carry must_change_password.

    Without the flag, enforce_password_change_requirement silently no-ops and a
    must_change_password user authenticated via the WebUI-token branch slips
    through the gate. This pins the literal built in
    session_access._set_user_from_webui_token.
    """
    from flask import Flask, g

    from app.modules.workspace import session_access

    with patch.dict("os.environ", {}, clear=False):
        with patch("app.services.webui_manager.WebUIManager") as mgr_cls:
            mgr_cls.return_value.validate_token.return_value = (True, 1, None)
            with patch("app.repositories.user_repo.UserRepository") as repo_cls:
                repo_cls.return_value.get_user_by_id.return_value = MUST_CHANGE_USER
                app = Flask(__name__)
                with app.test_request_context("/?token=webui-token"):
                    result = session_access._set_user_from_webui_token()
                    # Assert inside the app context: g.user only exists while the
                    # request context is active.
                    assert result is True
                    assert g.user["must_change_password"] is True


def test_clear_user_is_not_over_blocked_on_remote_blueprint(client):
    """A clear user must not be over-blocked on the remote blueprint."""
    with patch("app.auth.decorators._authenticate", return_value=(True, CLEAR_SESSION)):
        try:
            resp = client.get("/api/remote/machines", headers={"Authorization": "Bearer t"})
        except sqlite3.OperationalError:
            # No DB schema -> handler raises; reaching it proves the gate passed.
            return

    if resp.status_code == 403:
        body = resp.get_json(silent=True) or {}
        assert body.get("code") != "password_change_required"
