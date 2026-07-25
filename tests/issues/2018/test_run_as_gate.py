"""Gate-level tests for ``openace-run-as --isolated`` (Issue #2018).

These verify how the bash wrapper interprets the validator's verdict — the
mapping from helper output to exit code + audit line — WITHOUT needing root or
setfacl. The validation gate runs before any privileged work, so rejection
cases exit cleanly on macOS/Linux alike. The validator is faked so we can drive
each verdict deterministically.

The validator's own decision logic is covered by ``test_validate_launch.py``;
the full root+ACL integration (accept → grant → rollback) is a Linux-root
suite (see ``test_run_as_integration.py``).
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_WRAPPER = _ROOT / "scripts" / "openace-run-as.sh"


def _fake_validator(tmp_path: Path, behavior: str) -> Path:
    """Create an executable fake validator emitting a given verdict."""
    script = tmp_path / f"fake-validator-{behavior}"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            case "{behavior}" in
              accept)         exit 0 ;;
              reject_account) echo "openace-validate-launch: reject_account: expected openace-agent" >&2; exit 1 ;;
              reject_path)    echo "openace-validate-launch: reject_path: outside allowlist" >&2; exit 1 ;;
              reject_conf)    echo "openace-validate-launch: reject_conf: conf not root-owned" >&2; exit 1 ;;
              *)              exit 2 ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _run_wrapper(tmp_path: Path, validator: Path, *, account="openace-agent"):
    """Run the wrapper through its validation gate with a faked validator.

    Uses an absolute project path (passes the cheap pre-check) and a throwaway
    audit log + conf path.
    """
    audit_log = tmp_path / "run-as-audit.log"
    env = {
        **os.environ,
        "OPENACE_VALIDATE_LAUNCH": str(validator),
        "OPENACE_LAUNCHER_CONF": str(tmp_path / "absent.conf"),
        "OPENACE_AUDIT_LOG": str(audit_log),
    }
    # Prevent any inherited OPENACE_* override from the developer shell.
    env.pop("OPENACE_RUN_AS", None)
    result = subprocess.run(
        ["bash", str(_WRAPPER), "--isolated", account, "/home/some/repo", "/usr/bin/true"],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    return result, audit_log


class TestRunAsGate:
    def test_reject_account_exits_67(self, tmp_path):
        result, audit = _run_wrapper(tmp_path, _fake_validator(tmp_path, "reject_account"))
        assert result.returncode == 67
        assert "reject_account" in result.stderr
        assert "reject_account" in audit.read_text()

    def test_reject_path_exits_64(self, tmp_path):
        result, audit = _run_wrapper(tmp_path, _fake_validator(tmp_path, "reject_path"))
        assert result.returncode == 64
        assert "reject_path" in audit.read_text()

    def test_reject_conf_exits_66(self, tmp_path):
        result, audit = _run_wrapper(tmp_path, _fake_validator(tmp_path, "reject_conf"))
        assert result.returncode == 66
        assert "reject_conf" in audit.read_text()

    def test_missing_validator_fails_closed(self, tmp_path):
        # A missing helper binary must fail closed (never an unvalidated grant).
        audit_log = tmp_path / "audit.log"
        env = {
            **os.environ,
            "OPENACE_VALIDATE_LAUNCH": str(tmp_path / "does-not-exist"),
            "OPENACE_LAUNCHER_CONF": str(tmp_path / "absent.conf"),
            "OPENACE_AUDIT_LOG": str(audit_log),
        }
        env.pop("OPENACE_RUN_AS", None)
        result = subprocess.run(
            [
                "bash",
                str(_WRAPPER),
                "--isolated",
                "openace-agent",
                "/home/some/repo",
                "/usr/bin/true",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
        assert result.returncode == 66
        assert "validator unavailable" in result.stderr
        assert "reject_missing_validator" in audit_log.read_text()

    def test_accept_logs_accept_before_privileged_work(self, tmp_path):
        # On macOS the wrapper exits non-zero AFTER the gate (no flock/setfacl),
        # but the accept audit line must already be written. We only assert the
        # audit trail here; the full accept→grant flow lives in the Linux suite.
        result, audit = _run_wrapper(tmp_path, _fake_validator(tmp_path, "accept"))
        assert "result=accept" in audit.read_text()
        # And it must NOT have been re-classified as a rejection.
        assert "reject" not in audit.read_text()

    def test_audit_omits_command_args(self, tmp_path):
        # The audit line must never echo the command/args (proxy-token hygiene).
        result, audit = _run_wrapper(tmp_path, _fake_validator(tmp_path, "reject_account"))
        line = audit.read_text()
        assert "SECRET" not in line
        assert "/usr/bin/true" not in line
