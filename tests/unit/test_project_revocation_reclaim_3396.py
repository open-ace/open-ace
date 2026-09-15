"""Route-level tests for shared-project revocation reclaim (Issue #3396).

``PUT /api/projects/<id> {is_shared: false}`` used to flip only the DB flag:
group-member accounts kept OS-level read/write (the API layer's browse 400s
never reach the OS channel). The route must now call
``revoke_shared_project_access`` (chown -R creator + dirs 0700 / files 0600)
on the True->False transition — and ONLY there. The reclaim is fail-soft:
a failure surfaces as ``permission_warning`` in the 200 response, the
revocation itself stands.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, g

pytestmark = [pytest.mark.regression, pytest.mark.issue(3396)]

USER_ALICE = {"id": 7, "username": "alice", "role": "user", "tenant_id": 1}


def _project(**overrides):
    base = SimpleNamespace(
        id=101,
        path="/workspace/shared/team-proj",
        name="team-proj",
        is_shared=True,
        created_by=7,
        tenant_id=1,
        to_dict=lambda: {"id": 101, "is_shared": False},
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


@pytest.fixture
def projects_app():
    from app.routes.projects import projects_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(projects_bp, url_prefix="/api")

    # projects_bp 是模块级单例:清空蓝图自带回调后注入 g.user
    app.before_request_funcs["projects"] = []

    @app.before_request
    def _set_user():
        g.user = dict(USER_ALICE)
        g.user_id = USER_ALICE["id"]
        g.user_role = USER_ALICE["role"]
        g.tenant_id = USER_ALICE["tenant_id"]
        return None

    return app


def _stub_repo(project, *, update_ok=True, creator=None):
    repo = MagicMock()
    repo.get_project_by_id = MagicMock(
        side_effect=lambda pid, tenant_id=None: (
            project if project.is_shared else _project(is_shared=False)
        )
    )
    repo.update_project = MagicMock(return_value=update_ok)
    users = MagicMock()
    users.get_user_by_id = MagicMock(
        return_value=creator if creator is not None else {"system_account": "alice-acct"}
    )
    return repo, users


def _revoke(client):
    return client.put("/api/projects/101", json={"is_shared": False})


def test_revocation_reclaims_os_access(projects_app):
    """True->False must run the reclaim with the CREATOR's account."""
    repo, users = _stub_repo(_project())
    with (
        patch("app.routes.projects.project_repo", repo),
        patch("app.routes.projects.user_repo", users),
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch("app.routes.projects._is_docker_multi_user_mode", return_value=True),
        patch("app.routes.projects.revoke_shared_project_access") as mock_revoke,
    ):
        mock_revoke.return_value = (True, "")
        resp = _revoke(projects_app.test_client())

    assert resp.status_code == 200
    mock_revoke.assert_called_once_with(
        "/workspace/shared/team-proj",
        "alice-acct",
        user_id=7,
        project_id=101,
    )
    assert "permission_warning" not in resp.get_json()


def test_revocation_reclaim_failure_is_fail_soft(projects_app):
    """A failed reclaim must not fail the revocation — warning in the body."""
    repo, users = _stub_repo(_project())
    with (
        patch("app.routes.projects.project_repo", repo),
        patch("app.routes.projects.user_repo", users),
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch("app.routes.projects._is_docker_multi_user_mode", return_value=True),
        patch("app.routes.projects.revoke_shared_project_access") as mock_revoke,
    ):
        mock_revoke.return_value = (False, "chown failed: boom")
        resp = _revoke(projects_app.test_client())

    assert resp.status_code == 200
    assert "chown failed: boom" in resp.get_json()["permission_warning"]


def test_revocation_without_creator_account_warns(projects_app):
    """No resolvable creator -> skip the reclaim, still 200 + warning."""
    repo, users = _stub_repo(_project(), creator={"system_account": None, "username": None})
    with (
        patch("app.routes.projects.project_repo", repo),
        patch("app.routes.projects.user_repo", users),
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch("app.routes.projects._is_docker_multi_user_mode", return_value=True),
        patch("app.routes.projects.revoke_shared_project_access") as mock_revoke,
    ):
        resp = _revoke(projects_app.test_client())

    assert resp.status_code == 200
    mock_revoke.assert_not_called()
    assert "reclaim was skipped" in resp.get_json()["permission_warning"]


def test_no_reclaim_when_project_was_not_shared(projects_app):
    """is_shared=false on an already-private project: nothing to reclaim."""
    repo, users = _stub_repo(_project(is_shared=False))
    with (
        patch("app.routes.projects.project_repo", repo),
        patch("app.routes.projects.user_repo", users),
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch("app.routes.projects._is_docker_multi_user_mode", return_value=True),
        patch("app.routes.projects.revoke_shared_project_access") as mock_revoke,
    ):
        resp = _revoke(projects_app.test_client())

    assert resp.status_code == 200
    mock_revoke.assert_not_called()


def test_no_reclaim_in_single_user_mode(projects_app):
    """Non-Docker mode has no OS groups — the DB flag is the revocation."""
    repo, users = _stub_repo(_project())
    with (
        patch("app.routes.projects.project_repo", repo),
        patch("app.routes.projects.user_repo", users),
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch("app.routes.projects._is_docker_multi_user_mode", return_value=False),
        patch("app.routes.projects.revoke_shared_project_access") as mock_revoke,
    ):
        resp = _revoke(projects_app.test_client())

    assert resp.status_code == 200
    mock_revoke.assert_not_called()


def test_name_only_update_does_not_revoke(projects_app):
    """A PUT that never touches is_shared must not reclaim anything."""
    repo, users = _stub_repo(_project())
    with (
        patch("app.routes.projects.project_repo", repo),
        patch("app.routes.projects.user_repo", users),
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch("app.routes.projects._is_docker_multi_user_mode", return_value=True),
        patch("app.routes.projects.revoke_shared_project_access") as mock_revoke,
    ):
        resp = projects_app.test_client().put("/api/projects/101", json={"name": "renamed"})

    assert resp.status_code == 200
    mock_revoke.assert_not_called()


def test_share_setup_uses_tenant_group(projects_app, tmp_path):
    """False->True re-share must pass the project's tenant to the group
    setup (the tenant-scoped chgrp happens inside the workspace util)."""
    project_dir = tmp_path / "team-proj"
    project_dir.mkdir()
    project = _project(is_shared=False, path=str(project_dir))
    repo, users = _stub_repo(project)
    with (
        patch("app.routes.projects.project_repo", repo),
        patch("app.routes.projects.user_repo", users),
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch("app.routes.projects._is_docker_multi_user_mode", return_value=True),
        patch("app.routes.projects.estimate_file_count_fast", return_value=0),
        patch("app.routes.projects.setup_permissions_with_depth_limit") as mock_setup,
    ):
        mock_setup.return_value = (True, "", 0)
        resp = projects_app.test_client().put("/api/projects/101", json={"is_shared": True})

    assert resp.status_code == 200
    assert mock_setup.call_args.kwargs.get("tenant_id") == 1
