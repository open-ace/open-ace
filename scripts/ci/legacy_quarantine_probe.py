#!/usr/bin/env python3
"""Weekly probe of ci/legacy-issue-quarantine.json nodeids.

Each entry declares an ``expected_probe_outcome`` of ``timeout`` (the only
permitted value — see ``legacy_issue_baseline.PROBE_OUTCOMES``). The probe runs
the nodeid in a subprocess under a hard timeout (pytest-timeout cannot kill a
thread deadlock, so ``subprocess.run(timeout=)`` is the kill mechanism) and
compares the observed outcome to the declared one.

- match (e.g. still times out) → known quarantine debt, green.
- mismatch (recovered → ``pass``, or behavior/infra change → ``fail``) → the
  entry must be removed/updated; the probe fails so quarantine cannot drift
  silently. pytest rc 2-5 (collection/usage/internal) are never matchable and
  always read as a probe failure.

Fails closed on any quarantine config error (missing/corrupt/expired/invalid).
Writes per-nodeid logs + a structured ``test-results/quarantine-probe-results.json``.

CLI flags (``--quarantine`` / ``--hard-timeout`` / ``--pytest-timeout``
/ ``--out-dir``) exist so contract tests can drive the real entry point from the
repo root with a synthetic quarantine and a short timeout instead of waiting on
the 604 deadlock.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

# The hard subprocess timeout (subprocess.run timeout=) is the kill mechanism
# that actually terminates a thread deadlock. pytest-timeout's soft ``--timeout``
# is an optional courtesy (its thread method cannot interrupt a deadlock either);
# the probe must not hard-fail if the plugin is absent.
try:
    import pytest_timeout  # noqa: F401

    _HAS_PYTEST_TIMEOUT = True
except ImportError:  # pragma: no cover - exercised in CI which has the plugin
    _HAS_PYTEST_TIMEOUT = False

# This script lives at scripts/ci/legacy_quarantine_probe.py — repo root is two
# parents up. parents[1] would resolve to scripts/ and the import/quarantine
# paths below would become scripts/scripts/... and scripts/ci/... (FileNotFoundError
# at import time), so the probe would never reach config validation.
ROOT = Path(__file__).resolve().parents[2]
QUARANTINE = ROOT / "ci" / "legacy-issue-quarantine.json"
HARD_TIMEOUT = 180  # seconds — kills a thread deadlock pytest-timeout cannot
PYTEST_TIMEOUT = 60  # seconds — soft per-test timeout pytest-timeout enforces


def _load_lib():
    spec = importlib.util.spec_from_file_location(
        "_lib_baseline", str(ROOT / "scripts" / "legacy_issue_baseline.py")
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_lib_baseline"] = mod
    spec.loader.exec_module(mod)
    return mod


def _classify(timed_out: bool, rc: int) -> str:
    if timed_out:
        return "timeout"
    return "pass" if rc == 0 else "fail"


def _probe_entry(entry, *, hard_timeout: int, pytest_timeout: int, cwd: Path, outdir: Path):
    """Run one quarantined nodeid in a subprocess and return its result record.

    pytest-timeout cannot terminate a thread deadlock, so ``subprocess.run`` is
    given ``hard_timeout`` as the kill mechanism. The returned dict is what gets
    serialized into ``quarantine-probe-results.json``.
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        entry.nodeid,
        "-m",
        "not postgres",
        "-p",
        "no:cacheprovider",
        "-q",
        "--no-header",
    ]
    if _HAS_PYTEST_TIMEOUT:
        # Soft per-test timeout; only added when pytest-timeout is installed.
        # It cannot interrupt a thread deadlock — the subprocess hard timeout
        # below is the real kill mechanism — but it gives cleaner diagnostics.
        cmd += ["--timeout", str(pytest_timeout)]
    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=hard_timeout)
        rc, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        rc, timed_out = 124, True
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout, stderr = stdout.decode(errors="replace"), stderr.decode(errors="replace")
    duration = round(time.monotonic() - start, 1)

    outcome = _classify(timed_out, rc)
    matches = outcome == entry.expected_probe_outcome
    slug = "".join(c if c.isalnum() else "_" for c in entry.nodeid)[:80]
    (outdir / f"probe-{slug}.log").write_text(
        f"$ {' '.join(cmd)}\nexit={rc} timed_out={timed_out} duration={duration}s "
        f"expected={entry.expected_probe_outcome} outcome={outcome}\n\n"
        f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n"
    )
    return {
        "nodeid": entry.nodeid,
        "expected": entry.expected_probe_outcome,
        "outcome": outcome,
        "returncode": rc,
        "timed_out": timed_out,
        "duration_s": duration,
        "matches": matches,
        "owner": entry.owner,
        "tracking_issue": entry.tracking_issue,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quarantine", default=str(QUARANTINE))
    parser.add_argument("--hard-timeout", type=int, default=HARD_TIMEOUT)
    parser.add_argument("--pytest-timeout", type=int, default=PYTEST_TIMEOUT)
    parser.add_argument("--out-dir", default=str(ROOT / "test-results"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    quarantine = Path(args.quarantine)
    outdir = Path(args.out_dir)
    lib = _load_lib()
    if not quarantine.exists():
        print(
            f"FATAL: {quarantine} missing — cannot probe without the tracked exclusions.",
            file=sys.stderr,
        )
        return 1
    try:
        entries = lib.load_quarantine(quarantine)
    except Exception as exc:
        print(f"FATAL: cannot load quarantine: {exc}", file=sys.stderr)
        return 1
    invalid = lib.validate_quarantine(entries, (), datetime.date.today().isoformat())
    if invalid:
        print("FATAL: invalid quarantine:\n  " + "\n  ".join(invalid), file=sys.stderr)
        return 1

    outdir.mkdir(parents=True, exist_ok=True)
    results = []
    changed = False
    for e in entries:
        result = _probe_entry(
            e,
            hard_timeout=args.hard_timeout,
            pytest_timeout=args.pytest_timeout,
            cwd=ROOT,
            outdir=outdir,
        )
        results.append(result)
        if not result["matches"]:
            changed = True
        tag = "OK (expected)" if result["matches"] else "CHANGED"
        print(
            f"[{tag}] {e.nodeid}: expected={result['expected']} outcome={result['outcome']} "
            f"rc={result['returncode']} {result['duration_s']}s"
        )

    (outdir / "quarantine-probe-results.json").write_text(json.dumps(results, indent=2) + "\n")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
