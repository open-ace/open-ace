#!/usr/bin/env python3
"""Weekly probe of ci/legacy-issue-quarantine.json nodeids.

Each entry declares an ``expected_probe_outcome`` (``timeout`` / ``pass`` /
``fail``). The probe runs the nodeid in a subprocess under a hard timeout
(pytest-timeout cannot kill a thread deadlock, so ``subprocess.run(timeout=)``
is the kill mechanism) and compares the observed outcome to the declared one.

- match (e.g. still times out) → known quarantine debt, green.
- mismatch (recovered, or behavior/infra changed) → the entry must be removed/
  updated; the probe fails so quarantine cannot drift silently.

Fails closed on any quarantine config error (missing/corrupt/expired/invalid).
Writes per-nodeid logs + a structured ``test-results/quarantine-probe-results.json``.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUARANTINE = ROOT / "ci" / "legacy-issue-quarantine.json"
OUTDIR = ROOT / "test-results"
HARD_TIMEOUT = 180  # seconds — kills a thread deadlock pytest-timeout cannot


def _load_lib():
    spec = importlib.util.spec_from_file_location(
        "_lib_baseline", str(ROOT / "scripts" / "legacy_issue_baseline.py")
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_lib_baseline"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    lib = _load_lib()
    if not QUARANTINE.exists():
        print(
            f"FATAL: {QUARANTINE} missing — cannot probe without the tracked exclusions.",
            file=sys.stderr,
        )
        return 1
    try:
        entries = lib.load_quarantine(QUARANTINE)
    except Exception as exc:
        print(f"FATAL: cannot load quarantine: {exc}", file=sys.stderr)
        return 1
    invalid = lib.validate_quarantine(entries, (), datetime.date.today().isoformat())
    if invalid:
        print("FATAL: invalid quarantine:\n  " + "\n  ".join(invalid), file=sys.stderr)
        return 1

    OUTDIR.mkdir(parents=True, exist_ok=True)
    results = []
    changed = False
    for e in entries:
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            e.nodeid,
            "-m",
            "not postgres",
            "-p",
            "no:cacheprovider",
            "-q",
            "--no-header",
            "--timeout",
            "60",
        ]
        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                cmd, cwd=ROOT, capture_output=True, text=True, timeout=HARD_TIMEOUT
            )
            rc, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            rc, timed_out = 124, True
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout, stderr = stdout.decode(errors="replace"), stderr.decode(errors="replace")
        duration = round(time.monotonic() - start, 1)

        if timed_out:
            outcome = "timeout"
        elif rc == 0:
            outcome = "pass"
        else:
            outcome = "fail"
        matches = outcome == e.expected_probe_outcome
        if not matches:
            changed = True

        slug = "".join(c if c.isalnum() else "_" for c in e.nodeid)[:80]
        (OUTDIR / f"probe-{slug}.log").write_text(
            f"$ {' '.join(cmd)}\nexit={rc} timed_out={timed_out} duration={duration}s "
            f"expected={e.expected_probe_outcome} outcome={outcome}\n\n"
            f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n"
        )
        results.append(
            {
                "nodeid": e.nodeid,
                "expected": e.expected_probe_outcome,
                "outcome": outcome,
                "returncode": rc,
                "timed_out": timed_out,
                "duration_s": duration,
                "matches": matches,
                "owner": e.owner,
                "tracking_issue": e.tracking_issue,
            }
        )
        tag = "OK (expected)" if matches else "CHANGED"
        print(
            f"[{tag}] {e.nodeid}: expected={e.expected_probe_outcome} outcome={outcome} rc={rc} {duration}s"
        )

    (OUTDIR / "quarantine-probe-results.json").write_text(json.dumps(results, indent=2) + "\n")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
