"""Release gates must reject metadata that describes different products."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_release_version

pytestmark = [pytest.mark.regression, pytest.mark.issue(3469)]

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check_release_version.py"
RELEASE_FILES = (
    "pyproject.toml",
    "frontend/package.json",
    "frontend/package-lock.json",
    "Dockerfile",
    "CHANGELOG.md",
)


def _copy_release_files(target_root: Path) -> None:
    for name in RELEASE_FILES:
        target = target_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / name, target)


@pytest.fixture
def release_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _copy_release_files(tmp_path)
    monkeypatch.setattr(check_release_version, "ROOT", tmp_path)
    return tmp_path


def _run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_current_release_metadata_agrees(release_tree: Path) -> None:
    version = check_release_version.project_version()
    assert check_release_version.check(f"v{version}", require_changelog=True) == []


def test_release_gate_rejects_mismatched_tag_and_frontend(release_tree: Path) -> None:
    package_path = release_tree / "frontend/package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["version"] = "0.2.0"
    package_path.write_text(json.dumps(package), encoding="utf-8")

    errors = check_release_version.check("v9.9.9", require_changelog=False)
    assert any("frontend/package.json" in error for error in errors)
    assert any("release tag" in error for error in errors)


def test_release_gate_rejects_legacy_docker_label(release_tree: Path) -> None:
    dockerfile_path = release_tree / "Dockerfile"
    dockerfile_path.write_text(
        dockerfile_path.read_text(encoding="utf-8") + '\nLABEL version="1.0.0"\n',
        encoding="utf-8",
    )

    assert any("legacy static LABEL" in error for error in check_release_version.check(None, False))


@pytest.mark.parametrize(
    "label",
    [
        'LABEL org.opencontainers.image.version="${OPENACE_VERSION}"',
        "LABEL org.opencontainers.image.version=${OPENACE_VERSION}",
        "LABEL org.opencontainers.image.version=$OPENACE_VERSION",
        'LABEL maintainer="Open ACE" \\\n'
        "      # continuation comment\n"
        '      org.opencontainers.image.version="${OPENACE_VERSION}"',
    ],
)
def test_docker_oci_label_accepts_equivalent_forms(label: str) -> None:
    dockerfile = f"FROM python:3.11-slim AS production\nARG OPENACE_VERSION=dev\n{label}\n"
    assert check_release_version.dockerfile_errors(dockerfile) == []


@pytest.mark.parametrize(
    ("dockerfile", "message"),
    [
        # The only mention of the OCI label is a comment: nothing sets it.
        ('# LABEL org.opencontainers.image.version="${OPENACE_VERSION}"\n', "OCI version"),
        # Hard-coded instead of derived from the build arg.
        ('LABEL org.opencontainers.image.version="2.1.0"\n', "OCI version"),
        # Docker does not expand variables inside single quotes.
        ("LABEL org.opencontainers.image.version='${OPENACE_VERSION}'\n", "OCI version"),
        ("LABEL \"org.opencontainers.image.version\"='${OPENACE_VERSION}'\n", "OCI version"),
        # Legacy static label hidden in a multi-key instruction.
        (
            'LABEL a=b \\\n  version="1.0.0" \\\n'
            '  org.opencontainers.image.version="${OPENACE_VERSION}"\n',
            "legacy static LABEL",
        ),
        # Label keys are matched case-insensitively for the legacy key.
        (
            'LABEL Version="1.0.0" org.opencontainers.image.version="${OPENACE_VERSION}"\n',
            "legacy static LABEL",
        ),
    ],
)
def test_docker_oci_label_rejects_missing_or_static_values(dockerfile: str, message: str) -> None:
    errors = check_release_version.dockerfile_errors(
        "FROM python:3.11-slim AS production\n" + dockerfile
    )
    assert any(message in error for error in errors), errors


def test_docker_oci_label_must_be_in_the_production_stage() -> None:
    dockerfile = (
        "FROM node:20-alpine AS frontend-builder\n"
        'LABEL org.opencontainers.image.version="${OPENACE_VERSION}"\n'
        "FROM python:3.11-slim AS production\n"
        "FROM production AS development\n"
    )
    errors = check_release_version.dockerfile_errors(dockerfile)
    assert any("production stage" in error for error in errors), errors


def test_static_oci_label_in_another_stage_is_named_as_static() -> None:
    dockerfile = (
        "FROM node:20-alpine AS frontend-builder\n"
        'LABEL org.opencontainers.image.version="2.1.0"\n'
        "FROM python:3.11-slim AS production\n"
        'LABEL org.opencontainers.image.version="${OPENACE_VERSION}"\n'
    )
    errors = check_release_version.dockerfile_errors(dockerfile)
    assert errors == [
        "Dockerfile must set its OCI version from OPENACE_VERSION, not a static value"
    ]


def test_malformed_lockfile_is_reported_not_raised(release_tree: Path) -> None:
    (release_tree / "frontend/package-lock.json").write_text(
        json.dumps({"version": "0.0.0", "packages": []}), encoding="utf-8"
    )

    errors = check_release_version.check(None, False)
    assert any("packages['']" in error for error in errors), errors


def _make_legacy_tree(tmp_path: Path) -> Path:
    """A tree like v2.1.0: no checker, frontend 0.2.0, static image label."""
    _copy_release_files(tmp_path)
    for name in ("frontend/package.json", "frontend/package-lock.json"):
        path = tmp_path / name
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = "0.2.0"
        path.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "Dockerfile").write_text('FROM scratch\nLABEL version="1.0.0"\n', encoding="utf-8")
    return tmp_path


def test_legacy_tag_tree_can_still_be_republished(tmp_path: Path) -> None:
    root = _make_legacy_tree(tmp_path)
    version = check_release_version.project_version()

    strict = _run_checker("--root", str(root), "--tag", f"v{version}")
    assert strict.returncode == 1

    legacy = _run_checker("--root", str(root), "--tag", f"v{version}", "--allow-legacy")
    assert legacy.returncode == 0, legacy.stderr
    assert "warning" in legacy.stderr
    assert "frontend/package.json" in legacy.stderr


def test_legacy_mode_still_rejects_a_mismatched_tag(tmp_path: Path) -> None:
    root = _make_legacy_tree(tmp_path)

    result = _run_checker("--root", str(root), "--tag", "v0.0.1", "--allow-legacy")
    assert result.returncode == 1
    assert "release tag 'v0.0.1'" in result.stderr


def test_allow_legacy_does_not_relax_a_tree_with_the_checker(tmp_path: Path) -> None:
    root = _make_legacy_tree(tmp_path)
    (root / "scripts").mkdir()
    shutil.copyfile(CHECKER, root / "scripts/check_release_version.py")
    version = check_release_version.project_version()

    result = _run_checker("--root", str(root), "--tag", f"v{version}", "--allow-legacy")
    assert result.returncode == 1
    assert "Version check failed" in result.stderr


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def release_repo(tmp_path: Path) -> Path:
    """A clean git checkout with an empty origin, laid out like the repo."""
    repo = tmp_path / "repo"
    _copy_release_files(repo)
    (repo / "scripts").mkdir()
    for name in ("release.sh", "check_release_version.py"):
        shutil.copy2(REPO_ROOT / "scripts" / name, repo / "scripts" / name)
    subprocess.run(["git", "init", "--bare", "-q", str(tmp_path / "origin.git")], check=True)
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    return repo


def _tool_path(tmp_path: Path, *, npm: str | None) -> str:
    """PATH with only the tools release.sh needs, and a chosen npm."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("git", "sed", "grep", "awk", "date", "dirname", "mv", "rm", "cat"):
        found = shutil.which(tool)
        assert found is not None, tool
        (bin_dir / tool).symlink_to(found)
    (bin_dir / "python3").symlink_to(sys.executable)
    if npm is not None:
        npm_path = bin_dir / "npm"
        npm_path.write_text(npm, encoding="utf-8")
        npm_path.chmod(0o755)
    return str(bin_dir)


def _run_release(repo: Path, path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(repo / "scripts/release.sh"), "--version", "9.9.9"],
        cwd=repo,
        env={**os.environ, "PATH": path},
        capture_output=True,
        text=True,
        check=False,
    )


def _status(repo: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def test_release_script_refuses_to_start_without_npm(release_repo: Path, tmp_path: Path) -> None:
    result = _run_release(release_repo, _tool_path(tmp_path, npm=None))

    assert result.returncode != 0
    assert "npm is required" in result.stdout
    assert _status(release_repo) == ""


def test_release_script_restores_files_when_a_step_fails(
    release_repo: Path, tmp_path: Path
) -> None:
    result = _run_release(release_repo, _tool_path(tmp_path, npm="#!/bin/sh\nexit 1\n"))

    assert result.returncode != 0
    assert "restoring" in result.stdout
    assert _status(release_repo) == ""


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm not installed")
def test_release_script_prepares_consistent_files(release_repo: Path) -> None:
    result = _run_release(release_repo, os.environ["PATH"])

    assert result.returncode == 0, result.stdout + result.stderr
    check = _run_checker("--root", str(release_repo), "--tag", "v9.9.9")
    assert check.returncode == 0, check.stderr
    assert "## [v9.9.9] - " in (release_repo / "CHANGELOG.md").read_text(encoding="utf-8")
