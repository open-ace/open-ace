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
    forever. The wrapper reads an optional conf, and the entrypoint writes
    it at boot from the configured base dirs.

    PR #3402 review: the wrapper runs as ROOT via sudo, so the conf is DATA
    (one prefix per line), never sourced shell code. Before use the wrapper
    stat-validates it (root-owned, no group/world write bits) and validates
    every line (absolute, not "/", [A-Za-z0-9/._-] only); a rejected conf
    falls back to the built-in prefixes with an audit line. Tested
    FUNCTIONALLY with a PATH-shimmed harness (fake stat/chown/flock): the
    wrapper's own logic runs verbatim, only the system commands are
    stubbed."""

    WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "openace-chown.sh"
    ENTRYPOINT = Path(__file__).resolve().parents[2] / "docker-entrypoint.sh"

    def _run_wrapper(
        self,
        tmp_path: Path,
        *,
        conf_lines: str | None,
        stat_out: str = "0 644",
        args: tuple[str, ...] = ("1000:1000", "/data/proj"),
    ) -> tuple[int, str, str, str]:
        """Run a relocated copy of the wrapper with stubbed stat/chown/flock.

        Returns (rc, stdout, stderr, chown-log). ``stat_out`` is what the
        fake ``stat -c '%u %a'`` reports for the conf file."""
        import os
        import subprocess

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        chown_log = tmp_path / "chown.log"
        chown_log.unlink(missing_ok=True)  # one log per invocation
        fake_stat = bin_dir / "stat"
        fake_stat.write_text('#!/bin/sh\necho "$FAKE_STAT_OUT"\n')
        fake_stat.chmod(0o755)
        fake_chown = bin_dir / "chown"
        fake_chown.write_text('#!/bin/sh\necho "chown $*" >> "$FAKE_CHOWN_LOG"\n')
        fake_chown.chmod(0o755)
        fake_flock = bin_dir / "flock"
        fake_flock.write_text("#!/bin/sh\nexit 0\n")
        fake_flock.chmod(0o755)

        conf = tmp_path / "openace-chown.conf"
        if conf_lines is not None:
            conf.write_text(conf_lines)

        script = (
            self.WRAPPER.read_text(encoding="utf-8")
            .replace('CONF_FILE="/etc/openace/openace-chown.conf"', f'CONF_FILE="{conf}"')
            .replace('LOCK_FILE="/var/lock/openace-chown.lock"', f'LOCK_FILE="{tmp_path}/lock"')
            .replace('AUDIT_LOG="/app/logs/sudoers-audit.log"', f'AUDIT_LOG="{tmp_path}/audit.log"')
        )
        runner = tmp_path / "wrapper-under-test.sh"
        runner.write_text(script)
        runner.chmod(0o755)

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_STAT_OUT": stat_out,
            "FAKE_CHOWN_LOG": str(chown_log),
        }
        proc = subprocess.run(
            ["/bin/bash", str(runner), *args],
            capture_output=True,
            text=True,
            env=env,
        )
        log = chown_log.read_text() if chown_log.exists() else ""
        return proc.returncode, proc.stdout, proc.stderr, log

    def test_wrapper_reads_validated_conf_data_not_shell(self):
        content = self.WRAPPER.read_text(encoding="utf-8")
        assert (
            'ALLOWED_PREFIXES=("/workspace/" "/home/")' in content
        ), "built-in defaults must be kept for conf-less environments"
        assert 'CONF_FILE="/etc/openace/openace-chown.conf"' in content
        assert '. "$CONF_FILE"' not in content, (
            "the conf must never be SOURCED: it is env-derived data and this "
            "wrapper runs as root (PR #3402 review)"
        )
        assert "stat -c '%u %a'" in content, "ownership/permission validation required"
        assert "${#ALLOWED_PREFIXES[@]} -eq 0" in content, "empty-list guard must be kept"

    def test_entrypoint_writes_conf_from_workspace_base_dirs(self):
        content = self.ENTRYPOINT.read_text(encoding="utf-8")
        assert "openace-chown.conf" in content, "entrypoint must write the conf at boot"
        # derived from WORKSPACE_BASE_DIR (comma list, trimmed, slash-normalized)
        assert "WORKSPACE_BASE_DIR:-/workspace" in content
        assert "printf '/home/\\n'" in content, "one prefix per line, /home always included"

    def test_clean_root_owned_conf_extends_prefixes(self, tmp_path):
        rc, _out, _err, log = self._run_wrapper(
            tmp_path,
            conf_lines="# comment\n/data/\n",
            stat_out="0 644",
            args=("1000:1000", "/data/proj"),
        )
        assert rc == 0, _err
        assert "chown 1000:1000 /data/proj" in log

    def test_non_root_owned_conf_rejected_with_fallback(self, tmp_path):
        """A conf not owned by root must be refused — but fail-safe: the
        built-in prefixes still apply (an operator error must not brick
        every reclaim)."""
        rc, _out, err, log = self._run_wrapper(
            tmp_path,
            conf_lines="/data/\n",
            stat_out="1001 644",
            args=("1000:1000", "/data/proj"),
        )
        assert rc == 2, "conf prefix must not apply when the file is not root-owned"
        assert "not root-owned" in err
        assert log == "", "no chown may run under an untrusted conf's prefixes"
        # fall back: a built-in prefix still works (a non-existent /workspace
        # path resolves through the parent — deterministic on every host)
        rc2, _o2, _e2, log2 = self._run_wrapper(
            tmp_path,
            conf_lines="/data/\n",
            stat_out="1001 644",
            args=("1000:1000", "/workspace/foo/x"),
        )
        assert rc2 == 0, _e2
        assert "chown 1000:1000 /workspace/foo/x" in log2

    def test_world_writable_conf_rejected(self, tmp_path):
        rc, _out, err, log = self._run_wrapper(
            tmp_path,
            conf_lines="/data/\n",
            stat_out="0 666",
            args=("1000:1000", "/data/proj"),
        )
        assert rc == 2
        assert "group/world-writable" in err
        assert log == ""

    def test_malformed_conf_entries_are_skipped(self, tmp_path):
        """Only admissible lines apply: absolute paths, not "/", characters
        within [A-Za-z0-9/._-]. A relative entry, the filesystem root, and
        shell-metacharacter entries are skipped with a warning; a conf of
        ONLY inadmissible lines keeps the built-in prefixes."""
        conf = '/ok/\nnot/absolute\n/\n/quo"te\n$(reboot)\n'
        rc, _out, err, log = self._run_wrapper(
            tmp_path, conf_lines=conf, stat_out="0 644", args=("1000:1000", "/ok/f")
        )
        assert rc == 0, err
        assert "chown 1000:1000 /ok/f" in log
        assert err.count("skipped") >= 3, "each inadmissible line must be loudly skipped"
        # the shell-syntax lines did NOT execute (the harness surviving this
        # far proves it: $(reboot) as a command would have errored loudly)
        rc2, _o, _e, log2 = self._run_wrapper(
            tmp_path, conf_lines=conf, stat_out="0 644", args=("1000:1000", "/data/proj")
        )
        assert rc2 == 2, "no inadmissible prefix may broaden the guard"
        assert log2 == ""
        # only-inadmissible conf: built-ins still apply
        rc3, _o3, _e3, log3 = self._run_wrapper(
            tmp_path,
            conf_lines="/\nrelative\n",
            stat_out="0 644",
            args=("1000:1000", "/workspace/foo/x"),
        )
        assert rc3 == 0
        assert "chown 1000:1000 /workspace/foo/x" in log3
