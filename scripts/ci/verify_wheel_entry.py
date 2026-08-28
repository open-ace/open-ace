#!/usr/bin/env python3
"""Verify built wheels ship the console-script entry module (issue #3171).

The ``openace`` console script targets the top-level ``cli`` module. If the
wheel omits it (which packages.find alone allows), ``pip install`` succeeds
and ``openace --help`` dies with ModuleNotFoundError — the weekly
package-install failure mode. This checker runs inside the shared ``package``
suite so every PR build behaviorally pins the wheel contents instead of
waiting for the weekly lane. Stdlib-only on purpose: it executes under the
suite runner's isolated environment.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

EXPECTED_MODULE = "cli.py"
EXPECTED_ENTRY = re.compile(r"^openace\s*=\s*cli:main\s*$", re.MULTILINE)


def verify_wheel(wheel: Path) -> str | None:
    """Return a failure description, or None when the wheel is complete."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if EXPECTED_MODULE not in names:
            return f"{wheel.name}: missing top-level {EXPECTED_MODULE}"
        entry_point_files = [name for name in names if name.endswith("entry_points.txt")]
        combined = "\n".join(
            archive.read(name).decode("utf-8", errors="replace") for name in entry_point_files
        )
    if not EXPECTED_ENTRY.search(combined):
        return f"{wheel.name}: entry_points.txt lacks 'openace = cli:main'"
    return None


def main() -> int:
    wheels = sorted(Path("dist").glob("*.whl"))
    if not wheels:
        print("verify_wheel_entry: no wheels found under dist/", file=sys.stderr)
        return 1
    failures = [detail for wheel in wheels if (detail := verify_wheel(wheel))]
    if failures:
        for detail in failures:
            print(f"verify_wheel_entry: {detail}", file=sys.stderr)
        return 1
    print(
        f"verify_wheel_entry: {len(wheels)} wheel(s) ship "
        f"{EXPECTED_MODULE} + 'openace = cli:main'"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
