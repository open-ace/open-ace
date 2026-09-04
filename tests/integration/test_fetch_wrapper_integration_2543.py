#!/usr/bin/env python3
"""
Integration tests for Issue #2543

Tests the complete data collection flow with permission 700 home directories.
These tests require:
1. Root access (or passwordless sudo)
2. Multiple test users with permission 700 home directories
3. Real Qwen JSONL data files

Issue #2543: Local workspace session data collection permission fix
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ============================================================================
# Test fixtures
# ============================================================================


def _bash_major() -> int:
    import subprocess

    try:
        out = subprocess.run(
            ["bash", "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True
        )
        return int(out.stdout.strip() or 0)
    except (OSError, ValueError):
        return 0


requires_bash4 = pytest.mark.skipif(
    _bash_major() < 4, reason="wrapper requires bash>=4 (CI bash5 runs it)"
)


@pytest.fixture
def project_root():
    """Get the project root directory."""
    return Path(__file__).parent.parent.parent


@pytest.fixture
def wrapper_path():
    """Get the wrapper script path."""
    possible_paths = [
        "/usr/local/bin/openace-fetch-wrapper",
        "scripts/openace-fetch-wrapper",
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return None


@pytest.fixture
def temp_test_users():
    """Create temporary test users (requires root)."""
    if os.geteuid() != 0:
        pytest.skip("Requires root access")

    users = []
    try:
        # Create test users
        for i in range(2):
            username = f"test_fetch_user_{i}"
            # This would need actual user creation commands
            users.append(username)
        yield users
    finally:
        # Cleanup test users
        pass


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        yield db_path
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


# ============================================================================
# Core functionality tests
# ============================================================================


# TestPermission700Collection was deleted (#3186 batch 3): three root-gated
# pass-body placeholders requiring real user creation — no CI-honest path
# (mock-gated testing is the forbidden formal repair). The root/multi-user
# e2e family is tracked in #3293. temp_test_users (an unimplemented shell
# fixture) was removed with it.


class TestSecurityIntegration:
    """Security tests that require full environment."""

    # test_web_service_cannot_read_other_users was deleted (#3186 batch 3):
    # the service-account permission-regression e2e needs a deployment
    # context (#3293); the wrapper's sudoers confinement face is asserted by
    # test_sudoers_only_allows_wrapper below.

    def test_sudoers_only_allows_wrapper(self, project_root):
        """
        Test that sudoers only allows the wrapper, not arbitrary Python.
        """
        sudoers_path = "/etc/sudoers.d/open-ace"

        if not os.path.exists(sudoers_path):
            pytest.skip("Sudoers file not installed")

        with open(sudoers_path) as f:
            content = f.read()

        # Check that FETCH_WRAPPER is defined
        assert "FETCH_WRAPPER" in content or "openace-fetch-wrapper" in content

        # Check that it doesn't allow arbitrary Python
        assert "python" not in content.lower() or "openace-fetch-wrapper" in content

    def test_parameter_injection_blocked(self, wrapper_path):
        """
        Test that parameter injection attacks are blocked.
        """
        if wrapper_path is None:
            pytest.skip("Wrapper not installed")

        # Test various injection attempts
        injection_attempts = [
            ["--malicious", "fetch_qwen", "--days", "1"],
            ["fetch_qwen", "--days", "1", "--malicious"],
            ["fetch_qwen", "--days", "1", ";", "ls"],
            ["fetch_qwen; ls", "--days", "1"],
        ]

        for args in injection_attempts:
            result = subprocess.run(
                ["bash", wrapper_path] + args,
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0, f"Injection not blocked: {args}"

    @requires_bash4
    def test_symlink_attack_blocked(self, wrapper_path, tmp_path):
        """A whitelist-side symlink pointing OUTSIDE the whitelist is rejected
        by the REAL validate_file closure (#3186 batch 3).

        The link must live under a home-shaped path (the allowlist gates the
        link location); its malicious target lives in plain tmp_path —
        safe_resolve_symlink must refuse to resolve outside the whitelist,
        and the rejection is audited.
        """
        import re as _re
        import shutil
        import subprocess as _sp
        import uuid

        if wrapper_path is None:
            pytest.skip("Wrapper not installed")

        text = Path(wrapper_path).read_text(encoding="utf-8")
        parts = []
        for fn in [
            "normalize_path",
            "is_allowed_path",
            "safe_resolve_symlink",
            "log_audit",
            "sanitize_username",
            "sanitize_details",
            "_username_hash",
            "validate_file",
        ]:
            m = _re.search(rf"^{fn}\(\) \{{.*?^\}}", text, _re.S | _re.M)
            assert m, f"extraction failed for {fn}"
            parts.append(m.group(0))
        for cp, flags in [
            (r"^MAX_FILE_SIZE=.*$", _re.M),
            (r"^MAX_SYMLINK_DEPTH=.*$", _re.M),
            (r"^AUDIT_LOG=.*$", _re.M),
            (r"^declare -A TOOL_TO_DIR=\(.*?^\)$", _re.S | _re.M),
        ]:
            m = _re.search(cp, text, flags)
            assert m, f"extraction failed for {cp}"
            parts.append(m.group(0))
        harness = tmp_path / "closure.sh"
        harness.write_text("\n\n".join(parts) + "\n")

        import pwd

        pw_dir = pwd.getpwuid(os.getuid()).pw_dir
        under_home = Path(pw_dir) / ".qwen" / f"fwtest-{uuid.uuid4().hex[:8]}"
        audit = tmp_path / "audit.log"
        try:
            proj = under_home / "projects"
            proj.mkdir(parents=True)
            malicious = tmp_path / "malicious.jsonl"
            malicious.write_text('{"malicious": true}')
            symlink = proj / "attack.jsonl"
            symlink.symlink_to(malicious)

            preflight = (
                "for f in normalize_path is_allowed_path safe_resolve_symlink log_audit "
                "sanitize_username sanitize_details _username_hash validate_file; "
                "do declare -F $f >/dev/null || exit 99; done; "
                "for v in MAX_FILE_SIZE MAX_SYMLINK_DEPTH AUDIT_LOG TOOL_TO_DIR; "
                "do declare -p $v >/dev/null || exit 99; done"
            )
            script = (
                f"set -u; source {harvest_quote(harness)}; {preflight} || exit 99; "
                f"validate_file {harvest_quote(symlink)}; echo rc=$?"
            )
            result = _sp.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                env={**os.environ, "AUDIT_LOG": str(audit)},
            )
            assert result.returncode == 0, result.stderr
            assert "rc=1" in result.stdout
            assert "symlink_rejected" in audit.read_text()
        finally:
            shutil.rmtree(under_home, ignore_errors=True)


def harvest_quote(path) -> str:
    import shlex

    return shlex.quote(str(path))


# ============================================================================
# Configuration tests
# ============================================================================


class TestConfigurationIntegration:
    """Configuration drift and setup tests."""

    def test_scheduler_env_fetch_use_sudo(self, project_root):
        """
        Test that scheduler service has FETCH_USE_SUDO=true configured.
        """
        service_path = project_root / "scripts" / "openace-scheduler.service"

        if not service_path.exists():
            pytest.skip("Service file not found")

        content = service_path.read_text()

        # Check for FETCH_USE_SUDO=true
        assert "FETCH_USE_SUDO=true" in content

    def test_generate_sudoers_includes_fetch_wrapper(self, project_root):
        """
        Test that generate-sudoers.sh includes FETCH_WRAPPER rule.
        """
        sudoers_script = project_root / "scripts" / "generate-sudoers.sh"

        if not sudoers_script.exists():
            pytest.skip("generate-sudoers.sh not found")

        content = sudoers_script.read_text()

        # Check for FETCH_WRAPPER
        assert "FETCH_WRAPPER" in content

    def test_fetch_route_uses_wrapper(self, project_root):
        """
        Test that fetch route uses wrapper when FETCH_USE_SUDO=true.
        """
        fetch_route = project_root / "app" / "routes" / "fetch.py"

        if not fetch_route.exists():
            pytest.skip("fetch.py not found")

        content = fetch_route.read_text()

        # Check for wrapper usage
        assert "openace-fetch-wrapper" in content or "use_wrapper" in content


# ============================================================================
# Error handling tests
# ============================================================================


# TestErrorHandlingIntegration was deleted (#3186 batch 3): the degraded
# contract is implemented for real in
# tests/unit/test_fetch_wrapper_2543.py::TestIntegration (additive home_base
# scan root + chmod-000 projects dir => REAL PermissionError => denied), and
# idempotent collection likewise (test_idempotent_collection).


class TestAuditLoggingIntegration:
    """Test audit logging functionality."""

    def test_audit_log_created(self, wrapper_path):
        """
        Test that audit log is created after collection.
        """
        if wrapper_path is None:
            pytest.skip("Wrapper not installed")

        audit_log = Path("/var/log/openace/fetch-audit.log")

        if not audit_log.exists():
            pytest.skip("Audit log not found (may not have run yet)")

        # Verify log format
        content = audit_log.read_text()
        lines = content.strip().split("\n")

        if lines:
            # Check that log has expected format
            assert "|" in lines[0]  # Timestamp | caller | action | details
            assert "caller=" in lines[0]
            assert "action=" in lines[0]

    def test_audit_log_permissions(self):
        """
        Test that audit log has correct permissions (600).
        """
        audit_log = Path("/var/log/openace/fetch-audit.log")

        if not audit_log.exists():
            pytest.skip("Audit log not found")

        # Check permissions
        stat_result = audit_log.stat()
        # 600 = owner read/write only
        assert stat_result.st_mode & 0o777 == 0o600

    def test_usernames_sanitized_in_log(self):
        """
        Test that usernames are sanitized in audit log.

        #3292 review note: deliberately left as a deployed-box canary — it
        skips everywhere in CI (/var/log/openace/fetch-audit.log does not
        exist on runners). Strengthening it to assert the sanitized shape is
        unsound: on an upgraded box where the wrapper has not run since the
        upgrade, every existing line (including the last) predates the fix
        and legitimately contains raw usernames. The real shape contract is
        enforced on every PR by TestAuditLogging in
        tests/unit/test_fetch_wrapper_2543.py.
        """
        audit_log = Path("/var/log/openace/fetch-audit.log")

        if not audit_log.exists():
            pytest.skip("Audit log not found")

        content = audit_log.read_text()

        # Check that full home paths are not present
        assert "/home/" not in content or "user=" not in content


# ============================================================================
# Performance tests
# TestPerformanceIntegration was deleted (#3186 batch 3): the 100-real-user
# scale scenario belongs to the root/e2e family (#3293), and the large-file
# contract is covered for real by the extracted validate_file test in
# tests/unit/test_fetch_wrapper_2543.py (these were @pytest.mark.slow, not
# performance-marked — a different case from #3290).
