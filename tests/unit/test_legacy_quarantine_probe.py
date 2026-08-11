"""Contract tests for the weekly quarantine probe entry point.

These run the REAL probe CLI (``scripts/ci/legacy_quarantine_probe.py``) from the
repo root via subprocess — they exist primarily to prove the entry point imports
and resolves the repo root correctly (the ``ROOT=parents[2]`` fix; ``parents[1]``
crashed at import with FileNotFoundError) and that it fails closed on bad
quarantine config. They also exercise the outcome-matching logic with synthetic
slow/fast tests so a recovered or behavior-changed entry cannot stay green.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROBE = PROJECT_ROOT / "scripts" / "ci" / "legacy_quarantine_probe.py"


def _run_probe(
    quarantine: Path, *, hard_timeout: int = 30, out_dir: Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(PROBE),
            "--quarantine",
            str(quarantine),
            "--hard-timeout",
            str(hard_timeout),
            "--out-dir",
            str(out_dir),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _write_quarantine(path: Path, nodeid: str) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "schema": "openace-legacy-issue-quarantine",
                "entries": [
                    {
                        "nodeid": nodeid,
                        "reason": "synthetic contract-test entry",
                        "owner": "contract-test",
                        "tracking_issue": "https://github.com/open-ace/open-ace/issues/2457",
                        "exit_condition": "contract test only",
                        "expires_on": "2099-01-01",
                        "expected_probe_outcome": "timeout",
                    }
                ],
            }
        )
    )


def test_probe_entrypoint_fails_closed_on_missing_quarantine(tmp_path):
    # P0: the entry point must import + resolve repo root, then fail closed
    # (exit 1) on a missing quarantine — no FileNotFoundError at import time.
    proc = _run_probe(tmp_path / "does-not-exist.json", out_dir=tmp_path)
    assert proc.returncode == 1, proc.stderr
    assert "missing" in proc.stderr.lower()


def test_probe_entrypoint_fails_closed_on_corrupt_quarantine(tmp_path):
    q = tmp_path / "q.json"
    q.write_text("{ not valid json")
    proc = _run_probe(q, out_dir=tmp_path)
    assert proc.returncode == 1, proc.stderr
    assert "cannot load quarantine" in proc.stderr.lower()


def test_probe_entrypoint_fails_closed_on_expired_or_invalid(tmp_path):
    q = tmp_path / "q.json"
    q.write_text(
        json.dumps(
            {
                "version": 1,
                "schema": "openace-legacy-issue-quarantine",
                "entries": [
                    {
                        "nodeid": "tests/issues/604/x.py::a",
                        "reason": "r",
                        "owner": "o",
                        "tracking_issue": "t",
                        "exit_condition": "e",
                        "expires_on": "2000-01-01",  # expired
                        "expected_probe_outcome": "timeout",
                    }
                ],
            }
        )
    )
    proc = _run_probe(q, out_dir=tmp_path)
    assert proc.returncode == 1, proc.stderr
    assert "invalid quarantine" in proc.stderr.lower()
    assert "expired" in proc.stderr.lower()


def test_probe_green_when_declared_timeout_still_times_out(tmp_path):
    # A synthetic nodeid that hangs past the hard timeout → outcome "timeout"
    # matches the declared "timeout" → exit 0 (known debt, green).
    slow = tmp_path / "test_probe_slow.py"
    slow.write_text("import time\n\ndef test_hang():\n    time.sleep(30)\n")
    q = tmp_path / "q.json"
    _write_quarantine(q, f"{slow}::test_hang")
    proc = _run_probe(q, hard_timeout=3, out_dir=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    results = json.loads((tmp_path / "quarantine-probe-results.json").read_text())
    assert results[0]["outcome"] == "timeout"
    assert results[0]["timed_out"] is True
    assert results[0]["matches"] is True


def test_probe_fails_when_quarantined_nodeid_recovers(tmp_path):
    # A synthetic nodeid that passes fast while declared "timeout" → outcome
    # "pass" != "timeout" → exit 1 (recovered; must be removed from quarantine).
    fast = tmp_path / "test_probe_fast.py"
    fast.write_text("def test_ok():\n    assert True\n")
    q = tmp_path / "q.json"
    _write_quarantine(q, f"{fast}::test_ok")
    proc = _run_probe(q, hard_timeout=20, out_dir=tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    results = json.loads((tmp_path / "quarantine-probe-results.json").read_text())
    assert results[0]["outcome"] == "pass"
    assert results[0]["matches"] is False


def test_probe_writes_per_nodeid_log_artifact(tmp_path):
    slow = tmp_path / "test_probe_slow.py"
    slow.write_text("import time\n\ndef test_hang():\n    time.sleep(30)\n")
    q = tmp_path / "q.json"
    _write_quarantine(q, f"{slow}::test_hang")
    proc = _run_probe(q, hard_timeout=3, out_dir=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    logs = list(tmp_path.glob("probe-*.log"))
    assert logs, "expected a per-nodeid probe log artifact"
    body = logs[0].read_text()
    assert "expected=timeout" in body and "outcome=timeout" in body
    assert "--- stdout ---" in body and "--- stderr ---" in body
