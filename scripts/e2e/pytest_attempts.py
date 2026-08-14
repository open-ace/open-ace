"""pytest attempt-recording plugin (Issue #2491): every attempt, every phase.

The P0 probe (docs/dev-notes/2491-rerunfailures-junit-probe.md) proved the
JUnit report keeps only the final outcome under ``--reruns``: a test that
passes on rerun appears as a clean pass and the rerun signal lives only in
stdout. This plugin is therefore the authoritative per-attempt record: it
appends one JSONL line per phase report (setup/call/teardown, first attempt
and every retry) without changing execution semantics. Appending (not
buffering) keeps the record alive if the lane is killed.

Usage::

    pytest -p e2e_attempts --e2e-attempts=test-results/e2e-attempts.jsonl ...
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

_EXC_RE = re.compile(r"^E\s+(\w+(?:Error|Exception|Failure|Interrupt)?):\s*(.*)$", re.M)


def _exception_from_longrepr(longrepr: str | None) -> tuple[str | None, str | None]:
    """Best-effort exception class/message extraction from a report longrepr."""
    if not longrepr:
        return None, None
    match = _EXC_RE.search(longrepr)
    if match:
        return match.group(1), match.group(2)
    first = longrepr.strip().splitlines()
    return None, first[0][:200] if first else None


def pytest_addoption(parser) -> None:
    group = parser.getgroup("e2e-attempts", "E2E attempt envelope recording (#2491)")
    group.addoption(
        "--e2e-attempts",
        action="store",
        default="",
        help="Path of the attempt JSONL envelope to append to.",
    )


def pytest_configure(config) -> None:
    path = config.getoption("--e2e-attempts")
    _configure_sink(Path(path) if path else None)


def _configure_sink(path: Path | None) -> None:
    """(Re)set the module-level append sink; tests reset state between runs."""
    global _ATTEMPTS_PATH
    _ATTEMPTS_PATH = path
    _ATTEMPT_INDEX.clear()
    if _ATTEMPTS_PATH:
        _ATTEMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)


_ATTEMPTS_PATH: Path | None = None
_ATTEMPT_INDEX: dict[str, int] = {}


def pytest_runtest_logreport(report) -> None:  # noqa: D401 - pytest hook
    if _ATTEMPTS_PATH is None:
        return
    nodeid = report.nodeid
    if report.when == "setup":
        # a new setup report starts a new attempt (rerunfailures re-runs setup)
        _ATTEMPT_INDEX[nodeid] = _ATTEMPT_INDEX.get(nodeid, 0) + 1
    attempt = _ATTEMPT_INDEX.get(nodeid, 1)
    longrepr = None
    if report.longrepr:
        try:
            longrepr = str(report.longrepr)
        except Exception:  # pragma: no cover - defensive
            longrepr = None
    exc_class, message = (None, None)
    if report.failed:
        exc_class, message = _exception_from_longrepr(longrepr)
    record = {
        "nodeid": nodeid,
        "attempt": attempt,
        "phase": report.when,
        "outcome": report.outcome,
        "duration_seconds": round(report.duration, 3),
        "monotonic": round(time.monotonic(), 3),
        "exception_class": exc_class,
        "message": message,
        "longrepr_head": (longrepr or "").strip().splitlines()[:1][0][:200] if longrepr else None,
    }
    with _ATTEMPTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def summarize_attempts(lines: list[str]) -> dict[str, dict[str, object]]:
    """Fold raw JSONL lines into per-nodeid attempt summaries.

    The final attempt's call phase is the authoritative final outcome
    (pytest-rerunfailures semantics); first attempt and attempt count are
    preserved so flaky signal survives in the artifact, not just in logs.
    """
    per_node: dict[str, list[dict]] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        per_node.setdefault(record["nodeid"], []).append(record)
    summary: dict[str, dict[str, object]] = {}
    for nodeid, records in per_node.items():
        attempts = sorted({r["attempt"] for r in records})
        final = records[-1]
        # last call-phase outcome decides; fall back to the last record
        call_records = [r for r in records if r["phase"] == "call"]
        decision = call_records[-1] if call_records else final
        first = next((r for r in records if r["phase"] == "call"), {"outcome": final["outcome"]})
        failed = [r for r in records if r["outcome"] != "passed"]
        summary[nodeid] = {
            "final_outcome": "pass" if decision["outcome"] == "passed" else "fail",
            "first_outcome": "pass" if first["outcome"] == "passed" else "fail",
            "attempts": len(attempts),
            "exception_class": failed[-1].get("exception_class") if failed else None,
            "message": failed[-1].get("message") if failed else None,
        }
    return summary
