#!/usr/bin/env python3
"""Check that every product version matches the Python package version."""

# Run by bare `python3` from scripts/release.sh and CI; keep annotations lazy
# so the stock macOS python3 (3.9) can still import this file.
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
OCI_VERSION_VALUES = {"${OPENACE_VERSION}", "$OPENACE_VERSION"}
# Tags cut before the version-consistency contract (<= v2.1.0) do not carry
# this script; release workflows may still republish them.
CONTRACT_MARKER = "scripts/check_release_version.py"


def project_version() -> str:
    content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\][ \t]*\n(.*?)(?=^\[|\Z)", content)
    if project is None:
        raise ValueError("pyproject.toml has no [project] section")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project.group(1))
    if match is None:
        raise ValueError("pyproject.toml has no static project version")
    return match.group(1)


def is_legacy_tree() -> bool:
    return not (ROOT / CONTRACT_MARKER).is_file()


def dockerfile_labels(dockerfile: str) -> list[dict[str, str]]:
    """Return the key/value pairs of every LABEL instruction."""
    logical: list[str] = []
    current = ""
    for raw in dockerfile.splitlines():
        stripped = raw.strip()
        # Docker drops comment and blank lines, including inside a continuation.
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1] + " "
            continue
        logical.append(current + stripped)
        current = ""
    if current:
        logical.append(current)

    labels = []
    for line in logical:
        keyword, _, rest = line.replace("\t", " ").partition(" ")
        if keyword.upper() != "LABEL":
            continue
        pairs: dict[str, str] = {}
        for token in shlex.split(rest):
            key, _, value = token.partition("=")
            pairs[key] = value
        labels.append(pairs)
    return labels


def dockerfile_errors(dockerfile: str) -> list[str]:
    errors = []
    labels = dockerfile_labels(dockerfile)
    if any("version" in pairs for pairs in labels):
        errors.append("Dockerfile still has a legacy static LABEL version")
    oci_versions = [
        pairs["org.opencontainers.image.version"]
        for pairs in labels
        if "org.opencontainers.image.version" in pairs
    ]
    if not oci_versions or any(value not in OCI_VERSION_VALUES for value in oci_versions):
        errors.append("Dockerfile must set its OCI version from OPENACE_VERSION")
    return errors


def check(tag: str | None, require_changelog: bool) -> list[str]:
    errors = []
    version = project_version()
    if SEMVER.fullmatch(version) is None:
        errors.append(f"Python package version is not X.Y.Z: {version}")

    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    for name, actual in (
        ("frontend/package.json", package.get("version")),
        ("frontend/package-lock.json", lock.get("version")),
        (
            "frontend/package-lock.json packages['']",
            lock.get("packages", {}).get("", {}).get("version"),
        ),
    ):
        if actual != version:
            errors.append(f"{name}: {actual!r} does not match pyproject.toml: {version!r}")

    errors.extend(dockerfile_errors((ROOT / "Dockerfile").read_text(encoding="utf-8")))

    if tag is not None:
        if tag != f"v{version}":
            errors.append(f"release tag {tag!r} does not match v{version}")
        require_changelog = True
    if require_changelog:
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        if (
            re.search(rf"(?m)^## \[v{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog)
            is None
        ):
            errors.append(f"CHANGELOG.md is missing a dated [v{version}] section")
    return errors


def check_legacy(tag: str | None) -> tuple[list[str], list[str]]:
    """Checks for a tree cut before the contract: only the tag is binding."""
    version = project_version()
    errors = []
    if tag is not None and tag != f"v{version}":
        errors.append(f"release tag {tag!r} does not match v{version}")
    warnings = [error for error in check(None, False) if "release tag" not in error]
    return errors, warnings


def main() -> int:
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Expected release tag, e.g. v2.1.0")
    parser.add_argument("--require-changelog", action="store_true")
    parser.add_argument(
        "--root",
        type=Path,
        help="Repository tree to check (default: the tree containing this script)",
    )
    parser.add_argument(
        "--allow-legacy",
        action="store_true",
        help=(
            "If the tree predates this checker (a release tag cut before it existed), "
            "only require the tag to match pyproject.toml and report the rest as warnings"
        ),
    )
    args = parser.parse_args()
    if args.root is not None:
        ROOT = args.root.resolve()
    warnings: list[str] = []
    try:
        if args.allow_legacy and is_legacy_tree():
            errors, warnings = check_legacy(args.tag)
        else:
            errors = check(args.tag, args.require_changelog)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    for warning in warnings:
        print(f"Version check warning (legacy release tree): {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"Version check failed: {error}", file=sys.stderr)
        return 1
    print(f"Version check passed: v{project_version()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
