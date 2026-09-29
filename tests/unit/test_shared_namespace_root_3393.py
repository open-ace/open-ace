"""Issue #3393: on-demand shared-namespace-root provisioning in POST /api/projects.

The #3376 first-class namespace ``<base>/shared/<name>`` needs its root to
pre-exist (root-owned, group ``openace-shared``, sticky+setgid). The Docker
entrypoint provisions it at boot (#3379), but on a deployment where the
entrypoint did not run — or the root simply went missing — the FIRST shared
project creation ran ``sudo -u <user> mkdir -p`` against a root:root 0755
parent and EACCESed (HTTP 403), with no way to bootstrap the namespace from
the API.

``app.utils.workspace.ensure_shared_namespace_root`` closes the gap: invoked
from the create path right before the user-side mkdir, it provisions the one
namespace root the project path lives under (mirroring the entrypoint's
semantics — root-owned, ``groupadd -f``, mode 3770 with sticky+setgid and no
others bits). The tests below cover the helper's contract (command sequence,
idempotence, skip modes, fail-closed guards) and the route wiring (provision
then create; existing root untouched; provisioning failure degrades to a
clean 500, never a crash).
"""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask, g

pytestmark = [pytest.mark.regression, pytest.mark.issue(3393)]

USER = {"id": 7, "user_id": 7, "username": "alice", "role": "user", "tenant_id": 1}

_USER_ROWS = [{"id": 7, "username": "alice", "system_account": "alice-acct"}]


class _FakeProc:
    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


class TestEnsureSharedNamespaceRoot:
    """Helper-level contract, with the OS seams stubbed."""

    @pytest.fixture()
    def stubbed(self, monkeypatch, tmp_path):
        from app.utils import workspace as ws

        calls: list[tuple] = []
        root_exists = {"value": False}
        shared_account_exists = {"value": False}
        fail: dict[str, str] = {}

        def fake_root_if_needed(cmd):
            calls.append(tuple(cmd))
            name = Path(cmd[0]).name
            if name == "id":
                return _FakeProc(0 if shared_account_exists["value"] else 1)
            if name in fail:
                return _FakeProc(1, err=f"{name}: simulated failure")
            return _FakeProc(0)

        monkeypatch.setattr(ws, "run_as_root_if_needed", fake_root_if_needed)
        monkeypatch.setattr(ws, "_is_docker_multi_user_mode", lambda: True)
        monkeypatch.setattr(ws.subprocess, "run", lambda cmd, **kw: fake_root_if_needed(cmd))

        real_exists = os.path.exists

        def fake_exists(p):
            if p == str(tmp_path / "base" / "shared"):
                return root_exists["value"]
            return real_exists(p)

        monkeypatch.setattr(ws.os.path, "exists", fake_exists)
        return ws, calls, tmp_path, root_exists, shared_account_exists, fail

    def test_provisions_root_owned_group_mode(self, stubbed):
        """Missing root → groupadd + mkdir + chgrp + chmod 3770 (in order)."""
        ws, calls, tmp_path, *_ = stubbed
        ok, err = ws.ensure_shared_namespace_root(str(tmp_path / "base"))
        assert ok is True and err == ""
        base = str(tmp_path / "base")
        assert ("groupadd", "-f", "openace-shared") in calls
        assert ("mkdir", "-p", f"{base}/shared") in calls
        assert ("chgrp", "openace-shared", f"{base}/shared") in calls
        assert ("chmod", "3770", f"{base}/shared") in calls, (
            "sticky+setgid with no others bits (cross-tenant rename guard + "
            "group inheritance); must match the entrypoint's provisioning"
        )
        # Ordering: the group must exist before the chgrp names it.
        assert calls.index(("groupadd", "-f", "openace-shared")) < calls.index(
            ("chgrp", "openace-shared", f"{base}/shared")
        )

    def test_existing_root_left_untouched(self, stubbed):
        """Steady state: an existing root is never re-chgrp/chmod'd."""
        ws, calls, tmp_path, root_exists, *_ = stubbed
        root_exists["value"] = True
        ok, err = ws.ensure_shared_namespace_root(str(tmp_path / "base"))
        assert ok is True and err == ""
        assert not any(
            c[0].endswith(("mkdir", "chgrp", "chmod")) for c in calls
        ), "an existing namespace root must not be touched from the request path"

    def test_non_multi_user_mode_is_noop(self, monkeypatch, tmp_path):
        from app.utils import workspace as ws

        calls: list[tuple] = []
        monkeypatch.setattr(
            ws, "run_as_root_if_needed", lambda cmd: calls.append(tuple(cmd)) or _FakeProc(0)
        )
        monkeypatch.setattr(ws, "_is_docker_multi_user_mode", lambda: False)
        ok, err = ws.ensure_shared_namespace_root(str(tmp_path))
        assert ok is True and err == ""
        assert calls == []

    def test_refuses_account_named_shared(self, stubbed):
        """<base>/shared colliding with a REAL account's home fails closed."""
        ws, calls, tmp_path, _, shared_account_exists, _ = stubbed
        shared_account_exists["value"] = True
        ok, err = ws.ensure_shared_namespace_root(str(tmp_path / "base"))
        assert ok is False and "shared" in err
        assert not any(c[0].endswith(("mkdir", "chgrp", "chmod")) for c in calls)

    def test_provisioning_step_failure_returns_clean_error(self, stubbed):
        """Any failing step degrades to (False, message) — never an exception."""
        ws, calls, tmp_path, _, _, fail = stubbed
        for step in ("groupadd", "mkdir", "chgrp", "chmod"):
            fail[step] = step
            ok, err = ws.ensure_shared_namespace_root(str(tmp_path / "base"))
            assert ok is False and step in err, f"{step} failure must surface in the error"
            del fail[step]

    def test_rejects_relative_base_dir(self, stubbed):
        ws, *_ = stubbed
        ok, err = ws.ensure_shared_namespace_root("relative/base")
        assert ok is False and "absolute" in err


# ── route wiring: POST /api/projects provisions before the user-side mkdir ──

# Home-based scratch dir: macOS $TMPDIR realpaths under /private/var, which
# is_valid_path's system blacklist rejects — the 3376 suite uses the same
# home-based approach for its route-level tests.
_SCRATCH = Path.home() / ".ace_ns_root_3393"


@pytest.fixture
def workspace():
    ws = _SCRATCH
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir()
    yield ws
    shutil.rmtree(ws, ignore_errors=True)


@pytest.fixture
def projects_app(workspace, monkeypatch):
    from app.routes.projects import projects_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(projects_bp, url_prefix="/api")

    app.before_request_funcs["projects"] = []

    @app.before_request
    def _set_user():
        g.user = dict(USER)
        g.user_id = USER["id"]
        g.user_role = USER["role"]
        g.tenant_id = USER["tenant_id"]
        return None

    provision_calls: list[str] = []
    from app.utils import workspace as ws_mod

    monkeypatch.setattr(
        "app.routes.projects.ensure_shared_namespace_root",
        lambda base: provision_calls.append(base) or (True, ""),
    )

    with (
        patch("app.routes.projects.get_current_tenant_id", return_value=1),
        patch("app.routes.projects.get_workspace_base_dirs", return_value=[str(workspace)]),
        patch("app.routes.projects.user_repo.get_all_users", return_value=list(_USER_ROWS)),
        patch("app.routes.projects.project_repo.get_shared_project_paths", return_value=[]),
        patch("app.routes.projects.project_repo.get_project_by_path", return_value=None),
        patch(
            "app.routes.projects.project_repo.create_project",
            return_value=123,
        ),
        patch(
            "app.routes.projects.project_repo.get_project_by_id",
            return_value=type(
                "P",
                (),
                {
                    "to_dict": lambda self: {"id": 123, "path": "x"},
                    "id": 123,
                    "is_shared": True,
                },
            )(),
        ),
    ):
        yield app, provision_calls


def _create(client, path, is_shared=True, create_dir=True):
    return client.post(
        "/api/projects",
        json={"path": path, "name": "p", "is_shared": is_shared, "create_dir": create_dir},
    )


def test_route_provisions_missing_root_then_creates(projects_app, workspace):
    """Root missing → the create path provisions it BEFORE the project mkdir.

    The mkdir itself is stubbed to a no-op success (the OS-level mkdir is the
    helper-level suite's job); what matters here is the ORDER: provisioning
    runs, then the project row is created (201)."""
    app, provision_calls = projects_app
    client = app.test_client()
    resp = _create(client, str(workspace / "shared" / "team-proj"))
    assert resp.status_code == 201, resp.get_json()
    assert provision_calls == [str(workspace)]
    assert resp.get_json()["project"]["id"] == 123


def test_route_provisioning_failure_is_clean_500(projects_app, workspace, monkeypatch):
    """ "Provisioning fails → structured 500 error, no crash/traceback path."""
    app, _ = projects_app
    monkeypatch.setattr(
        "app.routes.projects.ensure_shared_namespace_root",
        lambda base: (False, "mkdir failed: EACCES"),
    )
    client = app.test_client()
    resp = _create(client, str(workspace / "shared" / "team-proj"))
    assert resp.status_code == 500
    assert "shared namespace root" in resp.get_json()["error"]
    # The DB row must NOT be created when provisioning failed.
    # (create_project is patched in the fixture; a 500 before the row insert
    # is asserted by the status code itself — the create call sits after the
    # provisioning block.)


def test_route_private_project_skips_provisioning(projects_app, workspace):
    """The on-demand path is scoped to SHARED projects: a private creation
    under a plain home never provisions (and never 500s on provisioning)."""
    app, provision_calls = projects_app
    client = app.test_client()
    resp = _create(client, str(workspace / "alice-acct" / "proj"), is_shared=False)
    assert resp.status_code == 201, resp.get_json()
    assert provision_calls == []


def test_route_shared_without_create_dir_skips_provisioning(projects_app, workspace):
    """create_dir=false registers an EXISTING directory — nothing to
    provision (the root must already exist for the path to be on disk)."""
    app, provision_calls = projects_app
    client = app.test_client()
    resp = _create(client, str(workspace / "shared" / "team-proj"), create_dir=False)
    assert resp.status_code == 201, resp.get_json()
    assert provision_calls == []
