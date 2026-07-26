"""Gate-level tests for ``openace-run-as --isolated --task-id`` (Issue #2020).

The launcher must accept a per-attempt ``--task-id`` and reject unsafe values
(path separators, spaces, empty) BEFORE any privileged work, so the reject
path is exercisable on macOS/Linux without root or setfacl — mirroring the
``test_run_as_gate.py`` (Issue #2018) approach.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_WRAPPER = _ROOT / "scripts" / "openace-run-as.sh"


def _fake_validator(tmp_path: Path, behavior: str) -> Path:
    script = tmp_path / f"fake-validator-{behavior}"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            case "{behavior}" in
              accept)         exit 0 ;;
              reject_account) echo "openace-validate-launch: reject_account" >&2; exit 1 ;;
              *)              exit 2 ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _run(tmp_path: Path, validator: Path, *, task_id=None, account="openace-agent"):
    audit_log = tmp_path / "run-as-audit.log"
    env = {
        **os.environ,
        "OPENACE_VALIDATE_LAUNCH": str(validator),
        "OPENACE_LAUNCHER_CONF": str(tmp_path / "absent.conf"),
        "OPENACE_AUDIT_LOG": str(audit_log),
    }
    env.pop("OPENACE_RUN_AS", None)
    argv = ["bash", str(_WRAPPER), "--isolated"]
    if task_id is not None:
        argv += ["--task-id", task_id]
    argv += [account, "/home/some/repo", "/usr/bin/true"]
    result = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=20)
    return result


class TestTaskIdGate:
    def test_rejects_path_separator_in_task_id(self, tmp_path):
        result = _run(tmp_path, _fake_validator(tmp_path, "accept"), task_id="a/b")
        assert result.returncode == 64
        assert "task-id" in result.stderr.lower()

    def test_rejects_space_in_task_id(self, tmp_path):
        result = _run(tmp_path, _fake_validator(tmp_path, "accept"), task_id="a b")
        assert result.returncode == 64

    def test_rejects_empty_task_id_flag(self, tmp_path):
        # --task-id with no following value is a usage error.
        audit_log = tmp_path / "run-as-audit.log"
        env = {
            **os.environ,
            "OPENACE_VALIDATE_LAUNCH": str(_fake_validator(tmp_path, "accept")),
            "OPENACE_LAUNCHER_CONF": str(tmp_path / "absent.conf"),
            "OPENACE_AUDIT_LOG": str(audit_log),
        }
        env.pop("OPENACE_RUN_AS", None)
        result = subprocess.run(
            [
                "bash",
                str(_WRAPPER),
                "--isolated",
                "--task-id",
                "openace-agent",
                "/home/some/repo",
                "/usr/bin/true",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
        # Here "openace-agent" was consumed as the task_id value, then the
        # positional parse finds too few args → usage error 64.
        assert result.returncode == 64

    def test_valid_task_id_does_not_short_circuit(self, tmp_path):
        # A valid task_id must not bypass account validation: a reject_account
        # validator still exits 67.
        result = _run(tmp_path, _fake_validator(tmp_path, "reject_account"), task_id="abc-123")
        assert result.returncode == 67
