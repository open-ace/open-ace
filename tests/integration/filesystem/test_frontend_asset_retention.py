"""Filesystem integration coverage for frontend release asset retention."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER_PATH = REPO_ROOT / "scripts" / "frontend_asset_retention.py"
SPEC = importlib.util.spec_from_file_location("frontend_asset_retention", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
retention = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retention)

pytestmark = [pytest.mark.issue(2875), pytest.mark.regression]


def _write_release(
    dist: Path,
    name: str,
    digest: str,
    *,
    previous: dict[str, object] | None = None,
) -> dict[str, object]:
    dist.mkdir(parents=True, exist_ok=True)
    (dist / ".vite").mkdir(exist_ok=True)
    asset = f"{name}.{digest}.js"
    (dist / asset).write_text(asset, encoding="utf-8")
    manifest = {f"src/{name}.tsx": {"file": asset, "isEntry": True}}
    raw = json.dumps(manifest, separators=(",", ":")).encode()
    (dist / ".vite" / "manifest.json").write_bytes(raw)
    current = {"build_id": hashlib.sha256(raw).hexdigest(), "assets": [asset]}
    state = {"schema_version": 1, "current": current, "previous": previous}
    (dist / retention.STATE_FILENAME).write_text(json.dumps(state), encoding="utf-8")
    return state


def test_shared_state_fixture_has_identical_python_contract(tmp_path: Path) -> None:
    fixture = json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "frontend_asset_state_v1.json").read_text()
    )
    for generation in (fixture["current"], fixture["previous"]):
        for asset in generation["assets"]:
            (tmp_path / asset).write_text(asset, encoding="utf-8")
    (tmp_path / retention.STATE_FILENAME).write_text(json.dumps(fixture))

    assert retention.load_state(tmp_path) == fixture


def test_reconcile_keeps_deployment_previous_not_package_previous(tmp_path: Path) -> None:
    deployed = tmp_path / "deployed"
    deployed_state = _write_release(deployed, "AutonomousDev", "dddddddd")
    snapshot = tmp_path / "snapshot"
    retention.snapshot_deployment(deployed, snapshot)

    package = tmp_path / "package"
    package_previous_asset = "AutonomousDev.pppppppp.js"
    package_previous = {
        "build_id": "f" * 64,
        "assets": [package_previous_asset],
    }
    package_state = _write_release(package, "AutonomousDev", "bbbbbbbb", previous=package_previous)
    (package / package_previous_asset).write_text("package-only", encoding="utf-8")

    result = retention.reconcile_install(package, snapshot)

    deployed_asset = deployed_state["current"]["assets"][0]
    package_asset = package_state["current"]["assets"][0]
    assert (package / deployed_asset).is_file()
    assert (package / package_asset).is_file()
    assert not (package / package_previous_asset).exists()
    assert result["current"] == package_state["current"]
    assert result["previous"] == deployed_state["current"]


def test_reconcile_without_deployment_discards_package_previous(tmp_path: Path) -> None:
    package_previous_asset = "AutonomousDev.pppppppp.js"
    package_previous = {"build_id": "f" * 64, "assets": [package_previous_asset]}
    package_state = _write_release(tmp_path, "AutonomousDev", "bbbbbbbb", previous=package_previous)
    (tmp_path / package_previous_asset).write_text("package-only", encoding="utf-8")

    result = retention.reconcile_install(tmp_path)

    assert result["current"] == package_state["current"]
    assert result["previous"] is None
    assert not (tmp_path / package_previous_asset).exists()


def test_reconcile_rejects_symlinked_destination_parent(tmp_path: Path) -> None:
    deployed = tmp_path / "deployed"
    nested = deployed / "nested"
    nested.mkdir(parents=True)
    old_asset = "nested/AutonomousDev.dddddddd.js"
    (deployed / old_asset).write_text("deployed", encoding="utf-8")
    deployed_state = {
        "schema_version": 1,
        "current": {"build_id": "d" * 64, "assets": [old_asset]},
        "previous": None,
    }
    (deployed / retention.STATE_FILENAME).write_text(json.dumps(deployed_state))
    snapshot = tmp_path / "snapshot"
    retention.snapshot_deployment(deployed, snapshot)

    package = tmp_path / "package"
    _write_release(package, "AutonomousDev", "bbbbbbbb")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (package / "nested").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(retention.RetentionError, match="unsafe destination parent"):
        retention.reconcile_install(package, snapshot)
    assert not (outside / "AutonomousDev.dddddddd.js").exists()


def test_legacy_snapshot_ignores_unknown_files_and_symlinks(tmp_path: Path) -> None:
    dist = tmp_path / "legacy"
    dist.mkdir()
    hashed = dist / "AutonomousDev.aaaaaaaa.js"
    hashed.write_text("old release", encoding="utf-8")
    (dist / "runtime-config.js").write_text("public", encoding="utf-8")
    outside = tmp_path / "outside.bbbbbbbb.js"
    outside.write_text("outside", encoding="utf-8")
    link = dist / "linked.cccccccc.js"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    snapshot = tmp_path / "snapshot"
    state = retention.snapshot_deployment(dist, snapshot)

    assert state["current"]["assets"] == [hashed.name]
    assert not (snapshot / "assets" / "runtime-config.js").exists()
    assert not (snapshot / "assets" / link.name).exists()
    assert outside.read_text(encoding="utf-8") == "outside"


def test_source_state_must_exactly_match_manifest(tmp_path: Path) -> None:
    state = _write_release(tmp_path, "index", "12345678")
    state["current"]["assets"].append("extra.87654321.js")
    state["current"]["assets"].sort()
    (tmp_path / "extra.87654321.js").write_text("extra", encoding="utf-8")
    (tmp_path / retention.STATE_FILENAME).write_text(json.dumps(state))

    with pytest.raises(retention.RetentionError, match="does not match Vite manifest"):
        retention.validate_source(tmp_path)


def test_installer_validates_and_snapshots_before_destructive_cleanup() -> None:
    installer = (
        REPO_ROOT / "scripts" / "install-central" / "package-method" / "install.sh"
    ).read_text(encoding="utf-8")
    validate = installer.index("validate-source")
    snapshot = installer.index('frontend_retention_helper" snapshot')
    destructive_cleanup = installer.index('find "$target_path" -mindepth 1', snapshot)
    destructive_cleanup = installer.index("-exec rm -rf", destructive_cleanup)
    package_copy = installer.index('cp -r "$SOURCE_DIR"/* "$target_path/"', destructive_cleanup)
    reconcile = installer.index('frontend_asset_retention.py" reconcile', package_copy)

    assert validate < destructive_cleanup
    assert snapshot < destructive_cleanup
    assert destructive_cleanup < package_copy < reconcile
    assert "source directory lacks static/js/dist" in installer
