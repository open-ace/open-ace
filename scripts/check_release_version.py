#!/usr/bin/env python3
"""Check that every product version matches the Python package version."""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def project_version() -> str:
    content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\][ \t]*\n(.*?)(?=^\[|\Z)", content)
    if project is None:
        raise ValueError("pyproject.toml has no [project] section")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project.group(1))
    if match is None:
        raise ValueError("pyproject.toml has no static project version")
    return match.group(1)


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

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    if re.search(r"(?m)^LABEL\s+version\s*=", dockerfile):
        errors.append("Dockerfile still has a legacy static LABEL version")
    if 'org.opencontainers.image.version="${OPENACE_VERSION}"' not in dockerfile:
        errors.append("Dockerfile must set its OCI version from OPENACE_VERSION")

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Expected release tag, e.g. v2.1.0")
    parser.add_argument("--require-changelog", action="store_true")
    args = parser.parse_args()
    try:
        errors = check(args.tag, args.require_changelog)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    if errors:
        for error in errors:
            print(f"Version check failed: {error}", file=sys.stderr)
        return 1
    print(f"Version check passed: v{project_version()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
