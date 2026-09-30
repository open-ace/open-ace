"""Release gates must reject metadata that describes different products."""

import json
import shutil
from pathlib import Path

import pytest

from scripts import check_release_version


@pytest.fixture
def release_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = Path(__file__).resolve().parents[2]
    for name in (
        "pyproject.toml",
        "frontend/package.json",
        "frontend/package-lock.json",
        "Dockerfile",
        "CHANGELOG.md",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    monkeypatch.setattr(check_release_version, "ROOT", tmp_path)
    return tmp_path


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
