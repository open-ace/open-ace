"""Route-level tests for shared-project revocation reclaim (Issue #3396).

``PUT /api/projects/<id> {is_shared: false}`` used to flip only the DB flag:
group-member accounts kept OS-level read/write (the API layer's browse 400s
never reach the OS channel). The route must now call
``revoke_shared_project_access`` (chown -R creator + dirs 0700 / files 0600)
on the True->False transition — and ONLY there. The reclaim is fail-soft:
a failure surfaces as ``permission_warning`` in the 200 response, the
revocation itself stands.
"""

from pathlib import Path
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


class TestChownWrapperConfigurablePrefixes:
    """Issue #3396 review (finding 8): ALLOWED_PREFIXES was hardcoded to
    /workspace + /home, so a deployment with a custom WORKSPACE_BASE_DIR
    (e.g. /data) had every reclaim rejected by the wrapper — fail-soft,
    forever. The wrapper must source an optional conf that may redefine the
    array, and the entrypoint must write that conf at boot from the
    configured base dirs. Textual tests per the entrypoint-test convention
    (the wrapper itself needs root to execute)."""

    WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "openace-chown.sh"
    ENTRYPOINT = Path(__file__).resolve().parents[2] / "docker-entrypoint.sh"

    def test_wrapper_sources_optional_conf_over_defaults(self):
        content = self.WRAPPER.read_text(encoding="utf-8")
        assert (
            'ALLOWED_PREFIXES=("/workspace/" "/home/")' in content
        ), "built-in defaults must be kept for conf-less environments"
        conf_line = '. "$CONF_FILE"'
        assert 'CONF_FILE="/etc/openace/openace-chown.conf"' in content
        assert conf_line in content, "a readable conf must be sourced"
        # the empty-array guard: an operator conf must not silently allow all
        assert "${#ALLOWED_PREFIXES[@]} -eq 0" in content

    def test_entrypoint_writes_conf_from_workspace_base_dirs(self):
        content = self.ENTRYPOINT.read_text(encoding="utf-8")
        assert "openace-chown.conf" in content, "entrypoint must write the conf at boot"
        # derived from WORKSPACE_BASE_DIR (comma list, trimmed, slash-normalized)
        assert "WORKSPACE_BASE_DIR:-/workspace" in content
        assert '"/home/"' in content

    def test_generated_conf_shape(self, tmp_path):
        """Run the entrypoint's generation snippet standalone and check the
        emitted conf is a valid bash array assignment the wrapper can
        source."""
        content = self.ENTRYPOINT.read_text(encoding="utf-8")
        start = content.index('_chown_conf="$_chown_conf_dir/openace-chown.conf"')
        # extract just the generation block between { and } > "$_chown_conf"
        block_start = content.index("{\n", start)
        block_end = content.index('} > "$_chown_conf"', block_start)
        gen_block = content[block_start + 2 : block_end]
        script = f"""
            _chown_conf="$(mktemp)"
            WORKSPACE_BASE_DIR=" /data ,/srv/ws "
            {{ {gen_block} }} > "$_chown_conf"
            cat "$_chown_conf"
        """
        import subprocess

        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        # simulate the wrapper consuming it
        check = subprocess.run(
            [
                "bash",
                "-c",
                'ALLOWED_PREFIXES=("/workspace/" "/home/"); '
                + proc.stdout.strip()
                + '; echo "${ALLOWED_PREFIXES[*]}"',
            ],
            capture_output=True,
            text=True,
        )
        assert check.returncode == 0, check.stderr
        assert check.stdout.strip() == "/data/ /srv/ws/ /home/"
