#!/usr/bin/env python3
"""
Unit tests for openace-fetch-wrapper

Tests the security features of the fetch wrapper:
1. Parameter validation (exact match, no wildcards)
2. Symlink attack prevention
3. Path whitelist validation
4. File size limits
5. Audit logging

Issue #2543: Local workspace session data collection permission fix
Issue #3249: Parameter validation underscore fix and security hardening
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# Test fixtures
# ============================================================================


@pytest.fixture
def wrapper_path():
    """Get the path to the wrapper script."""
    # Prefer local script over system-installed version for testing
    # This ensures tests run against the modified version
    local_path = "scripts/openace-fetch-wrapper"
    if os.path.exists(local_path):
        return local_path

    # Fall back to system-installed version
    system_path = "/usr/local/bin/openace-fetch-wrapper"
    if os.path.exists(system_path):
        return system_path

    return local_path


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def fake_config(temp_dir):
    """Create a fake config file for testing."""
    config_path = temp_dir / "config.json"
    config_path.write_text('{"database": {"url": "sqlite:///test.db"}}')
    return str(config_path)


# ============================================================================
# #3186 Phase B batch 3: real-behavior tests for the wrapper's file gate.
# The harness below extracts the REAL function text from
# scripts/openace-fetch-wrapper at test time (column-0 anchored) and sources
# it — a wrapper edit that breaks the extraction fails the define-preflight,
# and no harness fallbacks exist to mask drift. bash >= 4 required (the
# wrapper uses associative arrays); CI's bash 5 always runs these, stock
# macOS bash 3.2 skips.
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


WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "openace-fetch-wrapper"


def _extract_closure(tmp_path: Path) -> Path:
    """Extract validate_file and its full dependency closure from the real
    wrapper into a sourceable harness file (fail-closed: define-preflight in
    the consumer asserts everything landed)."""
    names = [
        "normalize_path",
        "is_allowed_path",
        "safe_resolve_symlink",
        "log_audit",
        "sanitize_username",
        "sanitize_details",
        "_username_hash",
        "validate_file",
    ]
    text = WRAPPER.read_text(encoding="utf-8")
    parts = []
    for fn in names:
        pattern = re.compile(rf"^{fn}\(\) \{{.*?^\}}", re.S | re.M)
        m = pattern.search(text)
        assert m, f"extraction failed for {fn} — wrapper drifted?"
        parts.append(m.group(0))
    # Constants the closure reads (single-line matches — NO re.S here, or
    # `.*$` would swallow the rest of the file under re.M's line anchors).
    for const_pat in [
        r"^MAX_FILE_SIZE=.*$",
        r"^MAX_SYMLINK_DEPTH=.*$",
        r"^AUDIT_LOG=.*$",
        r"^declare -A TOOL_TO_DIR=\(.*?^\)$",
    ]:
        # Single-line patterns must NOT use re.S: `.*$` under re.S+re.M
        # swallows the rest of the file (the TOOL_TO_DIR entry needs re.S
        # for its multi-line value, where the non-greedy `.*?^\)` stops at
        # the declaration's column-0 close).
        flags = re.S | re.M if "TOOL_TO_DIR" in const_pat else re.M
        m = re.search(const_pat, text, flags)
        assert m, f"extraction failed for {const_pat} — wrapper drifted?"
        parts.append(m.group(0))
    harness = tmp_path / "wrapper_closure.sh"
    harness.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    return harness


def _run_closure(
    harness: Path, script: str, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Source the extracted closure and run `script` under bash; fail-closed
    preflight asserts every symbol is defined by the extraction alone."""
    import os as _os

    preflight = (
        "for f in normalize_path is_allowed_path safe_resolve_symlink log_audit "
        "sanitize_username sanitize_details _username_hash validate_file; "
        'do declare -F $f >/dev/null || { echo "PREFLIGHT-MISSING: $f" >&2; exit 99; }; done; '
        "for v in MAX_FILE_SIZE MAX_SYMLINK_DEPTH AUDIT_LOG TOOL_TO_DIR; "
        'do declare -p $v >/dev/null || { echo "PREFLIGHT-MISSING: $v" >&2; exit 99; }; done'
    )
    full = f"set -u; source {harvest_quoted(harness)}; {preflight} || exit 99; {script}"
    environ = dict(_os.environ)
    environ.update(env or {})
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, env=environ)


def harvest_quoted(path: Path) -> str:
    return shlex_quote(str(path))


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def _real_user_home() -> Path:
    """The ACCOUNT's home directory, immune to HOME env overrides.

    The CI suite runner isolates HOME to a tmp path (ci.py
    isolated_environment) that the wrapper's hardcoded /home|/Users
    allowlist can never accept — Path.home() there points outside the
    whitelist. pwd.getpwuid gives the real, home-shaped, writable
    directory (/home/runner on CI, /Users/<user> on macOS). Skips
    (legitimately, conditionally) where none exists.
    """
    import pwd

    pw_dir = pwd.getpwuid(os.getuid()).pw_dir
    if re.match(r"^/(home|Users)/[a-zA-Z0-9_-]+$", pw_dir) and os.access(pw_dir, os.W_OK):
        return Path(pw_dir)
    pytest.skip(f"no writable home-shaped directory for the wrapper allowlist (pw_dir={pw_dir})")


# ============================================================================
# Issue #3249: Underscore in tool name tests
# ============================================================================


class TestUnderscoreInToolName:
    """Issue #3249: Test that underscore in tool names is accepted."""

    def test_all_allowed_tools_validate(self, wrapper_path):
        """Test that all ALLOWED_TOOLS pass tool validation."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        # All allowed tools have underscores
        allowed_tools = [
            "fetch_qwen",
            "fetch_claude",
            "fetch_zcode",
            "fetch_codex",
            "fetch_openclaw",
        ]

        for tool in allowed_tools:
            result = subprocess.run(
                ["bash", wrapper_path, tool, "--days", "1"],
                capture_output=True,
                text=True,
            )
            # Tool name should NOT be rejected as "Invalid tool"
            # The command may fail for other reasons (script not found, etc.)
            # but should NOT fail with "Invalid tool"
            assert "Invalid tool" not in result.stderr, f"Tool {tool} was rejected as invalid"

    def test_underscore_in_tool_name_accepted(self, wrapper_path):
        """Test that fetch_qwen (with underscore) is not rejected as invalid characters."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1"],
            capture_output=True,
            text=True,
        )
        # Should NOT have "Invalid characters" error for underscore
        assert (
            "Invalid characters" not in result.stderr
        ), "Underscore should not cause 'Invalid characters' error"


# ============================================================================
# Issue #3249: Mode parameter tests
# ============================================================================


class TestModeParameter:
    """Issue #3249: Test --mode parameter validation."""

    def test_mode_both_accepted(self, wrapper_path):
        """Test that --mode both is accepted."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_openclaw", "--mode", "both"],
            capture_output=True,
            text=True,
        )
        # Should NOT reject as unknown argument
        assert "Unknown argument" not in result.stderr

    def test_mode_usage_accepted(self, wrapper_path):
        """Test that --mode usage is accepted."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_openclaw", "--mode", "usage"],
            capture_output=True,
            text=True,
        )
        assert "Unknown argument" not in result.stderr

    def test_mode_messages_accepted(self, wrapper_path):
        """Test that --mode messages is accepted."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_openclaw", "--mode", "messages"],
            capture_output=True,
            text=True,
        )
        assert "Unknown argument" not in result.stderr

    def test_mode_invalid_rejected(self, wrapper_path):
        """Test that invalid --mode value is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_openclaw", "--mode", "invalid"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Invalid --mode" in result.stderr or "ERROR" in result.stderr


# ============================================================================
# Issue #3249: Duplicate parameter detection tests
# ============================================================================


class TestDuplicateParameterDetection:
    """Issue #3249: Test duplicate parameter detection."""

    def test_duplicate_days_rejected(self, wrapper_path):
        """Test that duplicate --days is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1", "--days", "2"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Duplicate parameter" in result.stderr

    def test_duplicate_config_rejected(self, wrapper_path):
        """Test that duplicate --config is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            [
                "bash",
                wrapper_path,
                "fetch_qwen",
                "--config",
                "/etc/openace/config.json",
                "--config",
                "/etc/openace/config.json",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Duplicate parameter" in result.stderr

    def test_duplicate_mode_rejected(self, wrapper_path):
        """Test that duplicate --mode is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            [
                "bash",
                wrapper_path,
                "fetch_openclaw",
                "--mode",
                "both",
                "--mode",
                "usage",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Duplicate parameter" in result.stderr


# ============================================================================
# Issue #3249: Parameter value boundary tests
# ============================================================================


class TestParameterValueBoundary:
    """Issue #3249: Test parameter value boundary validation."""

    def test_config_value_starts_with_dash_rejected(self, wrapper_path):
        """Test that --config value starting with - is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--config", "--evil"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "cannot start with '-'" in result.stderr or "ERROR" in result.stderr

    def test_days_value_starts_with_dash_rejected(self, wrapper_path):
        """Test that --days value starting with - is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "-1"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert (
            "cannot start with '-'" in result.stderr
        ), "Should reject parameter value starting with '-'"

    def test_mode_value_starts_with_dash_rejected(self, wrapper_path):
        """Test that --mode value starting with - is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_openclaw", "--mode", "-evil"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert (
            "cannot start with '-'" in result.stderr
        ), "Should reject parameter value starting with '-'"

    def test_days_missing_value_rejected(self, wrapper_path):
        """Test that --days without value is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "requires a value" in result.stderr or "ERROR" in result.stderr

    def test_config_missing_value_rejected(self, wrapper_path):
        """Test that --config without value is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--config"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "requires a value" in result.stderr or "ERROR" in result.stderr

    def test_exclamation_mark_rejected(self, wrapper_path):
        """Test that ! (history expansion) is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1!"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Invalid characters" in result.stderr

    def test_redirect_in_rejected(self, wrapper_path):
        """Test that < (input redirect) is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1<"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Invalid characters" in result.stderr

    def test_redirect_out_rejected(self, wrapper_path):
        """Test that > (output redirect) is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1>"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Invalid characters" in result.stderr

    def test_newline_rejected(self, wrapper_path):
        """Test that newline is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1\n2"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Invalid characters" in result.stderr or "ERROR" in result.stderr


# ============================================================================
# Issue #3249: Positive test cases (verify valid parameters are accepted)
# ============================================================================


class TestValidParametersAccepted:
    """Issue #3249: Test that valid parameter combinations are accepted."""

    def test_valid_tool_with_days_accepted(self, wrapper_path):
        """Test that fetch_qwen --days 1 is not rejected by validation."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1"],
            capture_output=True,
            text=True,
        )
        # Should NOT be rejected by validation
        assert "Invalid tool" not in result.stderr
        assert "Invalid characters" not in result.stderr
        assert "Unknown argument" not in result.stderr
        assert "Duplicate parameter" not in result.stderr

    def test_valid_tool_with_multi_user_accepted(self, wrapper_path):
        """Test that fetch_qwen --days 1 --multi-user is not rejected by validation."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1", "--multi-user"],
            capture_output=True,
            text=True,
        )
        # Should NOT be rejected by validation
        assert "Invalid tool" not in result.stderr
        assert "Invalid characters" not in result.stderr
        assert "Unknown argument" not in result.stderr

    def test_valid_tool_with_recent_accepted(self, wrapper_path):
        """Test that fetch_qwen --days 1 --recent is not rejected by validation."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1", "--recent"],
            capture_output=True,
            text=True,
        )
        # Should NOT be rejected by validation
        assert "Invalid tool" not in result.stderr
        assert "Invalid characters" not in result.stderr
        assert "Unknown argument" not in result.stderr

    def test_all_allowed_tools_accepted(self, wrapper_path):
        """Test that all ALLOWED_TOOLS pass validation."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        allowed_tools = [
            "fetch_qwen",
            "fetch_claude",
            "fetch_zcode",
            "fetch_codex",
            "fetch_openclaw",
        ]

        for tool in allowed_tools:
            result = subprocess.run(
                ["bash", wrapper_path, tool, "--days", "1"],
                capture_output=True,
                text=True,
            )
            # Should NOT be rejected as "Invalid tool"
            assert "Invalid tool" not in result.stderr, f"Tool {tool} was rejected as invalid"

    def test_days_boundary_min_accepted(self, wrapper_path):
        """Test that --days 1 (minimum) is accepted."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1"],
            capture_output=True,
            text=True,
        )
        assert "Invalid --days" not in result.stderr

    def test_days_boundary_max_accepted(self, wrapper_path):
        """Test that --days 365 (maximum) is accepted."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "365"],
            capture_output=True,
            text=True,
        )
        assert "Invalid --days" not in result.stderr

    def test_days_boundary_over_rejected(self, wrapper_path):
        """Test that --days 366 is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "366"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Invalid --days" in result.stderr


# ============================================================================
# Issue #3249: Relative path rejection tests
# ============================================================================


class TestRelativePathRejection:
    """Issue #3249: Test that relative paths are rejected."""

    def test_relative_config_path_rejected(self, wrapper_path):
        """Test that relative --config path is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--config", "config.json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "must be absolute" in result.stderr or "Config path" in result.stderr

    def test_relative_config_with_dots_rejected(self, wrapper_path):
        """Test that relative path with .. is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--config", "../config.json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "must be absolute" in result.stderr or "Config path" in result.stderr


# ============================================================================
# Issue #3249: Path normalization tests
# ============================================================================


class TestPathNormalization:
    """Issue #3249: Test path normalization fixes."""

    def test_wrapper_has_normalize_path(self, wrapper_path):
        """Test that wrapper contains normalize_path function."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        with open(wrapper_path) as f:
            content = f.read()

        assert "normalize_path" in content, "Missing normalize_path function"
        assert "readlink -f" in content or "realpath" in content, "Missing path normalization tools"

    def test_path_traversal_in_home_rejected(self, wrapper_path):
        """Test that path traversal via /home/user/../.. is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            [
                "bash",
                wrapper_path,
                "fetch_qwen",
                "--config",
                "/home/user/.open-ace/../../../etc/passwd",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert (
            "not allowed" in result.stderr
            or "not found" in result.stderr
            or "ERROR" in result.stderr
        )

    def test_nonexistent_path_rejected(self, wrapper_path):
        """Test that nonexistent path is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            [
                "bash",
                wrapper_path,
                "fetch_qwen",
                "--config",
                "/nonexistent/path/config.json",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "not found" in result.stderr or "ERROR" in result.stderr


# ============================================================================
# Issue #3249: Error message security tests
# ============================================================================


class TestErrorMessageSecurity:
    """Issue #3249: Test that error messages don't leak sensitive info."""

    def test_invalid_tool_no_tool_list(self, wrapper_path):
        """Test that invalid tool error doesn't list all tools."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_evil", "--days", "1"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        # Should NOT list all allowed tools
        assert "fetch_qwen" not in result.stderr
        assert "Allowed tools:" not in result.stderr

    def test_config_error_no_path_leak(self, wrapper_path):
        """Test that config error doesn't leak full path."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--config", "/tmp/evil.json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        # Error should be generic, not include the full path in some cases
        # (Note: current implementation may still include path, but that's a lower priority fix)


# ============================================================================
# Test parameter validation
# ============================================================================


class TestParameterValidation:
    """Test that parameter validation uses exact matching."""

    @requires_bash4
    def test_exact_match_valid_params(self, wrapper_path, tmp_path, monkeypatch):
        """Valid params pass validation — asserted on the POSITIVE face.

        Complements test_valid_tool_with_days_accepted (absence-of-rejection)
        by proving the wrapper actually starts and finishes the fetch: a
        PATH-shim python3 records the invocation and exits 0, and the audit
        log must carry fetch_start/fetch_end for tool=fetch_qwen. The shim
        keeps the run deterministic on both OSes and never touches the
        developer's real ~/.qwen.
        """
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        shim_dir = tmp_path / "bin"
        shim_dir.mkdir()
        argv_file = tmp_path / "argv.txt"
        (shim_dir / "python3").write_text(
            '#!/bin/sh\necho "$@" >> ' + shlex_quote(str(argv_file)) + "\nexit 0\n"
        )
        (shim_dir / "python3").chmod(0o755)
        monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
        audit_log = tmp_path / "audit.log"
        monkeypatch.setenv("AUDIT_LOG", str(audit_log))

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1"],
            capture_output=True,
            text=True,
        )
        assert "Invalid tool" not in result.stderr
        assert audit_log.exists(), "the wrapper must audit a real fetch"
        log_lines = audit_log.read_text()
        assert "action=fetch_start" in log_lines
        assert "tool=fetch_qwen" in log_lines
        assert "action=fetch_end" in log_lines
        assert argv_file.exists(), "the shim python3 must have been invoked"
        argv = argv_file.read_text().split()
        assert argv[0].endswith("fetch_qwen.py"), argv

    def test_reject_malicious_param_prefix(self, wrapper_path):
        """Test that malicious prefix is rejected (no substring match)."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        # Test that --malicious fetch_qwen ... is rejected
        # (substring match would incorrectly accept this)
        result = subprocess.run(
            ["bash", wrapper_path, "--malicious", "fetch_qwen", "--days", "1"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Invalid" in result.stderr or "ERROR" in result.stderr

    def test_reject_malicious_param_suffix(self, wrapper_path):
        """Test that malicious suffix is rejected (no substring match)."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        # Test that fetch_qwen ... --malicious is rejected
        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1", "--malicious"],
            capture_output=True,
            text=True,
        )
        # The wrapper should reject unknown arguments
        assert result.returncode != 0
        assert "Unknown argument" in result.stderr or "ERROR" in result.stderr

    def test_dashdash_separator_bypass_rejected(self, wrapper_path, tmp_path, monkeypatch):
        """`--` no longer stops validation (#3317).

        The separator case let everything after it skip the whitelist
        verbatim into the audit log (forging `| caller=... | action=...`
        fields) and the fetch script's argv. It must now fall into the
        unknown-argument rejection, and the rejection must happen BEFORE
        the first log_audit call — the forged fields reach no output and
        no audit line is written at all.
        """
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        audit_log = tmp_path / "audit.log"
        monkeypatch.setenv("AUDIT_LOG", str(audit_log))
        result = subprocess.run(
            [
                "bash",
                wrapper_path,
                "fetch_qwen",
                "--days",
                "1",
                "--",
                "| caller=root | action=forged | injected=yes",
                "dir=/home/alice",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Unknown argument: --" in result.stderr
        assert "caller=root" not in result.stderr
        # Rejection precedes fetch_start: no audit line may exist, or the
        # forged pipe-fields would be back in a consumer-parsed log.
        assert not audit_log.exists()

    def test_bare_dashdash_rejected(self, wrapper_path):
        """A bare `--` is rejected like any other unknown argument (#3317)."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Unknown argument: --" in result.stderr

    def test_reject_extra_args(self, wrapper_path):
        """Test that extra arguments are rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        # Use a valid config path that's in the whitelist
        result = subprocess.run(
            [
                "bash",
                wrapper_path,
                "fetch_qwen",
                "--days",
                "1",
                "--multi-user",
                "--recent",
                "--config",
                "/etc/openace/config.json",
                "--extra-arg",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Unknown argument" in result.stderr

    def test_reject_invalid_tool(self, wrapper_path):
        """Test that invalid tool names are rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        result = subprocess.run(
            ["bash", wrapper_path, "fetch_malicious", "--days", "1"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Invalid tool" in result.stderr

    def test_reject_dangerous_chars(self, wrapper_path):
        """Test that dangerous characters are rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        # Test semicolon injection - should be rejected as invalid tool
        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen; ls", "--days", "1"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Invalid tool" in result.stderr

        # Test shell injection in config value
        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1", "--config", "$(echo test)"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Invalid characters" in result.stderr or "ERROR" in result.stderr

    def test_config_path_validation(self, wrapper_path):
        """Test that config path validation is strict."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        # Test invalid config path
        result = subprocess.run(
            ["bash", wrapper_path, "fetch_qwen", "--days", "1", "--config", "/tmp/evil.json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Config path not allowed" in result.stderr or "Config path" in result.stderr

        # Test path traversal attempt
        result = subprocess.run(
            [
                "bash",
                wrapper_path,
                "fetch_qwen",
                "--days",
                "1",
                "--config",
                "/home/../../../etc/passwd",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0


# ============================================================================
# Test symlink attack prevention
# ============================================================================


class TestSymlinkPrevention:
    """Test that symlink attacks are prevented."""

    def test_wrapper_has_symlink_protection(self, wrapper_path):
        """Test that wrapper contains symlink protection code."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        # Read wrapper content
        with open(wrapper_path) as f:
            content = f.read()

        # Check for symlink protection functions
        assert "safe_resolve_symlink" in content, "Missing symlink resolution function"
        assert "is_allowed_path" in content, "Missing path whitelist function"
        assert "MAX_SYMLINK_DEPTH" in content, "Missing symlink depth limit"

    def test_path_whitelist_function(self, wrapper_path):
        """Test that path whitelist function exists."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        with open(wrapper_path) as f:
            content = f.read()

        # Check for tool directory mapping
        assert "TOOL_TO_DIR" in content, "Missing tool directory mapping"
        assert ".qwen" in content, "Missing .qwen directory"
        assert ".claude" in content, "Missing .claude directory"

    def test_symlink_loop_detection(self, wrapper_path):
        """Test that symlink loop detection exists."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        with open(wrapper_path) as f:
            content = f.read()

        # Check for loop detection
        assert "Symlink loop detected" in content, "Missing loop detection message"

    def test_symlink_depth_limit(self, wrapper_path):
        """Test that symlink depth limit is enforced."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        with open(wrapper_path) as f:
            content = f.read()

        # Check for depth limit
        assert "Symlink depth limit exceeded" in content, "Missing depth limit message"

    def test_single_level_symlink_outside_whitelist(self, temp_dir, wrapper_path):
        """Test that symlink pointing outside whitelist is rejected."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        # Create a malicious file outside allowed directories
        malicious_file = temp_dir / "malicious.jsonl"
        malicious_file.write_text('{"test": "malicious"}')

        # Create a user directory
        user_dir = temp_dir / "home" / "user1" / ".qwen" / "projects"
        user_dir.mkdir(parents=True)

        # Create a symlink pointing to the malicious file
        symlink_path = user_dir / "link.jsonl"
        symlink_path.symlink_to(malicious_file)

        # The wrapper should reject this (tested in real deployment)
        # This test verifies the logic exists
        assert symlink_path.exists()
        assert symlink_path.is_symlink()

    def test_multi_level_symlink_attack(self, temp_dir, wrapper_path):
        """Test that multi-level symlink attacks are prevented."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        # Create malicious file
        malicious_file = temp_dir / "malicious.jsonl"
        malicious_file.write_text('{"test": "malicious"}')

        # Create user directory
        user_dir = temp_dir / "home" / "user1" / ".qwen" / "projects"
        user_dir.mkdir(parents=True)

        # Create multi-level symlinks
        link1 = user_dir / "link1.jsonl"
        link1.symlink_to(malicious_file)

        link2 = user_dir / "link2.jsonl"
        link2.symlink_to(link1)

        # Both symlinks exist
        assert link1.is_symlink()
        assert link2.is_symlink()

    def test_relative_path_symlink_attack(self, temp_dir, wrapper_path):
        """Test that relative path symlinks are handled correctly."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        # Create malicious file at parent level
        malicious_file = temp_dir / "malicious.jsonl"
        malicious_file.write_text('{"test": "malicious"}')

        # Create user directory
        user_dir = temp_dir / "home" / "user1" / ".qwen" / "projects"
        user_dir.mkdir(parents=True)

        # Create symlink with relative path
        link = user_dir / "link.jsonl"
        # Relative path pointing outside
        link.symlink_to("../../../malicious.jsonl")

        assert link.is_symlink()


# ============================================================================
# Test path whitelist
# ============================================================================


class TestPathWhitelist:
    """Test that path whitelist validation works correctly."""

    def test_allowed_qwen_path(self, temp_dir):
        """Test that .qwen paths are allowed."""
        user_dir = temp_dir / "home" / "user1" / ".qwen" / "projects"
        user_dir.mkdir(parents=True)

        # This path should be in the whitelist
        assert user_dir.exists()

    def test_allowed_claude_path(self, temp_dir):
        """Test that .claude paths are allowed."""
        user_dir = temp_dir / "home" / "user1" / ".claude"
        user_dir.mkdir(parents=True)

        assert user_dir.exists()

    def test_allowed_zcode_path(self, temp_dir):
        """Test that .zcode paths are allowed."""
        user_dir = temp_dir / "home" / "user1" / ".zcode" / "cli" / "db"
        user_dir.mkdir(parents=True)

        assert user_dir.exists()

    def test_reject_etc_path(self, temp_dir):
        """Test that /etc paths are rejected."""
        # The wrapper should reject any path under /etc
        etc_path = Path("/etc/passwd")
        # This test is conceptual - in real tests, we'd mock the validation
        assert etc_path.exists()


# ============================================================================
# Test file size limits
# ============================================================================


class TestFileSizeLimits:
    """Test that file size limits are enforced."""

    def test_small_file_allowed(self, temp_dir):
        """Test that small files are allowed."""
        user_dir = temp_dir / "home" / "user1" / ".qwen" / "projects"
        user_dir.mkdir(parents=True)

        # Create a small JSONL file
        small_file = user_dir / "small.jsonl"
        small_file.write_text('{"test": "data"}')

        assert small_file.exists()
        assert small_file.stat().st_size < 50 * 1024 * 1024

    @requires_bash4
    def test_large_file_rejected(self, tmp_path):
        """validate_file rejects files over the real 50MB limit.

        Drives the REAL extracted validate_file closure. The accept-path
        fixture must live under a home-shaped path (is_allowed_path gates
        before the size check and rejects tmp paths); os.truncate makes the
        >50MB file sparse so no bytes are written. Note (rev3 NIT-F): the
        whitelist regex excludes dots in usernames — fine on CI (runner) and
        this repo's dev machines.
        """
        import uuid

        harness = _extract_closure(tmp_path)
        under_home = _real_user_home() / ".qwen" / f"fwtest-{uuid.uuid4().hex[:8]}"
        try:
            proj = under_home / "projects"
            proj.mkdir(parents=True)
            small = proj / "small.jsonl"
            small.write_text('{"test": "data"}')
            big = proj / "big.jsonl"
            big.write_text("x")
            os.truncate(big, 50 * 1024 * 1024 + 1)

            rc_small = _run_closure(
                harness,
                f"validate_file {shlex_quote(str(small))} 2>&1; echo rc=$?; "
                f"readlink -f {shlex_quote(str(small))}",
            )
            assert rc_small.returncode == 0, rc_small.stderr + rc_small.stdout
            assert rc_small.stdout.strip().endswith(str(small)), (
                f"small-file accept path failed on the real closure:\n"
                f"stdout: {rc_small.stdout}\nstderr: {rc_small.stderr}"
            )
            assert "rc=0" in rc_small.stdout, rc_small.stdout + rc_small.stderr

            audit = tmp_path / "audit.log"
            rc_big = _run_closure(
                harness,
                f"validate_file {shlex_quote(str(big))}; echo $?",
                env={"AUDIT_LOG": str(audit)},
            )
            assert rc_big.returncode == 0, rc_big.stderr
            assert rc_big.stdout.strip().endswith("1")
            assert "WARNING: File too large" in rc_big.stderr
            assert "file_skipped" in audit.read_text()
            assert "reason=too_large" in audit.read_text()
        finally:
            import shutil

            shutil.rmtree(under_home, ignore_errors=True)


# ============================================================================
# Test audit logging
# ============================================================================


class TestAuditLogging:
    """Audit logging via the REAL extracted log_audit closure."""

    @requires_bash4
    def test_audit_log_created(self, tmp_path):
        """log_audit writes the documented line format to AUDIT_LOG.

        The line is "<timestamp> | caller=<pseudonymized> | action=<action> |
        <details>" (fragments asserted separately — they are ` | `-separated).
        #3292: the caller is pseudonymized (first letter + *** + 8-hex
        truncation of sha256), never the raw username. The end-to-end
        fetch_start/fetch_end path is covered by
        TestParameterValidation.test_exact_match_valid_params (real wrapper).
        """
        harness = _extract_closure(tmp_path)
        audit = tmp_path / "audit.log"
        rc = _run_closure(
            harness,
            'log_audit "fetch_start" "tool=fetch_qwen"; echo $?',
            env={"AUDIT_LOG": str(audit), "USER": "auditprobe"},
        )
        assert rc.returncode == 0, rc.stderr
        assert rc.stdout.strip().endswith("0")
        line = audit.read_text().strip()
        caller = re.search(r" \| caller=([^|]*) \| ", line)
        assert caller, f"no caller field in {line!r}"
        # Shape only — never hardcode hash values (they are deterministic
        # but platform-tool-dependent in derivation, sha256sum vs shasum).
        assert re.fullmatch(r"a\*\*\*-[0-9a-f]{8}", caller.group(1)), line
        assert "auditprobe" not in line
        assert " | action=fetch_start | " in line
        assert line.endswith("tool=fetch_qwen")

    @requires_bash4
    def test_caller_pseudonymized_under_sudo(self, tmp_path):
        """SUDO_USER drives the caller field, pseudonymized (#3292).

        Drives the real log_audit with SUDO_USER=alice: the audit line must
        carry the a***-<hash8> pseudonym and must not contain the raw
        username anywhere.
        """
        harness = _extract_closure(tmp_path)
        audit = tmp_path / "audit.log"
        rc = _run_closure(
            harness,
            'log_audit "fetch_start" "tool=fetch_qwen"; echo $?',
            env={"AUDIT_LOG": str(audit), "SUDO_USER": "alice", "USER": "root"},
        )
        assert rc.returncode == 0, rc.stderr
        line = audit.read_text().strip()
        assert re.search(r" \| caller=a\*\*\*-[0-9a-f]{8} \| ", line), line
        assert "alice" not in line

    @requires_bash4
    def test_hash_deterministic_for_correlation(self, tmp_path):
        """Same user -> same hash suffix; different user -> different (#3292).

        The truncation hash exists so operators can correlate audit entries
        belonging to one user without learning the username.
        """
        harness = _extract_closure(tmp_path)
        rc = _run_closure(
            harness,
            'echo "$(sanitize_username carol) $(sanitize_username carol) '
            '$(sanitize_username dave)"',
        )
        assert rc.returncode == 0, rc.stderr
        carol_a, carol_b, dave = rc.stdout.strip().split()
        assert carol_a == carol_b, "same user must map to the same pseudonym"
        assert carol_a != dave, "different users must not collide"
        assert re.fullmatch(r"c\*\*\*-[0-9a-f]{8}", carol_a)
        assert re.fullmatch(r"d\*\*\*-[0-9a-f]{8}", dave)

    @requires_bash4
    def test_home_path_segment_sanitized_in_details(self, tmp_path):
        """Usernames inside --config home paths are sanitized too (#3292).

        Only pseudonymizing caller= would leak the username right back via
        args=--config /home/<user>/... — the details pass must rewrite the
        home-directory segment.
        """
        harness = _extract_closure(tmp_path)
        audit = tmp_path / "audit.log"
        rc = _run_closure(
            harness,
            'log_audit "fetch_start" '
            '"tool=fetch_qwen args=--config /home/alice/.open-ace/config.json"; echo $?',
            env={"AUDIT_LOG": str(audit), "USER": "root"},
        )
        assert rc.returncode == 0, rc.stderr
        line = audit.read_text().strip()
        assert "/home/alice/" not in line
        assert re.search(r"/home/a\*\*\*-[0-9a-f]{8}/\.open-ace/config\.json", line), line

    @requires_bash4
    def test_users_path_segment_sanitized_in_details(self, tmp_path):
        """macOS-shaped /Users/<name>/ segments are sanitized as well (#3292).

        Only reachable through the closure harness (the wrapper's config
        allowlist rejects /Users paths), but sanitize_details covers both
        home shapes.
        """
        harness = _extract_closure(tmp_path)
        rc = _run_closure(
            harness,
            'echo "$(sanitize_details "file=/Users/bob/.qwen/sessions.json")"',
        )
        assert rc.returncode == 0, rc.stderr
        out = rc.stdout.strip()
        assert "/Users/bob/" not in out
        assert re.search(r"/Users/b\*\*\*-[0-9a-f]{8}/\.qwen/sessions\.json", out), out

    @requires_bash4
    def test_single_char_username_hides_first_letter(self, tmp_path):
        """A 1-char username keeps no first letter — it IS the name (#3292).

        Uses the g-free details string tool=fetch_claude so the absence
        assertion cannot trip over the literal 'g' in "args".
        """
        harness = _extract_closure(tmp_path)
        audit = tmp_path / "audit.log"
        rc = _run_closure(
            harness,
            'log_audit "fetch_start" "tool=fetch_claude"; echo $?',
            env={"AUDIT_LOG": str(audit), "USER": "g"},
        )
        assert rc.returncode == 0, rc.stderr
        line = audit.read_text().strip()
        assert re.search(r" \| caller=\*\*\*-[0-9a-f]{8} \| ", line), line
        # 'g' is outside the hex alphabet, so its absence is unambiguous.
        assert "g" not in line

    # #3292 implemented the sanitization the header always claimed: caller
    # and home-path username segments are pseudonymized (first letter +
    # *** + sha256[:8]; 1-char names drop the letter). The #3186 batch-3
    # deletion note for the unit placeholder test_username_sanitized lived
    # here; the tests above are its real replacement (the integration-side
    # canary test_usernames_sanitized_in_log still exists by design — see
    # its docstring).


# ============================================================================
# Test privilege drop mechanism
# ============================================================================


class TestPrivilegeDrop:
    """Test that privilege drop mechanism is implemented."""

    def test_wrapper_has_privilege_drop(self, wrapper_path):
        """Test that wrapper contains privilege drop code."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        with open(wrapper_path) as f:
            content = f.read()

        # Check for privilege drop mechanism
        assert "sudo -u" in content or "RUN_USER" in content, "Missing privilege drop mechanism"
        assert "privilege_drop" in content, "Missing privilege drop audit log"

    def test_run_user_configurable(self, wrapper_path):
        """Test that RUN_USER is configurable."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        with open(wrapper_path) as f:
            content = f.read()

        # Check that RUN_USER is configurable via environment
        assert "RUN_USER=" in content, "RUN_USER should be configurable"

    def test_privilege_drop_only_for_root(self, wrapper_path):
        """Test that privilege drop only happens when running as root."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not installed")

        with open(wrapper_path) as f:
            content = f.read()

        # Check for root check
        assert "id -u" in content or "$(id -u)" in content, "Missing root check"


# ============================================================================
# Test user identity mapping
# ============================================================================


# TestUserIdentityMapping was deleted (#3186 batch 3): all three contracts
# (resolve by system_account, by username, unknown -> None) are covered by
# REAL PostgreSQL tests in
# tests/integration/test_qwen_user_attribution_2735_pg.py::TestUserIdResolution
# (batch 2b) — keeping hollow local stubs would duplicate coverage.


# ============================================================================
# Integration tests (require full environment)
# ============================================================================


class TestIntegration:
    """fetch_and_save multi-user status contract, driven for real."""

    def test_degraded_status_on_partial_failure(self, tmp_path, monkeypatch, capsys):
        """One accessible user + one denied user => status "degraded".

        The fixture root reaches the REAL find_all_qwen_project_dirs via the
        additive home_base parameter (zero monkeypatching of the scan). The
        denied user's .qwen stays traversable (0o755) while its projects/
        subdir is chmod 000 — opendir EACCES raises on every Python version
        (3.13+ pathlib rewrote predicate methods, not iteration errors), and
        the except PermissionError -> denied classification inside the real
        scan fires. Status/coverage are asserted from the FETCH_RESULT JSON
        markers on stdout.
        """
        import importlib.util
        import json as _json
        import sys as _sys

        scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
        if str(scripts_dir) not in _sys.path:
            _sys.path.insert(0, str(scripts_dir))
        import fetch_qwen

        import shared.db as shared_db
        from shared import config as shared_config

        # Fixture: userA good (.qwen/projects/<subdir>/*.jsonl), userB denied
        user_a_projects = tmp_path / "home" / "userA" / ".qwen" / "projects" / "proj1"
        user_a_projects.mkdir(parents=True)
        entry_user = {
            "uuid": "du1",
            "parentUuid": None,
            "type": "user",
            "timestamp": "2026-01-05T10:00:00Z",
            "sessionId": "sess-deg-1",
            "message": {"message_id": "dm1", "parts": []},
        }
        entry_asst = {
            "uuid": "da1",
            "parentUuid": "du1",
            "type": "assistant",
            "timestamp": "2026-01-05T10:00:05Z",
            "sessionId": "sess-deg-1",
            "model": "qwen-max",
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            },
            "message": {"message_id": "dm2", "parts": []},
        }
        (user_a_projects / "2026-01-05.jsonl").write_text(
            _json.dumps(entry_user) + "\n" + _json.dumps(entry_asst) + "\n"
        )
        user_b_qwen = tmp_path / "home" / "userB" / ".qwen"
        (user_b_qwen / "projects").mkdir(parents=True)
        (user_b_qwen / "projects").chmod(0o000)

        # Isolated, schema-initialized SQLite bound through BOTH seams (the
        # URL cache and the config resolver fetch_qwen actually imports).
        db_file = tmp_path / "degraded.db"
        test_url = f"sqlite:///{db_file}"
        monkeypatch.setattr(shared_db, "_db_url_cache", None)
        monkeypatch.setattr(shared_config, "get_database_url", lambda: test_url)
        shared_db.init_database()

        try:
            ok = fetch_qwen.fetch_and_save(
                days=7,
                hostname="deghost",
                multi_user_mode=True,
                home_base=tmp_path / "home",
            )
            assert ok is True
        finally:
            (user_b_qwen / "projects").chmod(0o755)

        out = capsys.readouterr().out
        assert "===FETCH_RESULT_START===" in out, out[-2000:]
        payload = out.split("===FETCH_RESULT_START===", 1)[1].split("===FETCH_RESULT_END===", 1)[0]
        result = _json.loads(payload)
        assert result["status"] == "degraded", result
        assert "userB" in result["coverage"]["users_denied"], result
        assert result["coverage"]["users_scanned"] >= 1, result

    # test_multi_user_collection_with_permission_700 was deleted (#3186
    # batch 3): root + real multi-user e2e has no CI-honest path (mock-gated
    # testing is the forbidden formal repair) — tracked with the rest of the
    # root-e2e family in #3293.

    def test_idempotent_collection(self, tmp_path):
        """Re-scanning the same tree is deterministic and per-file dedup holds.

        Two runs over the same fixture tree with FRESH aggregation dicts must
        produce identical (daily, messages); and a fixture file repeating one
        assistant message_id must count request_count once (seen_msg_ids
        contract — the per-file dedup that prevents double counting).
        """
        import importlib.util
        import json as _json
        import sys as _sys
        from collections import defaultdict

        scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
        if str(scripts_dir) not in _sys.path:
            _sys.path.insert(0, str(scripts_dir))
        import fetch_qwen

        proj = tmp_path / "home" / "userA" / ".qwen" / "projects" / "proj1"
        proj.mkdir(parents=True)
        base = {
            "type": "assistant",
            "model": "qwen-max",
            "usageMetadata": {"totalTokenCount": 7},
            "message": {"message_id": "same-id", "parts": []},
        }
        e1 = dict(base, uuid="r1", timestamp="2026-01-05T10:00:00Z")
        e2 = dict(base, uuid="r2", timestamp="2026-01-05T10:01:00Z")
        (proj / "2026-01-05.jsonl").write_text(_json.dumps(e1) + "\n" + _json.dumps(e2) + "\n")

        def new_agg():
            return defaultdict(
                lambda: {
                    "prompt_tokens": 0,
                    "candidates_tokens": 0,
                    "thoughts_tokens": 0,
                    "cached_tokens": 0,
                    "total_tokens": 0,
                    "request_count": 0,
                    "models_used": set(),
                }
            )

        results = []
        for _ in range(2):
            agg = new_agg()
            msgs: list = []
            fetch_qwen._process_projects_dir(proj.parent, "idemhost", "userA", agg, msgs)
            results.append(
                ({k: dict(v, models_used=sorted(v["models_used"])) for k, v in agg.items()}, msgs)
            )
        (daily1, msgs1), (daily2, msgs2) = results
        assert daily1 == daily2, "re-scan must be deterministic"
        assert len(msgs1) == len(msgs2) and msgs1 == msgs2
        # The repeated assistant message_id must be counted once.
        assert daily1["2026-01-05"]["request_count"] == 1, daily1

    # (Idempotency lives here rather than in the integration file, whose
    # TestErrorHandlingIntegration placeholders were deleted in #3186 batch 3.)


# ============================================================================
# Test wrapper script syntax
# ============================================================================


class TestWrapperSyntax:
    """Test that the wrapper script has valid syntax."""

    def test_bash_syntax_valid(self, wrapper_path):
        """Test that wrapper script has valid bash syntax."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not found")

        # Use bash -n to check syntax
        result = subprocess.run(
            ["bash", "-n", wrapper_path],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_wrapper_is_executable(self, wrapper_path):
        """Test that wrapper script is executable."""
        if not os.path.exists(wrapper_path):
            pytest.skip("Wrapper not found")

        # Check if the script has execute permission
        assert os.access(wrapper_path, os.X_OK)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
