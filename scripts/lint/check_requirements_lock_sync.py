#!/usr/bin/env python3
"""Check that requirements.lock stays consistent with requirements.txt.

The production Docker image installs the hash-pinned ``requirements.lock`` with
``pip install --require-hashes`` -- so pip installs *exactly* what the lock names
and nothing else. If a top-level dependency is added to (or version-tightened in)
``requirements.txt`` without regenerating the lock, the image ships a stale or
missing package. No PR CI lane builds the image (the docker job runs only on
push to main), so that failure would otherwise surface only at runtime.

This guard is deterministic and needs no network: it fails when the lock does
not satisfy every top-level requirement declared in requirements.txt. It does
NOT re-resolve against the mirror (that resolution drifts as the mirror syncs,
which would make a byte-exact gate flaky). Regenerate the lock with
``scripts/gen_requirements_lock.sh`` when this fails.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQ = ROOT / "requirements.txt"
LOCK = ROOT / "requirements.lock"

_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+-]*)")
_REQ_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        m = _PIN_RE.match(line)
        if m:
            pins[_canon(m.group(1))] = m.group(2)
    return pins


def parse_requirements(text: str) -> list[tuple[str, str, str]]:
    """Return (raw_name, canonical_name, specifier) for each top-level requirement."""
    out: list[tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        line = line.split(";", 1)[0].strip()  # drop environment markers
        m = _REQ_RE.match(line)
        if not m:
            continue
        out.append((m.group(1), _canon(m.group(1)), m.group(2).strip()))
    return out


def main() -> int:
    if not REQ.exists() or not LOCK.exists():
        print(
            f"[requirements-lock-sync] missing {REQ.name} or {LOCK.name}",
            file=sys.stderr,
        )
        return 1

    pins = parse_lock(LOCK.read_text())
    reqs = parse_requirements(REQ.read_text())

    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        have_packaging = True
    except Exception:  # pragma: no cover - packaging is always present in CI/dev
        have_packaging = False

    problems: list[str] = []
    for raw_name, canon, spec in reqs:
        if canon not in pins:
            problems.append(
                f"  - {raw_name}: in requirements.txt but MISSING from requirements.lock"
            )
            continue
        if spec and have_packaging:
            locked = pins[canon]
            try:
                if Version(locked) not in SpecifierSet(spec):
                    problems.append(f"  - {raw_name}: locked {locked} does not satisfy '{spec}'")
            except Exception:
                # Unparseable specifier/version: skip rather than false-fail.
                pass

    if problems:
        print(
            "requirements.lock is out of sync with requirements.txt:\n"
            + "\n".join(problems)
            + "\n\nRegenerate the lock:  scripts/gen_requirements_lock.sh\n",
            file=sys.stderr,
        )
        return 1

    if not have_packaging:
        print("[requirements-lock-sync] packaging unavailable; checked names only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
