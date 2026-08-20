#!/usr/bin/env python3
"""Safely preserve one deployed frontend release across package upgrades.

The frontend Node post-build step and this installer helper share the state
schema in ``.openace-release-assets.json``.  All paths in that file are treated
as untrusted input and must resolve to regular files below the dist directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
STATE_FILENAME = ".openace-release-assets.json"
SNAPSHOT_FILENAME = "snapshot-state.json"
VITE_MANIFEST = Path(".vite/manifest.json")
MAX_ASSETS = 10_000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
HASHED_ASSET_RE = re.compile(r"(?:^|/)[^/]+\.[A-Za-z0-9_-]{8}\.[^/]+$")


class RetentionError(RuntimeError):
    """Raised when release metadata or an asset violates the safety contract."""


def _normalize_asset(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise RetentionError("asset path must be a safe non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or ".." in path.parts:
        raise RetentionError(f"unsafe asset path: {value}")
    return value


def _asset_path(dist: Path, asset: str) -> Path:
    dist_real = dist.resolve(strict=True)
    candidate = dist_real.joinpath(*PurePosixPath(_normalize_asset(asset)).parts)
    try:
        candidate.parent.resolve(strict=True).relative_to(dist_real)
    except (FileNotFoundError, ValueError) as exc:
        raise RetentionError(f"asset escapes dist directory: {asset}") from exc
    return candidate


def _safe_destination(dist: Path, asset: str) -> Path:
    """Create destination parents without traversing symlinks."""
    dist_real = dist.resolve(strict=True)
    parts = PurePosixPath(_normalize_asset(asset)).parts
    parent = dist_real
    for part in parts[:-1]:
        candidate = parent / part
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            candidate.mkdir(mode=0o755)
            info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RetentionError(f"unsafe destination parent: {asset}")
        try:
            candidate.resolve(strict=True).relative_to(dist_real)
        except ValueError as exc:
            raise RetentionError(f"destination escapes dist directory: {asset}") from exc
        parent = candidate
    destination = parent / parts[-1]
    if destination.exists() or destination.is_symlink():
        info = destination.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise RetentionError(f"unsafe destination asset: {asset}")
    return destination


def _validate_regular_assets(dist: Path, assets: list[str]) -> list[str]:
    if len(assets) > MAX_ASSETS:
        raise RetentionError("asset count exceeds limit")
    if assets != sorted(set(assets)):
        raise RetentionError("assets must be sorted and unique")
    total_bytes = 0
    for asset in assets:
        if not HASHED_ASSET_RE.search(_normalize_asset(asset)):
            raise RetentionError(f"not a hashed release asset: {asset}")
        path = _asset_path(dist, asset)
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise RetentionError(f"missing asset: {asset}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise RetentionError(f"asset is not a regular file: {asset}")
        total_bytes += info.st_size
        if total_bytes > MAX_TOTAL_BYTES:
            raise RetentionError("asset bytes exceed limit")
    return assets


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RetentionError(f"invalid {label} JSON") from exc
    if not isinstance(value, dict):
        raise RetentionError(f"invalid {label} schema")
    return value, raw


def derive_current_release(dist: Path) -> dict[str, Any]:
    manifest, raw = _load_json(dist / VITE_MANIFEST, "Vite manifest")
    keys = set(manifest)
    assets: set[str] = set()
    for key, entry in manifest.items():
        if not isinstance(entry, dict):
            raise RetentionError(f"invalid manifest entry: {key}")
        for dependency_field in ("imports", "dynamicImports"):
            dependencies = entry.get(dependency_field, [])
            if not isinstance(dependencies, list):
                raise RetentionError(f"invalid {dependency_field} for {key}")
            for dependency in dependencies:
                if not isinstance(dependency, str) or dependency not in keys:
                    raise RetentionError(f"dangling {dependency_field} key for {key}: {dependency}")
        file_name = entry.get("file")
        if not isinstance(file_name, str):
            raise RetentionError(f"missing file for manifest entry: {key}")
        assets.add(_normalize_asset(file_name))
        for asset_field in ("css", "assets"):
            values = entry.get(asset_field, [])
            if not isinstance(values, list):
                raise RetentionError(f"invalid {asset_field} for {key}")
            for value in values:
                assets.add(_normalize_asset(value))
    current_assets = _validate_regular_assets(dist, sorted(assets))
    return {
        "build_id": hashlib.sha256(raw).hexdigest(),
        "assets": current_assets,
    }


def _validate_generation(dist: Path, value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RetentionError(f"invalid {label} generation")
    build_id = value.get("build_id")
    assets = value.get("assets")
    if not isinstance(build_id, str) or re.fullmatch(r"[a-f0-9]{64}", build_id) is None:
        raise RetentionError(f"invalid {label} build id")
    if not isinstance(assets, list) or not all(isinstance(item, str) for item in assets):
        raise RetentionError(f"invalid {label} assets")
    return {
        "build_id": build_id,
        "assets": _validate_regular_assets(dist, assets),
    }


def load_state(dist: Path) -> dict[str, Any] | None:
    state_path = dist / STATE_FILENAME
    if not state_path.exists():
        return None
    state, _ = _load_json(state_path, "release state")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise RetentionError("unsupported release state schema")
    previous = state.get("previous")
    return {
        "schema_version": SCHEMA_VERSION,
        "current": _validate_generation(dist, state.get("current"), "current"),
        "previous": None if previous is None else _validate_generation(dist, previous, "previous"),
    }


def validate_source(dist: Path) -> dict[str, Any]:
    state = load_state(dist)
    if state is None:
        raise RetentionError(f"missing source release state: {STATE_FILENAME}")
    manifest_current = derive_current_release(dist)
    if state["current"] != manifest_current:
        raise RetentionError("source state current does not match Vite manifest")
    return state


def _legacy_assets(dist: Path) -> list[str]:
    assets: list[str] = []
    for root, directories, files in os.walk(dist, followlinks=False):
        root_path = Path(root)
        directories[:] = [name for name in directories if not (root_path / name).is_symlink()]
        for name in files:
            path = root_path / name
            if path.is_symlink() or not path.is_file():
                continue
            asset = path.relative_to(dist).as_posix()
            if HASHED_ASSET_RE.search(asset):
                assets.append(asset)
    return _validate_regular_assets(dist, sorted(set(assets)))


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def snapshot_deployment(dist: Path, output: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise RetentionError("snapshot output must be empty")
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    state = load_state(dist)
    if state is None:
        assets = _legacy_assets(dist)
        current = {
            "build_id": hashlib.sha256("\n".join(assets).encode()).hexdigest(),
            "assets": assets,
        }
    else:
        current = state["current"]

    snapshot_assets = output / "assets"
    for asset in current["assets"]:
        source = _asset_path(dist, asset)
        destination = snapshot_assets.joinpath(*PurePosixPath(asset).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
    snapshot = {"schema_version": SCHEMA_VERSION, "current": current, "previous": None}
    _atomic_write(output / SNAPSHOT_FILENAME, snapshot)
    return snapshot


def _load_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    state, _ = _load_json(snapshot_dir / SNAPSHOT_FILENAME, "snapshot state")
    if state.get("schema_version") != SCHEMA_VERSION or state.get("previous") is not None:
        raise RetentionError("invalid snapshot schema")
    return {
        "schema_version": SCHEMA_VERSION,
        "current": _validate_generation(snapshot_dir / "assets", state.get("current"), "snapshot"),
        "previous": None,
    }


def reconcile_install(target_dist: Path, snapshot_dir: Path | None = None) -> dict[str, Any]:
    package_state = validate_source(target_dist)
    current_assets = set(package_state["current"]["assets"])

    # Discard only the package/build-machine previous generation. Unknown and
    # public files are deliberately left alone.
    package_previous = package_state["previous"]
    for asset in [] if package_previous is None else package_previous["assets"]:
        if asset not in current_assets:
            _asset_path(target_dist, asset).unlink(missing_ok=True)

    snapshot = None if snapshot_dir is None else _load_snapshot(snapshot_dir)
    previous_assets = (
        []
        if snapshot is None
        else [asset for asset in snapshot["current"]["assets"] if asset not in current_assets]
    )
    for asset in previous_assets:
        assert snapshot is not None and snapshot_dir is not None
        source = _asset_path(snapshot_dir / "assets", asset)
        destination = _safe_destination(target_dist, asset)
        if destination.exists():
            if (
                hashlib.sha256(destination.read_bytes()).digest()
                != hashlib.sha256(source.read_bytes()).digest()
            ):
                raise RetentionError(f"conflicting content-hashed asset: {asset}")
        else:
            shutil.copy2(source, destination, follow_symlinks=False)

    previous = (
        None
        if snapshot is None
        else {
            "build_id": snapshot["current"]["build_id"],
            "assets": previous_assets,
        }
    )
    deployed_state = {
        "schema_version": SCHEMA_VERSION,
        "current": package_state["current"],
        "previous": previous,
    }
    _atomic_write(target_dist / STATE_FILENAME, deployed_state)
    return deployed_state


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate-source")
    validate_parser.add_argument("--dist", required=True, type=Path)
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--dist", required=True, type=Path)
    snapshot_parser.add_argument("--output", required=True, type=Path)
    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("--target-dist", required=True, type=Path)
    reconcile_parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    if args.command == "validate-source":
        validate_source(args.dist)
    elif args.command == "snapshot":
        snapshot_deployment(args.dist, args.output)
    else:
        reconcile_install(args.target_dist, args.snapshot)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RetentionError as exc:
        raise SystemExit(f"frontend asset retention: {exc}") from exc
