#!/usr/bin/env python3
"""
Run the server-dependent Open ACE test suites from CI or a local checkout.

The default CI suite intentionally excludes tests/e2e and tests/issues because
they need a live web server and can be slow. This runner is the shared entry
point for scheduled, release, PR critical, and manual extended-test runs.

Issue #2189: Runner improvements
- Explicit file discovery (bypass norecursedirs)
- Collection verification and manifest
- Shard consistency validation
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://localhost:19888"
SERVER_CATEGORIES = {
    "all",
    "critical",
    "e2e",
    "issues",
    "regression",
    "ui",
    "remote",
    "terminal",
    "manage",
    "work",
    "performance",
    "specific",
}
CATEGORY_TARGETS = {
    "critical": [
        "tests/e2e/regression/test_login.py",
        "tests/e2e/regression/test_navigation.py::test_sidebar_menu_visible",
        "tests/e2e/regression/test_navigation.py::test_menu_navigation",
    ],
    "regression": ["tests/e2e/regression"],
    "ui": ["tests/e2e/ui"],
    "remote": ["tests/e2e/remote"],
    "terminal": ["tests/e2e/terminal"],
    "manage": ["tests/e2e/manage"],
    "work": ["tests/e2e/work"],
    "performance": ["tests/e2e/performance"],
    "e2e": ["tests/e2e"],
    "issues": ["tests/issues"],
    "all": ["tests/e2e", "tests/issues"],
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--category",
        choices=sorted(CATEGORY_TARGETS.keys() | {"specific"}),
        default="critical",
        help="Extended test group to run.",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="Specific pytest target. Required for --category specific.",
    )
    parser.add_argument(
        "--issue",
        dest="issues",
        action="append",
        default=[],
        help="Issue number under tests/issues to run. Can be repeated.",
    )
    parser.add_argument(
        "--issue-numbers",
        default="",
        help="Comma-separated issue numbers under tests/issues.",
    )
    parser.add_argument("--split-total", type=int, default=1, help="Total number of shards.")
    parser.add_argument("--split-group", type=int, default=1, help="1-based shard index to run.")
    parser.add_argument("--parallel", type=int, default=0, help="pytest-xdist worker count.")
    parser.add_argument("--reruns", type=int, default=0, help="Retry failed tests this many times.")
    parser.add_argument("--timeout", type=int, default=0, help="Per-test timeout in seconds.")
    parser.add_argument("--maxfail", type=int, default=0, help="Stop after this many failures.")
    parser.add_argument("--junitxml", default="", help="Write a pytest JUnit XML report.")
    parser.add_argument("--extra-pytest-arg", action="append", default=[], help="Extra pytest arg.")
    parser.add_argument(
        "--server",
        choices=["auto", "reuse", "skip"],
        default="auto",
        help="auto starts Open ACE when the health endpoint is unavailable.",
    )
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--isolated-home",
        action="store_true",
        help="Use a temporary HOME so test data never touches the developer database.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the pytest command without running it.",
    )
    # Issue #2189: 新增参数
    parser.add_argument(
        "--verify-shard",
        action="store_true",
        help="Verify shard consistency (all shards equal to no-shard collection).",
    )
    parser.add_argument(
        "--manifest-dir",
        default="test-results",
        help="Directory to write collection manifest (default: test-results).",
    )
    return parser.parse_args(argv)


def category_needs_server(category: str) -> bool:
    return category in SERVER_CATEGORIES


def parse_issue_numbers(args: argparse.Namespace) -> list[str]:
    numbers: list[str] = []
    numbers.extend(args.issues)
    if args.issue_numbers:
        numbers.extend(part.strip() for part in args.issue_numbers.split(","))
    clean = []
    for number in numbers:
        if not number:
            continue
        if not number.isdigit():
            raise ValueError(f"Invalid issue number: {number!r}")
        clean.append(number)
    return clean


def target_path(target: str) -> str:
    return target.split("::", 1)[0]


def target_exists(target: str) -> bool:
    return (PROJECT_ROOT / target_path(target)).exists()


def select_targets(args: argparse.Namespace) -> list[str]:
    if args.category == "specific":
        if not args.target:
            raise ValueError("--category specific requires at least one --target")
        targets = args.target
    elif args.category == "issues":
        issue_numbers = parse_issue_numbers(args)
        targets = [f"tests/issues/{number}" for number in issue_numbers] or CATEGORY_TARGETS[
            "issues"
        ]
    else:
        targets = CATEGORY_TARGETS[args.category]

    existing = [target for target in targets if target_exists(target)]
    missing = sorted(set(targets) - set(existing))
    if missing:
        print(f"Skipping missing targets: {', '.join(missing)}")
    if not existing:
        raise FileNotFoundError(f"No selected test targets exist: {targets}")
    return existing


def discover_test_files(targets: list[str]) -> list[str]:
    """
    显式发现测试文件，不依赖 pytest pattern

    Issue #2189: 改进
    - 显式搜索 test_*.py 和 e2e_*.py 文件
    - 验证非空（否则抛出异常）
    - 输出收集数量和文件列表到日志
    """
    files: list[Path] = []
    for target in targets:
        path = PROJECT_ROOT / target_path(target)
        if path.is_file():
            files.append(path)
            continue
        files.extend(path.rglob("test_*.py"))
        files.extend(path.rglob("e2e_*.py"))
    unique = sorted({file.relative_to(PROJECT_ROOT).as_posix() for file in files})

    # Issue #2189: 验证非空
    if not unique:
        raise ValueError(
            f"No test files found for targets: {targets}\n"
            f"Searched patterns: test_*.py, e2e_*.py"
        )

    # Issue #2189: 输出收集信息
    print(f"Discovered {len(unique)} test files:")
    for f in unique:
        print(f"  - {f}")

    return unique


def apply_split(targets: list[str], split_total: int, split_group: int) -> list[str]:
    """
    应用分片逻辑

    Issue #2189: 改进
    - 输出分片信息
    - 确保分片一致性
    """
    if split_total < 1:
        raise ValueError("--split-total must be >= 1")
    if split_group < 1 or split_group > split_total:
        raise ValueError("--split-group must be between 1 and --split-total")
    if split_total == 1:
        return targets

    files = discover_test_files(targets)
    selected = [
        file for index, file in enumerate(files) if (index % split_total) == (split_group - 1)
    ]
    if not selected:
        raise ValueError(f"Shard {split_group}/{split_total} selected no test files")

    # Issue #2189: 输出分片信息
    print(f"Selected {len(selected)} files for shard {split_group}/{split_total}")
    return selected


def build_pytest_command(args: argparse.Namespace) -> list[str]:
    """
    构建 pytest 命令，传递显式文件列表

    Issue #2189: 改进
    - 传递显式文件列表（绕过 norecursedirs）
    - 使用绝对路径
    - 不传递目录路径
    """
    targets = select_targets(args)

    # Issue #2189: 使用显式文件列表
    test_files = discover_test_files(targets)
    test_files = apply_split(test_files, args.split_total, args.split_group)

    # 转换为绝对路径
    absolute_files = [str(PROJECT_ROOT / f) for f in test_files]

    # 构建命令
    cmd = [sys.executable, "-m", "pytest", *absolute_files, "-m", "not postgres"]
    if args.parallel > 0:
        cmd.extend(["-n", str(args.parallel)])
    if args.reruns > 0:
        cmd.extend(["--reruns", str(args.reruns), "--reruns-delay", "5"])
    if args.timeout > 0:
        cmd.extend(["--timeout", str(args.timeout)])
    if args.maxfail > 0:
        cmd.append(f"--maxfail={args.maxfail}")
    if args.junitxml:
        cmd.append(f"--junitxml={args.junitxml}")
    cmd.extend(args.extra_pytest_arg)
    return cmd


def write_collection_manifest(
    test_files: list[str],
    category: str,
    split_group: int,
    split_total: int,
    manifest_dir: str,
) -> None:
    """
    Issue #2189: 写入 collection manifest

    用于 CI 验证和调试
    """
    manifest_path = PROJECT_ROOT / manifest_dir
    manifest_path.mkdir(parents=True, exist_ok=True)

    manifest = {
        "category": category,
        "split_group": split_group,
        "split_total": split_total,
        "test_files": test_files,
        "test_count": len(test_files),
        "timestamp": datetime.now().isoformat(),
    }

    manifest_file = manifest_path / "collection_manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2))
    print(f"Collection manifest written to {manifest_file}")


def verify_shard_consistency(args: argparse.Namespace) -> bool:
    """
    Issue #2189: 验证分片一致性

    验证所有分片全集等于不分片集合
    """
    print("\n" + "=" * 70)
    print("验证分片一致性")
    print("=" * 70)

    targets = select_targets(args)

    # 不分片
    all_files = discover_test_files(targets)
    print(f"\n不分片: {len(all_files)} files")

    # 所有分片
    shards = []
    for i in range(args.split_total):
        shard = apply_split(targets, args.split_total, i + 1)
        shards.extend(shard)

    # 去重后比较
    unique_shards = sorted(set(shards))
    print(f"分片全集: {len(unique_shards)} unique files")

    if sorted(all_files) != unique_shards:
        print("\n✗ 分片一致性检查失败")
        print(f"  Expected: {len(all_files)} files")
        print(f"  Got: {len(unique_shards)} unique files")
        return False

    print("\n✓ 分片一致性检查通过")
    return True


def frontend_dist_index() -> Path:
    return PROJECT_ROOT / "static" / "js" / "dist" / "index.html"


def ensure_frontend_built(category: str) -> None:
    if not category_needs_server(category):
        return
    if frontend_dist_index().exists():
        return
    raise RuntimeError(
        "Frontend build is missing. Run 'cd frontend && npm ci && npm run build' "
        "before server-dependent extended tests."
    )


def can_connect(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def health_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/health"


def is_healthy(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(health_url(base_url), timeout=2) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def wait_for_health(base_url: str, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_healthy(base_url):
            return
        time.sleep(2)
    raise TimeoutError(f"Open ACE did not become healthy at {health_url(base_url)}")


def default_playwright_browsers_path(home: Path) -> Path:
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "ms-playwright"
    return home / ".cache" / "ms-playwright"


def preserve_playwright_browser_cache(env: dict[str, str]) -> None:
    if env.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    home = Path(env.get("HOME", str(Path.home()))).expanduser()
    browsers_path = default_playwright_browsers_path(home)
    if browsers_path.exists():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)


def prepare_test_home(
    env: dict[str, str], isolated_home: bool
) -> tempfile.TemporaryDirectory | None:
    if not isolated_home:
        return None
    preserve_playwright_browser_cache(env)
    tmp_home = tempfile.TemporaryDirectory(prefix="open-ace-extended-tests-")
    env["HOME"] = tmp_home.name
    return tmp_home


def sqlite_has_table(db_path: Path, table_name: str) -> bool:
    if not db_path.exists():
        return False
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
    return row is not None


def ensure_sqlite_schema(env: dict[str, str]) -> None:
    config_dir = Path(env["HOME"]) / ".open-ace"
    db_path = config_dir / "ace.db"
    if sqlite_has_table(db_path, "tenants"):
        return

    schema_path = PROJECT_ROOT / "schema" / "schema-sqlite.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"SQLite schema not found: {schema_path}")

    config_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_path.read_text())


def initialize_database(env: dict[str, str]) -> None:
    config_dir = Path(env["HOME"]) / ".open-ace"
    config_dir.mkdir(parents=True, exist_ok=True)
    ensure_sqlite_schema(env)
    subprocess.run([sys.executable, "scripts/init_db.py"], cwd=PROJECT_ROOT, env=env, check=True)


def start_server_if_needed(
    args: argparse.Namespace, env: dict[str, str]
) -> subprocess.Popen | None:
    if not category_needs_server(args.category) or args.server == "skip":
        return None
    ensure_frontend_built(args.category)
    if is_healthy(args.base_url):
        print(f"Reusing healthy Open ACE server at {args.base_url}")
        return None
    if args.server == "reuse":
        raise RuntimeError(f"No healthy Open ACE server found at {args.base_url}")

    initialize_database(env)
    host_port = args.base_url.replace("http://", "").replace("https://", "").split("/", 1)[0]
    host, port_text = host_port.rsplit(":", 1)
    if can_connect(host, int(port_text)):
        raise RuntimeError(
            f"Port {port_text} is in use, but {health_url(args.base_url)} is not healthy"
        )

    # Issue #2185: Set security mode for test server
    env.setdefault("OPENACE_SECURITY_MODE", "development")
    env.setdefault("FLASK_ENV", "testing")
    # Issue #2185: Set test keys for development mode
    # These are only for testing, never for production
    env.setdefault("SECRET_KEY", "test-secret-key-for-extended-tests-32ch")
    env.setdefault("OPENACE_ENCRYPTION_KEY", "test-encryption-key-for-extended-tests-32")
    env.setdefault("UPLOAD_AUTH_KEY", "test-upload-auth-key-for-extended-tests-32")
    env.setdefault("SCHEDULER_HEALTH_MONITOR_ENABLED", "false")
    env.setdefault("DATA_FETCH_ENABLED", "false")
    env.setdefault("HEADLESS", "true")
    env["BASE_URL"] = args.base_url

    print(f"Starting Open ACE test server for {args.base_url}")
    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_health(args.base_url)
    except Exception:
        if proc.poll() is None:
            proc.terminate()
        output = ""
        if proc.stdout:
            try:
                output = proc.stdout.read()
            except OSError:
                output = ""
        raise RuntimeError(f"Failed to start Open ACE test server.\n{output}") from None
    return proc


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env = os.environ.copy()
    env.setdefault("BASE_URL", args.base_url)
    test_home = prepare_test_home(env, args.isolated_home)
    server_proc: subprocess.Popen | None = None

    try:
        # Issue #2189: 分片一致性验证
        if args.verify_shard:
            if not verify_shard_consistency(args):
                return 1
            return 0

        cmd = build_pytest_command(args)
        print("Pytest command:")
        print(" ".join(cmd))

        # Issue #2189: 写入 collection manifest
        targets = select_targets(args)
        test_files = discover_test_files(targets)
        test_files = apply_split(test_files, args.split_total, args.split_group)
        write_collection_manifest(
            test_files, args.category, args.split_group, args.split_total, args.manifest_dir
        )

        if args.dry_run:
            return 0
        server_proc = start_server_if_needed(args, env)
        return subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, check=False).returncode
    finally:
        stop_server(server_proc)
        if test_home is not None:
            test_home.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
