#!/usr/bin/env python3
"""Run Open ACE CI suites identically from a workstation or GitHub Actions."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUITE_FILE = PROJECT_ROOT / "ci" / "suites.json"
BASELINE_FILE = PROJECT_ROOT / ".test-baseline.json"
DATABASE_ENVIRONMENT = {
    "ACE_DATABASE_NAME",
    "DATABASE_URL",
    "DB_HOST",
    "DB_NAME",
    "DB_PASSWORD",
    "DB_PORT",
    "DB_USER",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_USER",
}

DOC_PATTERNS = (
    "*.md",
    "docs/**",
    "website/**",
    "CODE_OF_CONDUCT.md",
    "LICENSE",
)
FRONTEND_PATTERNS = ("frontend/**", "static/js/**")
POSTGRES_PATTERNS = (
    "app/models/**",
    "app/repositories/**",
    "migrations/**",
    "schema/**",
    "tests/integration/*_pg.py",
)
E2E_PATTERNS = (
    "app/auth/**",
    "app/routes/**",
    "app/remote_ws_handler.py",
    "frontend/**",
    "tests/e2e/**",
)
PACKAGE_PATTERNS = (
    "Dockerfile",
    "docker-compose*.yml",
    "pyproject.toml",
    "requirements*.lock",
    "requirements*.txt",
    "scripts/install-central/**",
)
DEPENDENCY_PATTERNS = (
    ".nvmrc",
    ".python-version",
    "frontend/package-lock.json",
    "frontend/package.json",
    "pyproject.toml",
    "requirements*.lock",
    "requirements*.txt",
    "uv.lock",
)
POLICY_PATTERNS = (
    ".github/workflows/**",
    ".pre-commit-config.yaml",
    ".test-baseline.json",
    "ci/**",
    "pytest.ini",
    "scripts/ci.py",
    "tests/conftest.py",
)
TEST_PATTERNS = ("tests/**", "pytest.ini", ".test-baseline.json")


class CIError(RuntimeError):
    """A deterministic CI configuration or execution failure."""


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_config() -> dict[str, Any]:
    config = load_json(SUITE_FILE)
    if (
        config.get("version") != 1
        or not isinstance(config.get("suites"), dict)
        or not isinstance(config.get("pr_suites"), list)
    ):
        raise CIError(f"Unsupported or invalid suite manifest: {SUITE_FILE}")
    return config


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def select_pr_suites(changed_files: list[str]) -> list[str]:
    """Select coarse, fail-safe PR lanes from repository-relative paths."""
    clean = sorted({path.strip().lstrip("./") for path in changed_files if path.strip()})
    if not clean:
        return ["default-collection", "issue-collection", "legacy-pr", "python-core"]

    policy_change = any(matches(path, POLICY_PATTERNS) for path in clean)
    docs_only = all(matches(path, DOC_PATTERNS) for path in clean)
    if docs_only and not policy_change:
        return []

    # Collection is cheap (~2s) and catches legacy imports broken by product
    # changes, so both baselines accompany every non-documentation PR.
    selected = {"default-collection", "issue-collection", "legacy-pr", "python-core"}
    if policy_change:
        selected.update(load_config()["pr_suites"])
        return sorted(selected)

    if any(matches(path, TEST_PATTERNS) for path in clean):
        selected.add("issue-collection")
    if any(matches(path, FRONTEND_PATTERNS) for path in clean):
        selected.add("frontend")
    if any(matches(path, POSTGRES_PATTERNS) for path in clean):
        selected.add("postgres")
    if any(matches(path, E2E_PATTERNS) for path in clean):
        selected.add("critical-e2e")
    if any(matches(path, PACKAGE_PATTERNS) for path in clean):
        selected.add("package")
    if any(matches(path, DEPENDENCY_PATTERNS) for path in clean):
        selected.add("compatibility-smoke")
        selected.add("dependency-audit")
    return sorted(selected)


def changed_files(base: str) -> list[str]:
    if not base or set(base) == {"0"}:
        # A new branch or rewritten push may not have a usable before SHA.
        # Treat uncertainty as a CI-policy change so every PR-eligible suite runs.
        return ["ci/suites.json"]
    committed = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if committed.returncode != 0:
        print(
            f"WARNING: unable to diff from {base}; selecting every PR suite: "
            f"{committed.stderr.strip()}",
            file=sys.stderr,
        )
        return ["ci/suites.json"]

    # A local pre-commit run must also see staged, unstaged, and untracked files.
    # GitHub checkouts are clean, so these commands add nothing there.
    local_commands = (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    changed = set(committed.stdout.splitlines())
    for command in local_commands:
        local = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if local.returncode != 0:
            print(
                "WARNING: unable to inspect the local worktree; selecting every PR suite: "
                f"{local.stderr.strip()}",
                file=sys.stderr,
            )
            return ["ci/suites.json"]
        changed.update(local.stdout.splitlines())
    return sorted(changed)


def isolated_environment(home: str, preserve_database_environment: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "CI": "true",
            "HOME": home,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "TZ": "UTC",
        }
    )
    if not preserve_database_environment:
        for name in DATABASE_ENVIRONMENT:
            env.pop(name, None)
    return env


def expand_command(command: list[str]) -> list[str]:
    return [part.replace("{python}", sys.executable) for part in command]


def run_command(
    command: list[str], *, cwd: Path, env: dict[str, str], timeout_seconds: int
) -> None:
    expanded = expand_command(command)
    print(f"+ {' '.join(expanded)}", flush=True)
    started = time.monotonic()
    try:
        result = subprocess.run(expanded, cwd=cwd, env=env, check=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise CIError(f"Command exceeded {timeout_seconds}s: {' '.join(expanded)}") from exc
    elapsed = time.monotonic() - started
    print(f"  completed in {elapsed:.1f}s", flush=True)
    if result.returncode:
        raise CIError(f"Command failed with exit code {result.returncode}: {' '.join(expanded)}")


def collection_count(output: str) -> int:
    matches = re.findall(r"(\d+) tests? collected", output)
    return int(matches[-1]) if matches else 0


def candidate_test_file_count(target: str) -> int:
    """Count pytest-named files covered by a collection target."""
    root = PROJECT_ROOT / target
    files = {
        path for pattern in ("test_*.py", "e2e_*.py", "*_test.py") for path in root.rglob(pattern)
    }
    if target == "tests":
        excluded_roots = {PROJECT_ROOT / "tests" / name for name in ("issues", "e2e")}
        files = {
            path
            for path in files
            if not any(path.is_relative_to(excluded) for excluded in excluded_roots)
            and "scripts" not in path.relative_to(PROJECT_ROOT / "tests").parts[:-1]
            and ".qwen" not in path.relative_to(PROJECT_ROOT / "tests").parts[:-1]
        }
    return len(files)


def run_collection_suite(name: str, suite: dict[str, Any], env: dict[str, str]) -> None:
    target = suite["collection_target"]
    command = [sys.executable, "-m", "pytest", target, "--collect-only", "-q"]
    print(f"+ {' '.join(command)}", flush=True)
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=suite["timeout_seconds"],
    )
    output = result.stdout + result.stderr
    print("\n".join(output.splitlines()[-40:]))
    if result.returncode:
        raise CIError(f"{name} failed pytest collection with exit code {result.returncode}")

    count = collection_count(output)
    layer = load_json(BASELINE_FILE)["layers"][suite["baseline_layer"]]
    min_tests = layer["min_tests"]
    file_count = candidate_test_file_count(target)
    min_files = layer.get("min_files", 0)
    if count < min_tests:
        raise CIError(f"{name} collected {count} tests, below baseline {min_tests}")
    if file_count < min_files:
        raise CIError(f"{name} found {file_count} test files, below baseline {min_files}")
    print(
        f"{name}: collected {count} tests from {file_count} files "
        f"(baselines {min_tests} tests / {min_files} files)",
        flush=True,
    )


def run_suite(name: str, config: dict[str, Any]) -> None:
    suites = config["suites"]
    if name not in suites:
        raise CIError(f"Unknown suite {name!r}; choose from: {', '.join(sorted(suites))}")
    suite = suites[name]
    print(f"\n=== {name}: {suite['description']} ===", flush=True)
    # Keep the isolated HOME below the checkout. On macOS the system temp
    # directory resolves through /private/var, which Open ACE deliberately
    # rejects as a workspace root; GitHub's Linux runner would not expose that
    # failure. A checkout-local temporary HOME is safe on both platforms and
    # is removed after the suite.
    with tempfile.TemporaryDirectory(prefix=f".openace-ci-{name}-", dir=PROJECT_ROOT) as home:
        env = isolated_environment(home, suite.get("preserve_database_environment", False))
        if "collection_target" in suite:
            run_collection_suite(name, suite, env)
            return
        suite_cwd = PROJECT_ROOT / suite.get("working_directory", ".")
        suite_started = time.monotonic()
        for definition in suite.get("commands", []):
            if isinstance(definition, dict):
                command = definition["command"]
                cwd = PROJECT_ROOT / definition.get("working_directory", ".")
            else:
                command = definition
                cwd = suite_cwd
            elapsed = time.monotonic() - suite_started
            remaining = max(1, int(suite["timeout_seconds"] - elapsed))
            run_command(command, cwd=cwd, env=env, timeout_seconds=remaining)


def write_github_outputs(selected: list[str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise CIError("--github-output requires the GITHUB_OUTPUT environment variable")
    selected_set = set(selected)
    config = load_config()
    with open(output_path, "a", encoding="utf-8") as handle:
        for name in sorted(config["suites"]):
            key = name.replace("-", "_")
            handle.write(f"{key}={'true' if name in selected_set else 'false'}\n")
        handle.write(f"selected={','.join(selected)}\n")


def check_toolchain(config: dict[str, Any], strict: bool = False) -> bool:
    """Report whether local runtimes match the canonical PR toolchain."""
    expected_python = config["toolchain"]["production_python"]
    actual_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    try:
        node_result = subprocess.run(
            ["node", "--version"], check=False, capture_output=True, text=True, timeout=5
        )
        actual_node = (
            node_result.stdout.strip().lstrip("v") if node_result.returncode == 0 else "missing"
        )
    except (OSError, subprocess.TimeoutExpired):
        actual_node = "missing"
    expected_node = str(config["toolchain"]["node"])
    node_matches = actual_node.split(".", 1)[0] == expected_node
    python_matches = actual_python == expected_python

    print(f"Python: {actual_python} (PR runtime {expected_python})")
    print(f"Node:   {actual_node} (PR runtime {expected_node}.x)")
    print(f"Runner: local (GitHub {config['toolchain']['runner']})")
    matches = python_matches and node_matches
    if not matches:
        message = "Local toolchain differs from the canonical PR toolchain"
        if strict:
            raise CIError(message)
        print(f"WARNING: {message}", file=sys.stderr)
    return matches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list", help="List configured suites")
    doctor_parser = subparsers.add_parser("doctor", help="Compare local and PR toolchains")
    doctor_parser.add_argument("--strict", action="store_true")

    run_parser = subparsers.add_parser("run", help="Run one or more suites")
    run_parser.add_argument("suites", nargs="+")

    detect_parser = subparsers.add_parser("detect", help="Select PR suites from changed files")
    detect_parser.add_argument("--base", default="origin/main")
    detect_parser.add_argument("--github-output", action="store_true")
    detect_parser.add_argument("files", nargs="*")

    pr_parser = subparsers.add_parser("pr", help="Run the locally selected PR suites")
    pr_parser.add_argument("--base", default="origin/main")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config()
        if args.action == "list":
            for name, suite in sorted(config["suites"].items()):
                print(f"{name:24} {suite['description']}")
            return 0
        if args.action == "doctor":
            check_toolchain(config, strict=args.strict)
            return 0
        if args.action == "run":
            for name in args.suites:
                run_suite(name, config)
            return 0

        files = args.files if args.action == "detect" and args.files else changed_files(args.base)
        selected = select_pr_suites(files)
        print(f"Changed files: {len(files)}")
        print(f"Selected suites: {', '.join(selected) if selected else 'policy-only'}")
        if args.action == "detect":
            if args.github_output:
                write_github_outputs(selected)
            return 0
        for name in selected:
            run_suite(name, config)
        return 0
    except (CIError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"CI ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
