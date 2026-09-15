"""Tests for shared project permissions (Issue #2730; tenant-scoped #3396).

Tests for the workspace utility functions that manage shared project
file system permissions in Docker multi-user mode. Since #3396 shared
project CONTENT is group-owned by the per-tenant group
``openace-shared-<tenant_id>`` with dirs 2770 / files 660 (no others
bits); the global ``openace-shared`` group only grants namespace-root
creation rights.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.utils.workspace import (
    SHARED_GROUP_NAME,
    add_user_to_shared_group,
    ensure_shared_group,
    estimate_file_count_fast,
    revoke_shared_project_access,
    setup_permissions_with_depth_limit,
    setup_shared_project_permissions,
    shared_tenant_group_name,
    verify_setgid_support,
)


class TestSharedTenantGroupName:
    """Issue #3396: tenant group naming."""

    def test_tenant_id_suffix(self):
        assert shared_tenant_group_name(1) == "openace-shared-1"
        assert shared_tenant_group_name(42) == "openace-shared-42"

    def test_null_tenant_maps_to_pseudo_zero(self):
        assert shared_tenant_group_name(None) == "openace-shared-0"

    def test_negative_tenant_rejected(self):
        with pytest.raises(ValueError):
            shared_tenant_group_name(-1)

    def test_absurd_tenant_id_exceeding_linux_limit_rejected(self):
        with pytest.raises(ValueError):
            shared_tenant_group_name(10**17)

    def test_name_fits_linux_group_limit_for_realistic_ids(self):
        assert len(shared_tenant_group_name(10**15)) == 31


class TestEnsureSharedGroup:
    """Tests for ensure_shared_group function."""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_skip_non_docker_mode(self, mock_run, mock_docker_mode):
        """Should skip group creation in non-Docker mode."""
        mock_docker_mode.return_value = False

        result = ensure_shared_group(tenant_id=7)

        assert result is True
        mock_run.assert_not_called()

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_creates_tenant_scoped_group(self, mock_run, mock_docker_mode):
        """Issue #3396: creates openace-shared-<tenant_id>, not the global group."""
        mock_docker_mode.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        result = ensure_shared_group(tenant_id=7)

        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "groupadd" in args
        assert "-f" in args
        assert "openace-shared-7" in args

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_null_tenant_creates_pseudo_group(self, mock_run, mock_docker_mode):
        mock_docker_mode.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        assert ensure_shared_group() is True
        assert "openace-shared-0" in mock_run.call_args[0][0]

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_returns_false_on_failure(self, mock_run, mock_docker_mode):
        """Should return False if group creation fails."""
        mock_docker_mode.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stderr="groupadd failed")

        result = ensure_shared_group(tenant_id=7)

        assert result is False


class TestAddUserToSharedGroup:
    """Tests for add_user_to_shared_group function."""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_skip_non_docker_mode(self, mock_run, mock_docker_mode):
        """Should skip in non-Docker mode."""
        mock_docker_mode.return_value = False

        result = add_user_to_shared_group("testuser", tenant_id=7)

        assert result is True
        mock_run.assert_not_called()

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_enrolls_into_global_and_tenant_groups(self, mock_run, mock_docker_mode):
        """Issue #3396: BOTH groups — global (namespace creation) and the
        tenant content group."""
        mock_docker_mode.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        result = add_user_to_shared_group("testuser", tenant_id=7)

        assert result is True
        usermod_cmds = [c.args[0] for c in mock_run.call_args_list if c.args[0][0] == "usermod"]
        assert ["usermod", "-aG", SHARED_GROUP_NAME, "testuser"] in usermod_cmds
        assert ["usermod", "-aG", "openace-shared-7", "testuser"] in usermod_cmds

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("subprocess.run")
    def test_returns_false_on_failure(self, mock_run, mock_docker_mode):
        """Should return False if usermod fails."""
        mock_docker_mode.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stderr="usermod failed")

        result = add_user_to_shared_group("testuser", tenant_id=7)

        assert result is False


class TestSetupSharedProjectPermissions:
    """Tests for setup_shared_project_permissions function."""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_skip_non_docker_mode(self, mock_docker_mode):
        """Should skip in non-Docker mode."""
        mock_docker_mode.return_value = False

        success, error = setup_shared_project_permissions("/some/path", tenant_id=7)

        assert success is True
        assert error == ""

    def test_empty_path_returns_error(self):
        """Should return error for empty path."""
        with patch("app.utils.workspace._is_docker_multi_user_mode", return_value=True):
            success, error = setup_shared_project_permissions("")

            assert success is False
            assert "Path is required" in error

    def test_relative_path_returns_error(self):
        """Should return error for relative path."""
        with patch("app.utils.workspace._is_docker_multi_user_mode", return_value=True):
            success, error = setup_shared_project_permissions("relative/path")

            assert success is False
            assert "must be absolute" in error

    def test_nonexistent_path_returns_error(self):
        """Should return error for non-existent path."""
        with patch("app.utils.workspace._is_docker_multi_user_mode", return_value=True):
            success, error = setup_shared_project_permissions("/nonexistent/path/12345")

            assert success is False
            assert "does not exist" in error

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace.ensure_shared_group")
    @patch("subprocess.run")
    def test_sets_permissions_successfully(self, mock_run, mock_ensure_group, mock_docker_mode):
        """Should set permissions successfully on existing directory."""
        mock_docker_mode.return_value = True
        mock_ensure_group.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            success, error = setup_shared_project_permissions(tmpdir, tenant_id=7)

            assert success is True
            assert error == ""
            cmds = [c.args[0] for c in mock_run.call_args_list]
            assert ["chown", ":openace-shared-7", tmpdir] in cmds
            assert ["chmod", "2770", tmpdir] in cmds, "no others bits (cross-tenant EACCES)"

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace.ensure_shared_group")
    def test_returns_error_if_group_creation_fails(self, mock_ensure_group, mock_docker_mode):
        """Should return error if shared group creation fails."""
        mock_docker_mode.return_value = True
        mock_ensure_group.return_value = False

        with tempfile.TemporaryDirectory() as tmpdir:
            success, error = setup_shared_project_permissions(tmpdir, tenant_id=7)

            assert success is False
            assert "Failed to create shared group" in error

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace.ensure_shared_group")
    @patch("subprocess.run")
    def test_returns_error_if_chown_fails(self, mock_run, mock_ensure_group, mock_docker_mode):
        """Should return error if chown fails."""
        mock_docker_mode.return_value = True
        mock_ensure_group.return_value = True

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "chown" in cmd:
                return MagicMock(returncode=1, stderr="chown failed")
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = run_side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            success, error = setup_shared_project_permissions(tmpdir, tenant_id=7)

            assert success is False
            assert "chown failed" in error

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace.ensure_shared_group")
    @patch("subprocess.run")
    def test_returns_error_if_chmod_fails(self, mock_run, mock_ensure_group, mock_docker_mode):
        """Should return error if chmod fails."""
        mock_docker_mode.return_value = True
        mock_ensure_group.return_value = True

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "chmod" in cmd and "2770" in cmd:
                return MagicMock(returncode=1, stderr="chmod failed")
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = run_side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            success, error = setup_shared_project_permissions(tmpdir, tenant_id=7)

            assert success is False
            assert "chmod failed" in error


class TestFixPermissionsEndpoint:
    """Tests for the fix-permissions API endpoint integration."""

    def test_endpoint_function_imported(self):
        """Verify the fix-permissions endpoint function can be imported."""
        # Simply verify the function exists in the module
        from app.routes import projects

        assert hasattr(projects, "api_fix_project_permissions")


# ============================================================================
# Performance Optimization Tests (Issue #2746)
# ============================================================================


class TestEstimateFileCountFast:
    """Tests for fast file count estimation."""

    def test_estimate_empty_directory(self):
        """Should return 0 for empty directory."""
        from app.utils.workspace import estimate_file_count_fast

        with tempfile.TemporaryDirectory() as tmpdir:
            count = estimate_file_count_fast(tmpdir)
            assert count == 0

    def test_estimate_small_directory(self):
        """Should estimate small number of files."""
        from app.utils.workspace import estimate_file_count_fast

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a few files
            for i in range(10):
                open(os.path.join(tmpdir, f"file{i}.txt"), "w").close()

            count = estimate_file_count_fast(tmpdir)
            assert count >= 10

    @patch("subprocess.run")
    def test_estimate_timeout_returns_max(self, mock_run):
        """Should return 50000 when estimation times out."""
        import subprocess

        from app.utils.workspace import estimate_file_count_fast

        mock_run.side_effect = subprocess.TimeoutExpired("find", 5)

        count = estimate_file_count_fast("/some/path", timeout=5)
        assert count == 50000

    def test_estimate_nonexistent_path(self):
        """Should return default for nonexistent path."""
        from app.utils.workspace import estimate_file_count_fast

        count = estimate_file_count_fast("/nonexistent/path/12345")
        assert count == 50000


class TestSetupPermissionsWithDepthLimit:
    """Tests for optimized permission setup with depth limit."""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_skip_non_docker_mode(self, mock_docker_mode):
        """Should skip in non-Docker mode."""
        from app.utils.workspace import setup_permissions_with_depth_limit

        mock_docker_mode.return_value = False

        success, error, processed = setup_permissions_with_depth_limit("/some/path")

        assert success is True
        assert error == ""
        assert processed == 0

    def test_invalid_path(self):
        """Should return error for invalid path."""
        from app.utils.workspace import setup_permissions_with_depth_limit

        with patch("app.utils.workspace._is_docker_multi_user_mode", return_value=True):
            success, error, processed = setup_permissions_with_depth_limit("relative/path")

            assert success is False
            assert "Invalid path" in error

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace.ensure_shared_group")
    @patch("subprocess.run")
    def test_sets_permissions_with_depth_limit(self, mock_run, mock_ensure_group, mock_docker_mode):
        """Should set permissions with depth limit."""
        mock_docker_mode.return_value = True
        mock_ensure_group.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some files
            for i in range(5):
                open(os.path.join(tmpdir, f"file{i}.txt"), "w").close()

            success, error, processed = setup_permissions_with_depth_limit(
                tmpdir,
                depth_limit=3,
                timeout=30,
                tenant_id=7,
            )

            assert success is True
            assert error == ""
            assert processed >= 0
            cmds = [c.args[0] for c in mock_run.call_args_list]
            assert ["chown", ":openace-shared-7", tmpdir] in cmds
            assert ["chmod", "2770", tmpdir] in cmds

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace.ensure_shared_group")
    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_batch_chmod_modes_drop_others_bits(
        self, mock_popen, mock_run, mock_ensure_group, mock_docker_mode
    ):
        """Issue #3396: recursive passes use 2770/660 — no others bits, or
        cross-tenant accounts (global-group members) could read content."""
        mock_docker_mode.return_value = True
        mock_ensure_group.return_value = True

        with tempfile.TemporaryDirectory() as tmpdir:
            # find returns the root dir (d) / one file (f) so the batched
            # xargs branches actually run
            mock_run.side_effect = lambda cmd, **kw: MagicMock(
                returncode=0,
                stderr="",
                stdout=f"{tmpdir}\n" if cmd[1] == tmpdir and cmd[2:3] == ["-type"] else "",
            )
            success, error, _ = setup_permissions_with_depth_limit(tmpdir, tenant_id=7)
            assert success is True
            popen_cmds = [c.args[0] for c in mock_popen.call_args_list]
            assert ["xargs", "-0", "chmod", "2770"] in popen_cmds
            assert ["xargs", "-0", "chmod", "660"] in popen_cmds
            assert not any("2775" in cmd or "664" in cmd for cmd in popen_cmds)


class TestRevokeSharedProjectAccess:
    """Issue #3396: revocation reclaims OS-level access."""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_skip_non_docker_mode(self, mock_docker_mode):
        mock_docker_mode.return_value = False
        success, error = revoke_shared_project_access("/some/path", "alice")
        assert success is True
        assert error == ""

    def test_missing_path_is_success(self):
        """A path that never materialized has nothing to reclaim."""
        with patch("app.utils.workspace._is_docker_multi_user_mode", return_value=True):
            success, error = revoke_shared_project_access("/nonexistent/proj/12345", "alice")
        assert success is True
        assert error == ""

    def test_missing_owner_is_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.utils.workspace._is_docker_multi_user_mode", return_value=True):
                success, error = revoke_shared_project_access(tmpdir, "")
        assert success is False
        assert "Owner system account is required" in error

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace._is_wrapper_available")
    @patch("app.utils.workspace.run_as_root_if_needed")
    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_reclaims_to_creator_private_permissions(
        self, mock_popen, mock_run, mock_root, mock_wrapper, mock_docker_mode
    ):
        """chown -R creator + dirs 0700 / files 0600 — group members lose
        access, the creator keeps it."""
        mock_docker_mode.return_value = True
        mock_wrapper.return_value = False
        mock_root.return_value = MagicMock(returncode=0, stderr="")
        id_results = {
            ("id", "-u", "alice"): MagicMock(returncode=0, stdout="1500\n", stderr=""),
            ("id", "-g", "alice"): MagicMock(returncode=0, stdout="1500\n", stderr=""),
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            # find -type d / -type f return one entry each so the batched
            # xargs chmod branches run
            mock_run.side_effect = lambda cmd, **kw: id_results.get(
                tuple(cmd),
                MagicMock(
                    returncode=0,
                    stderr="",
                    stdout=f"{tmpdir}\n" if cmd[0] == "find" else "",
                ),
            )
            mock_popen.return_value = MagicMock(
                returncode=0, communicate=MagicMock(return_value=("", ""))
            )
            success, error = revoke_shared_project_access(tmpdir, "alice")

        assert success is True
        assert error == ""
        root_cmds = [c.args[0] for c in mock_root.call_args_list]
        assert ["chown", "-R", "1500:1500", tmpdir] in root_cmds
        popen_cmds = [c.args[0] for c in mock_popen.call_args_list]
        assert ["xargs", "-0", "chmod", "0700"] in popen_cmds
        assert ["xargs", "-0", "chmod", "0600"] in popen_cmds

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace._is_wrapper_available")
    @patch("app.utils.workspace.run_as_root_if_needed")
    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_partial_chmod_strip_reports_failure(
        self, mock_popen, mock_run, mock_root, mock_wrapper, mock_docker_mode
    ):
        """Review on #3396 (finding 7): the xargs chmod's return code used to
        be ignored — a partial strip (xargs dies mid-batch) reported success
        while ex-members kept access to the surviving entries."""
        mock_docker_mode.return_value = True
        mock_wrapper.return_value = False
        mock_root.return_value = MagicMock(returncode=0, stderr="")
        id_results = {
            ("id", "-u", "alice"): MagicMock(returncode=0, stdout="1500\n", stderr=""),
            ("id", "-g", "alice"): MagicMock(returncode=0, stdout="1500\n", stderr=""),
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = lambda cmd, **kw: id_results.get(
                tuple(cmd),
                MagicMock(
                    returncode=0,
                    stderr="",
                    stdout=f"{tmpdir}\n" if cmd[0] == "find" else "",
                ),
            )
            mock_popen.return_value = MagicMock(
                returncode=123, communicate=MagicMock(return_value=("", "chmod: No such file"))
            )
            success, error = revoke_shared_project_access(tmpdir, "alice")

        assert success is False
        assert "rc=123" in error
        assert "chmod: No such file" in error

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace._is_wrapper_available")
    @patch("app.utils.workspace.run_as_root_if_needed")
    @patch("subprocess.run")
    def test_unresolvable_owner_fails_closed(
        self, mock_run, mock_root, mock_wrapper, mock_docker_mode
    ):
        """If the creator account does not exist, say so instead of chowning
        to garbage."""
        mock_docker_mode.return_value = True
        mock_wrapper.return_value = False
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no such user")

        with tempfile.TemporaryDirectory() as tmpdir:
            success, error = revoke_shared_project_access(tmpdir, "ghost")

        assert success is False
        assert "Cannot resolve owner" in error
        mock_root.assert_not_called()

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    @patch("app.utils.workspace._is_wrapper_available")
    @patch("app.utils.workspace.run_as_root_if_needed")
    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_uses_chown_wrapper_when_available(
        self, mock_popen, mock_run, mock_root, mock_wrapper, mock_docker_mode
    ):
        mock_docker_mode.return_value = True
        mock_wrapper.return_value = True
        mock_root.return_value = MagicMock(returncode=0, stderr="")
        id_results = {
            ("id", "-u", "alice"): MagicMock(returncode=0, stdout="1500\n", stderr=""),
            ("id", "-g", "alice"): MagicMock(returncode=0, stdout="1500\n", stderr=""),
        }
        mock_run.side_effect = lambda cmd, **kw: id_results.get(
            tuple(cmd), MagicMock(returncode=0, stdout="", stderr="")
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            success, _ = revoke_shared_project_access(tmpdir, "alice")

        assert success is True
        first_cmd = mock_root.call_args_list[0].args[0]
        assert first_cmd[0] == "/usr/local/bin/openace-chown"
        assert first_cmd[1] == "-R"


class TestVerifySetgidSupport:
    """Tests for setgid support verification."""

    @patch("app.utils.workspace._is_docker_multi_user_mode")
    def test_verify_in_non_docker_mode(self, mock_docker_mode):
        """Should return False in non-Docker mode (skip verification)."""
        from app.utils.workspace import verify_setgid_support

        mock_docker_mode.return_value = False

        # This test would need actual Docker environment to verify setgid
        # For now, just test the function exists and can be called
        assert callable(verify_setgid_support)

    def test_verify_nonexistent_path(self):
        """Should return error for nonexistent path."""
        from app.utils.workspace import verify_setgid_support

        supported, error = verify_setgid_support("/nonexistent/path/12345")

        assert supported is False
        assert "does not exist" in error


class TestPermissionTaskService:
    """Tests for permission task service."""

    def test_service_singleton(self):
        """Should return the same service instance."""
        from app.services.permission_task_service import (
            PermissionTaskService,
            get_permission_task_service,
        )

        service1 = get_permission_task_service()
        service2 = get_permission_task_service()

        assert service1 is service2
        assert isinstance(service1, PermissionTaskService)

    def test_generate_checksum(self):
        """Should generate consistent checksum for same inputs."""
        from app.services.permission_task_service import get_permission_task_service

        service = get_permission_task_service()

        checksum1 = service.generate_task_checksum(123, "/path/to/project")
        checksum2 = service.generate_task_checksum(123, "/path/to/project")

        assert checksum1 == checksum2
        assert len(checksum1) == 32  # MD5 hex digest

    def test_different_checksum_for_different_projects(self):
        """Should generate different checksums for different projects."""
        from app.services.permission_task_service import get_permission_task_service

        service = get_permission_task_service()

        checksum1 = service.generate_task_checksum(123, "/path/to/project1")
        checksum2 = service.generate_task_checksum(456, "/path/to/project2")

        assert checksum1 != checksum2


class TestPermissionTaskAPIEndpoints:
    """Tests for permission task API endpoints."""

    def test_task_status_endpoint_exists(self):
        """Verify task status endpoint function can be imported."""
        from app.routes import projects

        assert hasattr(projects, "api_get_permission_task_status")

    def test_cancel_task_endpoint_exists(self):
        """Verify cancel task endpoint function can be imported."""
        from app.routes import projects

        assert hasattr(projects, "api_cancel_permission_task")


class TestSubmitTaskTenantPayload:
    """Issue #3396 review (finding 5): the async permission path must carry
    the project's tenant so a future processor chgrps to
    openace-shared-<tenant_id> instead of the openace-shared-0 pseudo-tenant.

    The queue table has no consumer and no dedicated column, so tenant_id
    rides in the checkpoint_data JSON payload slot at submit time."""

    def _submit(self, monkeypatch, tmp_path, tenant_id):
        from app.services.permission_task_service import get_permission_task_service

        service = get_permission_task_service()
        monkeypatch.setattr(service, "check_queue_saturation", lambda db: (False, 0))
        monkeypatch.setattr(service, "check_existing_task", lambda db, pid: None)
        monkeypatch.setattr(
            "app.services.permission_task_service.estimate_file_count_fast",
            lambda path: 5000,
        )

        insert_params: dict = {}

        class _Result:
            @staticmethod
            def scalar():
                return 0

        class _Db:
            def execute(self, stmt, params=None):
                sql = str(stmt)
                if "INSERT INTO permission_tasks" in sql:
                    insert_params.update(params or {})
                return _Result()

            def commit(self):
                return None

            def rollback(self):
                return None

        success, error_msg, task_info = service.submit_task(
            _Db(),
            project_id=101,
            user_id=7,
            path=str(tmp_path / "big-proj"),
            tenant_id=tenant_id,
        )
        return success, error_msg, task_info, insert_params

    def test_tenant_id_persisted_in_checkpoint_payload(self, monkeypatch, tmp_path):
        import json

        success, error_msg, task_info, params = self._submit(monkeypatch, tmp_path, tenant_id=3)
        assert success, error_msg
        assert json.loads(params["checkpoint_data"]) == {"tenant_id": 3}
        assert task_info["tenant_id"] == 3

    def test_absent_tenant_leaves_checkpoint_null(self, monkeypatch, tmp_path):
        success, error_msg, task_info, params = self._submit(monkeypatch, tmp_path, tenant_id=None)
        assert success, error_msg
        assert params["checkpoint_data"] is None
        assert task_info["tenant_id"] is None
