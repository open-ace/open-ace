#!/usr/bin/env python3
"""
Run the server-dependent Open ACE test suites from CI or a local checkout.

The default CI suite intentionally excludes tests/e2e and tests/issues because
they need a live web server and can be slow. This runner is the shared entry
point for scheduled, release, PR critical, and manual extended-test runs.
"""

from __future__ import annotations

import argparse
import json
import math
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
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
        "tests/e2e/browser/test_login.py",
        "tests/e2e/browser/test_navigation.py::test_sidebar_menu_visible",
        "tests/e2e/browser/test_navigation.py::test_menu_navigation",
    ],
    "regression": ["tests/e2e/browser"],
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


@dataclass
class ServerHandle:
    process: subprocess.Popen
    log_file: TextIO
    log_path: Path
    stopped_by_runner: bool = False


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
    parser.add_argument(
        "--selection-json",
        default="",
        help="Run the exact ids from a selector selection.json instead of a category tree.",
    )
    parser.add_argument(
        "--e2e-attempts",
        default="",
        help="Append authoritative per-attempt JSONL records to this path.",
    )
    parser.add_argument(
        "--envelope-json",
        default="",
        help="Write a machine-readable run envelope to this path.",
    )
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


def load_selection_targets(path: str) -> list[str]:
    selection_path = Path(path)
    payload = json.loads(selection_path.read_text(encoding="utf-8"))
    targets = list(payload.get("normal") or []) + list(payload.get("advisory") or [])
    if not targets:
        raise ValueError(f"No executable targets found in selection: {selection_path}")
    return [str(target) for target in targets]


def discover_test_files(targets: list[str]) -> list[str]:
    files: list[Path] = []
    for target in targets:
        if target.startswith("standalone::"):
            continue
        path = PROJECT_ROOT / target_path(target)
        if path.is_file():
            files.append(path)
            continue
        files.extend(path.rglob("test_*.py"))
        files.extend(path.rglob("e2e_*.py"))
    unique = sorted({file.relative_to(PROJECT_ROOT).as_posix() for file in files})
    return unique


def resolved_targets(args: argparse.Namespace) -> list[str]:
    if args.selection_json:
        selected = load_selection_targets(args.selection_json)
        standalone = [item for item in selected if item.startswith("standalone::")]
        pytest_targets = [item for item in selected if not item.startswith("standalone::")]
        if standalone and args.split_total > 1:
            raise ValueError("--selection-json with standalone targets cannot be sharded")
        if args.split_total == 1:
            return pytest_targets + standalone
        return apply_split(pytest_targets, args.split_total, args.split_group)
    targets = select_targets(args)
    return apply_split(targets, args.split_total, args.split_group)


def standalone_targets(targets: list[str]) -> list[str]:
    return [target for target in targets if target.startswith("standalone::")]


def pytest_targets(targets: list[str]) -> list[str]:
    return [target for target in targets if not target.startswith("standalone::")]


def apply_split(targets: list[str], split_total: int, split_group: int) -> list[str]:
    if split_total < 1:
        raise ValueError("--split-total must be >= 1")
    if split_group < 1 or split_group > split_total:
        raise ValueError("--split-group must be between 1 and --split-total")

    # Always expand to file list for consistency (Issue #2189)
    files = discover_test_files(targets)

    if not files:
        raise ValueError(f"No test files discovered from targets: {targets}")

    if split_total == 1:
        # Issue #2189: Return file list even for non-split mode
        print(f"Collected {len(files)} test files")
        return files

    selected = [
        file for index, file in enumerate(files) if (index % split_total) == (split_group - 1)
    ]
    if not selected:
        raise ValueError(f"Shard {split_group}/{split_total} selected no test files")
    print(f"Selected {len(selected)} files for shard {split_group}/{split_total}")
    return selected


def load_baseline() -> dict | None:
    """Load test baseline from .test-baseline.json (Issue #2189)."""
    baseline_path = PROJECT_ROOT / ".test-baseline.json"
    if not baseline_path.exists():
        return None

    import json

    try:
        with open(baseline_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: Failed to load baseline: {e}")
        return None


def check_baseline(category: str, file_count: int, split_total: int = 1) -> bool:
    """Check a full suite or shard against its test-file baseline."""
    baseline = load_baseline()
    if not baseline:
        return True

    # Map category to baseline layer
    layer_map = {
        "default": "default",
        "critical": "critical",
        "e2e": "e2e_pytest",
        "issues": "issues",
    }

    layer_name = layer_map.get(category)
    if not layer_name or layer_name not in baseline.get("layers", {}):
        return True

    layer_baseline = baseline["layers"][layer_name]
    min_files = math.ceil(layer_baseline.get("min_files", 0) / split_total)

    if file_count < min_files:
        tolerance = baseline.get("tolerance", {})
        threshold = tolerance.get("require_review_threshold", 10)
        decrease_pct = ((min_files - file_count) / min_files * 100) if min_files > 0 else 0

        if decrease_pct >= threshold:
            print(
                f"ERROR: Test file count {file_count} below baseline {min_files} "
                f"({decrease_pct:.1f}% decrease >= {threshold}% threshold)"
            )
            return False
        else:
            print(
                f"WARNING: Test file count {file_count} below baseline {min_files} "
                f"({decrease_pct:.1f}% decrease)"
            )

    return True


def print_collection_manifest(files: list[str]) -> None:
    """Print the exact execution manifest (files, nodeids, or standalone ids)."""
    print("\n=== Test Collection Manifest ===")
    print(f"Total targets: {len(files)}")
    if files:
        print("\nCollected targets:")
        for file in files:
            print(f"  - {file}")
    print("=" * 40 + "\n")


def _quarantine_nodeids(path=None) -> list[str]:
    """Read quarantined nodeids from ci/legacy-issue-quarantine.json.

    Fail-closed: a missing, corrupt, wrong-schema/version, or expired entry must
    NOT silently fall back to "no quarantine" — that would re-run a known-
    deadlocking nodeid and hang the shard for the full job timeout. Any error
    raises SystemExit and aborts the run. Expiry is checked so a stale quarantine
    cannot silently keep deselecting; full nodeid-collectability is enforced by
    the comparator (same shared loader).
    """
    import datetime
    import importlib.util

    if path is None:
        path = PROJECT_ROOT / "ci" / "legacy-issue-quarantine.json"
    path = Path(path)
    if not path.exists():
        raise SystemExit(
            "ci/legacy-issue-quarantine.json is missing; refusing to run the "
            "issue shard without the tracked exclusions (would deadlock)."
        )
    try:
        # Reuse the comparator's strict loader (schema + version + entry types).
        spec = importlib.util.spec_from_file_location(
            "_lib_baseline", str(PROJECT_ROOT / "scripts" / "legacy_issue_baseline.py")
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_lib_baseline"] = mod  # register before exec (dataclasses PEP 563)
        spec.loader.exec_module(mod)
        entries = mod.load_quarantine(path)
        today = datetime.date.today().isoformat()
        invalid = mod.validate_quarantine(entries, (), today)
        if invalid:
            raise SystemExit("invalid ci/legacy-issue-quarantine.json:\n  " + "\n  ".join(invalid))
    except SystemExit:
        raise
    except Exception as exc:  # corrupt JSON, wrong schema, parse error, ...
        raise SystemExit(f"cannot load ci/legacy-issue-quarantine.json: {exc}") from exc
    return [e.nodeid for e in entries]


def build_pytest_command(args: argparse.Namespace) -> list[str]:
    targets = resolved_targets(args)
    pytest_only = pytest_targets(targets)

    # Issue #2189: Print the file manifest. Item collection is separately gated
    # by pytest itself; a targeted issue run must not be compared with the full
    # legacy-suite baseline.
    print_collection_manifest(targets)
    if not pytest_only:
        return []
    targeted_issue_run = args.category == "issues" and bool(parse_issue_numbers(args))
    file_count = len(discover_test_files(pytest_only)) if args.selection_json else len(pytest_only)
    if not targeted_issue_run and not check_baseline(
        args.category, file_count, split_total=args.split_total
    ):
        raise ValueError(f"Test file count below baseline threshold for category: {args.category}")

    cmd = [sys.executable, "-m", "pytest", *pytest_only, "-m", "not postgres"]
    # Continue past per-file collection errors so every collectable nodeid gets a
    # terminal result; otherwise one bad file aborts the shard and leaves the
    # rest result-less, which the #2457 failure-baseline completeness gate would
    # (correctly) reject. Collection errors are still surfaced in the JUnit and
    # hard-failed by the comparator (never baselined).
    cmd.append("--continue-on-collection-errors")
    # Deselect nodeids tracked in ci/legacy-issue-quarantine.json (e.g. a test
    # that deadlocks the shard). The same list is read by the comparator, which
    # excludes them from the expected-executed set and reports them as debt, so
    # the deselect + the manifest stay consistent (local == CI).
    for nodeid in _quarantine_nodeids():
        cmd.extend(["--deselect", nodeid])
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
    if args.e2e_attempts:
        cmd.extend(["-p", "scripts.e2e.pytest_attempts", f"--e2e-attempts={args.e2e_attempts}"])
    cmd.extend(args.extra_pytest_arg)
    return cmd


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


def isolated_base_url(base_url: str) -> str:
    """Return a loopback URL with a currently available ephemeral port."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported base URL scheme: {base_url}")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return urlunsplit((parsed.scheme, f"127.0.0.1:{port}", parsed.path, "", ""))


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


def configure_server_address(env: dict[str, str], base_url: str) -> None:
    """Make the spawned server listen at the same address used by the tests."""
    parsed = urlsplit(base_url)
    if parsed.hostname is None or parsed.port is None:
        raise ValueError(f"Base URL must include a host and port: {base_url}")
    config_path = Path(env["HOME"]) / ".open-ace" / "config.json"
    try:
        config = json.loads(config_path.read_text()) if config_path.exists() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid test server config: {config_path}") from exc
    server_config = config.setdefault("server", {})
    server_config["web_host"] = parsed.hostname
    server_config["web_port"] = parsed.port
    config_path.write_text(json.dumps(config, indent=2) + "\n")


def start_server_if_needed(args: argparse.Namespace, env: dict[str, str]) -> ServerHandle | None:
    if not category_needs_server(args.category) or args.server == "skip":
        return None
    ensure_frontend_built(args.category)
    if is_healthy(args.base_url):
        print(f"Reusing healthy Open ACE server at {args.base_url}")
        return None
    if args.server == "reuse":
        raise RuntimeError(f"No healthy Open ACE server found at {args.base_url}")

    initialize_database(env)
    configure_server_address(env, args.base_url)
    parsed = urlsplit(args.base_url)
    host = parsed.hostname
    port = parsed.port
    if host is None or port is None:
        raise ValueError(f"Base URL must include a host and port: {args.base_url}")
    if can_connect(host, port):
        raise RuntimeError(f"Port {port} is in use, but {health_url(args.base_url)} is not healthy")

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
    log_path = PROJECT_ROOT / "test-results" / f"open-ace-server-{args.category}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w")
    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    handle = ServerHandle(proc, log_file, log_path)
    try:
        wait_for_health(args.base_url)
    except Exception:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=15)
        log_file.close()
        output = log_path.read_text(errors="replace") if log_path.exists() else ""
        raise RuntimeError(f"Failed to start Open ACE test server.\n{output}") from None
    return handle


def stop_server(handle: ServerHandle | None) -> None:
    if handle is None:
        return
    proc = handle.process
    if proc.poll() is None:
        handle.stopped_by_runner = True
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    handle.log_file.close()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _current_head_sha() -> str | None:
    for value in (os.environ.get("GITHUB_SHA"), os.environ.get("COMMIT_SHA")):
        if value:
            return value
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return proc.stdout.strip() or None
    return None


def _current_contract_key() -> str | None:
    try:
        from scripts.e2e.common import CONTRACT_SCHEMA_NAME, contract_key_identity, load_artifact

        contract = load_artifact(PROJECT_ROOT / "ci" / "e2e-contract.json", CONTRACT_SCHEMA_NAME)
        return contract_key_identity(contract)
    except Exception:
        return None


def _load_attempt_records(path: str) -> list[dict[str, object]]:
    attempts_path = Path(path)
    if not attempts_path.exists():
        return []
    records: list[dict[str, object]] = []
    for line in attempts_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def _canonical_outcome(report_outcome: str) -> str:
    if report_outcome == "passed":
        return "pass"
    if report_outcome == "skipped":
        return "skip"
    return "fail"


def _summarize_attempt_records(
    records: list[dict[str, object]], server_evidence: dict[str, object]
) -> list[dict[str, object]]:
    from scripts.e2e.comparator import classify_failure, fingerprint_failure

    by_node: dict[str, list[dict[str, object]]] = {}
    for record in records:
        nodeid = str(record.get("nodeid", "")).strip()
        if not nodeid:
            continue
        by_node.setdefault(nodeid, []).append(record)

    outcomes: list[dict[str, object]] = []
    for nodeid in sorted(by_node):
        node_records = by_node[nodeid]
        attempts = sorted({int(record.get("attempt", 1)) for record in node_records})
        final = node_records[-1]
        call_records = [record for record in node_records if record.get("phase") == "call"]
        decision = call_records[-1] if call_records else final
        first_attempt_records = [
            record for record in node_records if int(record.get("attempt", 1)) == attempts[0]
        ]
        first_passed = all(record.get("outcome") == "passed" for record in first_attempt_records)
        final_outcome = _canonical_outcome(str(decision.get("outcome", "failed")))
        total_duration = round(
            sum(float(record.get("duration_seconds") or 0.0) for record in node_records), 3
        )
        summary: dict[str, object] = {
            "nodeid": nodeid,
            "attempts": len(attempts),
            "first_attempt_outcome": "pass" if first_passed else "fail",
            "final_outcome": final_outcome,
            "duration_seconds": total_duration,
        }
        if final_outcome == "fail":
            failed_records = [
                record
                for record in node_records
                if record.get("outcome") not in ("passed", "rerun")
            ]
            failed = failed_records[-1] if failed_records else decision
            failure = {
                "phase": failed.get("phase", "call"),
                "exception_class": failed.get("exception_class"),
                "message": failed.get("message"),
                "timeout": "timeout"
                in f"{failed.get('exception_class', '')} {failed.get('message', '')}".lower(),
            }
            summary["category"] = classify_failure(failure, server_evidence)
            summary["fingerprint"] = fingerprint_failure(failure)
            summary["exception_class"] = failed.get("exception_class")
            summary["message"] = failed.get("message")
        outcomes.append(summary)
    return outcomes


def _standalone_script_path(item_id: str) -> str:
    _, _, path = item_id.partition("::")
    if not path:
        raise ValueError(f"Invalid standalone target id: {item_id}")
    return path


def _run_standalone_targets(
    target_ids: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: int,
) -> list[dict[str, object]]:
    from scripts.e2e.common import failure_fingerprint

    results: list[dict[str, object]] = []
    for item_id in target_ids:
        script_path = _standalone_script_path(item_id)
        started = time.time()
        try:
            completed = subprocess.run(
                [sys.executable, script_path],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
            duration = round(time.time() - started, 3)
            passed = completed.returncode == 0
            result: dict[str, object] = {
                "nodeid": item_id,
                "attempts": 1,
                "first_attempt_outcome": "pass" if passed else "fail",
                "final_outcome": "pass" if passed else "fail",
                "duration_seconds": duration,
            }
            if not passed:
                message = f"{script_path} exited with code {completed.returncode}"
                result["category"] = "test_body_exception"
                result["fingerprint"] = failure_fingerprint("StandaloneExitError", message)
                result["exception_class"] = "StandaloneExitError"
                result["message"] = message
                result["return_code"] = completed.returncode
            results.append(result)
        except subprocess.TimeoutExpired:
            duration = round(time.time() - started, 3)
            message = f"{script_path} timed out after {timeout_seconds}s"
            results.append(
                {
                    "nodeid": item_id,
                    "attempts": 1,
                    "first_attempt_outcome": "fail",
                    "final_outcome": "fail",
                    "duration_seconds": duration,
                    "category": "timeout",
                    "fingerprint": failure_fingerprint("TimeoutExpired", message),
                    "exception_class": "TimeoutExpired",
                    "message": message,
                }
            )
    return results


def _write_run_envelope(
    path: str,
    *,
    args: argparse.Namespace,
    env: dict[str, str],
    cmd: list[str],
    selected_targets: list[str],
    server_handle: ServerHandle | None,
    return_code: int,
    started_at: str,
    completed_at: str,
    standalone_outcomes: list[dict[str, object]] | None = None,
    error_message: str | None = None,
) -> None:
    if not path:
        return

    envelope_path = Path(path)
    envelope_path.parent.mkdir(parents=True, exist_ok=True)
    started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    completed_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    duration_seconds = max(0.0, (completed_dt - started_dt).total_seconds())
    server_ready = None
    server_log = None
    exit_info = {"code": None, "abnormal": False}
    if category_needs_server(args.category):
        server_ready = server_handle is not None or is_healthy(args.base_url)
    if server_handle is not None:
        server_log = str(server_handle.log_path)
        exit_code = server_handle.process.poll()
        exit_info["code"] = exit_code
        exit_info["abnormal"] = (
            exit_code is not None
            and not server_handle.stopped_by_runner
            and exit_code != 0
        )
    attempts_records = _load_attempt_records(args.e2e_attempts) if args.e2e_attempts else []
    server_evidence = {
        "readiness_achieved": server_ready,
        "exit": exit_info,
        "environment_missing": False,
        "liveness_failures": [],
        "base_url": args.base_url,
        "log_path": server_log,
    }
    outcomes = _summarize_attempt_records(attempts_records, server_evidence)
    if standalone_outcomes:
        outcomes.extend(standalone_outcomes)
        outcomes.sort(key=lambda item: str(item.get("nodeid", "")))
    payload = {
        "schema_name": "openace-e2e-run-envelope",
        "schema_version": 1,
        "category": args.category,
        "base_url": args.base_url,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": round(duration_seconds, 3),
        "duration_minutes": round(duration_seconds / 60.0, 3),
        "commit_sha": _current_head_sha(),
        "contract_key": _current_contract_key(),
        "job_conclusion": "success" if return_code == 0 else "failure",
        "return_code": return_code,
        "error": error_message,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "playwright_browsers_path": env.get("PLAYWRIGHT_BROWSERS_PATH"),
        "isolated_home": env.get("HOME"),
        "selected_targets": selected_targets,
        "pytest_command": cmd,
        "artifacts": {
            "junitxml": args.junitxml or None,
            "attempts_jsonl": args.e2e_attempts or None,
            "server_log": server_log,
        },
        "server": server_evidence,
        "outcomes": outcomes,
    }
    envelope_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env = os.environ.copy()
    test_home = prepare_test_home(env, args.isolated_home)
    if args.isolated_home and args.server == "auto" and category_needs_server(args.category):
        args.base_url = isolated_base_url(args.base_url)
    env["BASE_URL"] = args.base_url
    server_handle: ServerHandle | None = None
    selected_targets: list[str] = []
    cmd: list[str] = []
    standalone_outcomes: list[dict[str, object]] = []
    return_code = 1
    error_message: str | None = None
    started_at = _utc_now()

    try:
        selected_targets = resolved_targets(args)
        cmd = build_pytest_command(args)
        print("Pytest command:")
        print(" ".join(cmd))
        if args.dry_run:
            return 0
        server_handle = start_server_if_needed(args, env)
        return_code = 0
        if cmd:
            return_code = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, check=False).returncode
        standalone_selected = standalone_targets(selected_targets)
        if standalone_selected:
            standalone_outcomes = _run_standalone_targets(
                standalone_selected,
                env=env,
                timeout_seconds=args.timeout or 240,
            )
            if any(item.get("final_outcome") != "pass" for item in standalone_outcomes):
                return_code = return_code or 1
        return return_code
    except Exception as exc:
        error_message = str(exc)
        raise
    finally:
        completed_at = _utc_now()
        _write_run_envelope(
            args.envelope_json,
            args=args,
            env=env,
            cmd=cmd,
            selected_targets=selected_targets,
            server_handle=server_handle,
            return_code=return_code,
            started_at=started_at,
            completed_at=completed_at,
            standalone_outcomes=standalone_outcomes,
            error_message=error_message,
        )
        stop_server(server_handle)
        if test_home is not None:
            test_home.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
