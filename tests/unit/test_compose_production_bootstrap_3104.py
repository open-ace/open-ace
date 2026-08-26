import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bootstrap_compose_env.py"
OVERLAY = ROOT / "docker-compose.multi-user.yml"


def fake_docker(tmp_path: Path, volumes: str = "", exit_code: int = 0) -> Path:
    binary = tmp_path / "docker"
    binary.write_text(f"#!/bin/sh\nprintf '%s' '{volumes}'\nexit {exit_code}\n")
    binary.chmod(0o755)
    return binary


def run_bootstrap(project: Path, bin_dir: Path, extra_env=None):
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    for key in ("DB_PASSWORD", "SECRET_KEY", "OPENACE_ENCRYPTION_KEY", "UPLOAD_AUTH_KEY", "OPENACE_SECURITY_MODE"):
        env.pop(key, None)
    env.update(extra_env or {})
    return subprocess.run(
        ["python3", str(SCRIPT), "--project-root", str(project)],
        capture_output=True, text=True, env=env, timeout=20,
    )


def values(path: Path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


def test_fresh_bootstrap_is_secure_and_idempotent(tmp_path):
    fake_docker(tmp_path)
    result = run_bootstrap(tmp_path, tmp_path)
    assert result.returncode == 0, result.stderr
    env_path = tmp_path / ".env"
    first = env_path.read_bytes()
    configured = values(env_path)
    assert configured["OPENACE_SECURITY_MODE"] == "production"
    assert all(configured[key] for key in ("DB_PASSWORD", "SECRET_KEY", "OPENACE_ENCRYPTION_KEY", "UPLOAD_AUTH_KEY"))
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert run_bootstrap(tmp_path, tmp_path).returncode == 0
    assert env_path.read_bytes() == first


def test_preserves_comments_unknown_keys_and_existing_secrets(tmp_path):
    fake_docker(tmp_path)
    original_password = "existing-strong-password"
    (tmp_path / ".env").write_text(f"# keep me\nUNKNOWN=value\nDB_PASSWORD={original_password}\n")
    result = run_bootstrap(tmp_path, tmp_path)
    assert result.returncode == 0, result.stderr
    content = (tmp_path / ".env").read_text()
    assert "# keep me" in content and "UNKNOWN=value" in content
    assert values(tmp_path / ".env")["DB_PASSWORD"] == original_password


def test_existing_project_volume_blocks_missing_password(tmp_path):
    fake_docker(tmp_path, "project_postgres-data\\n")
    result = run_bootstrap(tmp_path, tmp_path)
    assert result.returncode != 0
    assert "existing postgres-data volume" in result.stderr
    assert not (tmp_path / ".env").exists()


def test_unknown_volume_state_fails_closed(tmp_path):
    fake_docker(tmp_path, exit_code=1)
    result = run_bootstrap(tmp_path, tmp_path)
    assert result.returncode != 0
    assert "volume state is unknown" in result.stderr


def test_shell_values_are_persisted_without_logging(tmp_path):
    fake_docker(tmp_path)
    supplied = {
        "DB_PASSWORD": "shell-strong-password",
        "SECRET_KEY": "a" * 64,
        "OPENACE_ENCRYPTION_KEY": "b" * 64,
        "UPLOAD_AUTH_KEY": "c" * 64,
        "OPENACE_SECURITY_MODE": "production",
    }
    result = run_bootstrap(tmp_path, tmp_path, supplied)
    assert result.returncode == 0, result.stderr
    assert values(tmp_path / ".env")["DB_PASSWORD"] == supplied["DB_PASSWORD"]
    assert all(secret not in result.stdout + result.stderr for secret in supplied.values())


def test_conflicting_shell_and_env_values_fail_closed(tmp_path):
    fake_docker(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENACE_SECURITY_MODE=production\nDB_PASSWORD=file-strong-password\n"
        f"SECRET_KEY={'a' * 64}\nOPENACE_ENCRYPTION_KEY={'b' * 64}\nUPLOAD_AUTH_KEY={'c' * 64}\n"
    )
    result = run_bootstrap(tmp_path, tmp_path, {"DB_PASSWORD": "different-shell-password"})
    assert result.returncode != 0
    assert "differs between the shell and .env" in result.stderr
    assert "different-shell-password" not in result.stderr


def test_symlink_env_is_rejected(tmp_path):
    fake_docker(tmp_path)
    target = tmp_path / "target"
    target.write_text("")
    (tmp_path / ".env").symlink_to(target)
    result = run_bootstrap(tmp_path, tmp_path)
    assert result.returncode != 0
    assert "symbolic-link" in result.stderr


def test_overlay_has_fail_fast_contract_for_all_services():
    content = OVERLAY.read_text()
    for key in ("DB_PASSWORD", "SECRET_KEY", "OPENACE_ENCRYPTION_KEY"):
        assert content.count(f"${{{key}:?") >= 2
    assert "POSTGRES_PASSWORD=${DB_PASSWORD:?" in content
