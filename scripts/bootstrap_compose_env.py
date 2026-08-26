#!/usr/bin/env python3
"""Safely bootstrap persistent secrets for multi-user Compose deployments."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

REQUIRED = ("DB_PASSWORD", "SECRET_KEY", "OPENACE_ENCRYPTION_KEY")
ALL_KEYS = ("OPENACE_SECURITY_MODE", *REQUIRED, "UPLOAD_AUTH_KEY")
FORBIDDEN_PASSWORDS = {
    "ace-secret", "dev-password-change-in-production", "change-me", "password",
    "admin", "postgres", "123456",
}


def parse_env(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.rstrip("\r\n"))
        if match:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[match.group(1)] = value
    return values


def validate(values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if values.get("OPENACE_SECURITY_MODE") != "production":
        errors.append("OPENACE_SECURITY_MODE must be production")
    password = values.get("DB_PASSWORD", "")
    if len(password) < 9 or password in FORBIDDEN_PASSWORDS:
        errors.append("DB_PASSWORD must be a strong non-default value (9+ characters)")
    if len(values.get("SECRET_KEY", "")) < 32:
        errors.append("SECRET_KEY must contain at least 32 characters")
    if len(values.get("OPENACE_ENCRYPTION_KEY", "")) < 32:
        errors.append("OPENACE_ENCRYPTION_KEY must contain at least 32 characters")
    if not values.get("UPLOAD_AUTH_KEY"):
        errors.append("UPLOAD_AUTH_KEY is required for complete upload functionality")
    return errors


def compose_project_name(project_root: Path) -> str:
    explicit = os.environ.get("COMPOSE_PROJECT_NAME", "").strip()
    raw = explicit or project_root.name
    normalized = re.sub(r"[^a-z0-9_-]", "", raw.lower())
    normalized = re.sub(r"^[^a-z0-9]+", "", normalized)
    if not normalized:
        raise RuntimeError("cannot determine the Compose project name")
    return normalized


def find_postgres_volumes(project_root: Path) -> list[str]:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker CLI is unavailable; existing volume state is unknown")
    project = compose_project_name(project_root)
    result = subprocess.run(
        [docker, "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}",
         "--filter", "label=com.docker.compose.volume=postgres-data"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("cannot inspect Docker volumes; existing volume state is unknown")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def safe_external_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    if "\n" in value or "\r" in value or not re.fullmatch(r"[A-Za-z0-9_.~:/+@%-]+", value):
        raise RuntimeError(f"{name} from the shell cannot be safely persisted; put it in .env explicitly")
    return value


def generated_value(name: str) -> str:
    if name == "OPENACE_SECURITY_MODE":
        return "production"
    if name == "DB_PASSWORD":
        return secrets.token_urlsafe(32)
    return secrets.token_hex(32)


def update_lines(lines: list[str], values: dict[str, str]) -> list[str]:
    result = list(lines)
    positions: dict[str, int] = {}
    for index, line in enumerate(result):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if match:
            positions[match.group(1)] = index
    for key, value in values.items():
        rendered = f"{key}={value}\n"
        if key in positions:
            result[positions[key]] = rendered
        else:
            result.append(rendered)
    return result


def atomic_write(path: Path, lines: list[str]) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".env.tmp.", dir=path.parent, text=True)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def bootstrap(project_root: Path, check_only: bool = False) -> None:
    env_path = project_root / ".env"
    if env_path.is_symlink():
        raise RuntimeError("refusing to read or replace a symbolic-link .env")
    lock_path = project_root / ".env.bootstrap.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(lock_fd, "r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True) if env_path.exists() else []
            current = parse_env(lines)
            for key in ALL_KEYS:
                external = os.environ.get(key)
                if external and current.get(key) and external != current[key]:
                    raise RuntimeError(
                        f"{key} differs between the shell and .env; refusing an ambiguous deployment"
                    )
            if check_only:
                errors = validate({**current, **{key: os.environ[key] for key in ALL_KEYS if os.environ.get(key)}})
                if errors:
                    raise RuntimeError("invalid production configuration:\n- " + "\n- ".join(errors))
                return
            missing_persisted_db = not current.get("DB_PASSWORD")
            if missing_persisted_db:
                volumes = find_postgres_volumes(project_root)
                if volumes:
                    raise RuntimeError(
                        "an existing postgres-data volume was detected while DB_PASSWORD is missing; "
                        "preserve data with a controlled role-password migration, or manually rebuild only after confirming no data is needed"
                    )
            additions: dict[str, str] = {}
            for key in ALL_KEYS:
                if current.get(key):
                    continue
                additions[key] = safe_external_value(key) or generated_value(key)
            effective = {**current, **additions}
            errors = validate(effective)
            if errors:
                raise RuntimeError("invalid production configuration:\n- " + "\n- ".join(errors))
            if additions or not env_path.exists():
                atomic_write(env_path, update_lines(lines, additions))
            else:
                os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)
    finally:
        if os.path.exists(lock_path):
            os.chmod(lock_path, stat.S_IRUSR | stat.S_IWUSR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        bootstrap(args.project_root.resolve(), args.check)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1
    print("Production Compose environment is valid." if args.check else "Production Compose environment is ready (.env mode 0600).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
