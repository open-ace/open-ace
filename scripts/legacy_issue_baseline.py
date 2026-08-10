#!/usr/bin/env python3
"""Legacy issue quarantine failure-baseline comparator.

A pure-stdlib library (identical local + CI) that:

* parses one or more pytest xUnit2 JUnit reports emitted by the legacy
  ``tests/issues`` shards;
* normalizes test identity to a stable ``(nodeid, outcome, category)`` key;
* merges any number of shard reports and validates completeness against an
  expected nodeid manifest (and the ``.test-baseline.json`` file floor);
* produces a bidirectional diff (known/new/resolved/changed/invalid) and a
  machine-readable + Markdown summary.

``compare`` is read-only and is the authoritative gate. ``snapshot`` writes a
reviewable candidate baseline to an explicit path. ``manifest`` emits the
expected nodeid set. See ``docs/TEST_LAYERS.md`` and
``docs/issue-2457-agent-handoff.md`` for the contract.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = PROJECT_ROOT / "ci" / "legacy-issue-failures.json"
TEST_BASELINE_FILE = PROJECT_ROOT / ".test-baseline.json"

SCHEMA_VERSION = 1
SCHEMA_NAME = "openace-legacy-issue-failures"
DEFAULT_SELECTION = "not postgres"

OUTCOMES = ("pass", "failure", "error", "skip")
# Coarse, stable categories. ``setup_error`` deliberately folds setup/fixture/
# teardown together so a phase flip cannot create a spurious ``changed``.
CATEGORIES = (
    "collection_error",
    "setup_error",
    "timeout",
    "assertion_failure",
    "test_body_exception",
    "infrastructure_error",
)


class BaselineError(RuntimeError):
    """A deterministic baseline configuration or comparison failure."""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def normalize_nodeid(nodeid: str, runner_root: str | None = None) -> str:
    """Return a repo-relative nodeid anchored to ``tests/issues/...``.

    Strips absolute runner roots, temp HOME prefixes, ports and whitespace.
    Parametrized ``[...]`` suffixes are identity-bearing and preserved.
    """
    n = (nodeid or "").strip()
    if runner_root and n.startswith(runner_root):
        n = n[len(runner_root) :].lstrip("/")
    m = re.search(r"(tests/issues/.*)$", n, re.S)
    if m:
        n = m.group(1)
    return n.strip()


@dataclasses.dataclass(frozen=True)
class ParsedTestcase:
    """A single testcase as parsed from JUnit, regardless of outcome."""

    nodeid: str
    outcome: str  # one of OUTCOMES
    category: str
    exception_type: str
    summary: str

    def as_failure(self) -> FailureRecord | None:
        if self.outcome in ("failure", "error"):
            return FailureRecord(
                nodeid=self.nodeid,
                issue_number=_issue_number_for(self.nodeid),
                outcome=self.outcome,
                category=self.category,
                exception_type=self.exception_type,
                summary=self.summary,
            )
        return None


@dataclasses.dataclass(frozen=True)
class FailureRecord:
    nodeid: str
    issue_number: str
    outcome: str  # "failure" | "error"
    category: str
    exception_type: str
    summary: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.nodeid, self.outcome, self.category)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


_ISSUE_RE = re.compile(r"tests/issues/(\d+)/")


def _issue_number_for(nodeid: str) -> str:
    m = _ISSUE_RE.search(nodeid)
    return m.group(1) if m else "0"


@dataclasses.dataclass
class Baseline:
    entries: list[FailureRecord]
    provenance: dict[str, Any] | None = None
    selection: str = DEFAULT_SELECTION
    version: int = SCHEMA_VERSION
    schema: str = SCHEMA_NAME

    def to_json(self) -> str:
        entries = sorted(
            (e.to_dict() for e in self.entries),
            key=lambda d: (d["nodeid"], d["outcome"], d["category"]),
        )
        obj: dict[str, Any] = {
            "version": self.version,
            "schema": self.schema,
            "provenance": dict(sorted((self.provenance or {}).items())),
            "selection": self.selection,
            "entries": entries,
        }
        return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Baseline:
        obj = json.loads(text)
        if obj.get("version") != SCHEMA_VERSION or obj.get("schema") != SCHEMA_NAME:
            raise BaselineError(
                f"Unsupported baseline schema: version={obj.get('version')!r} "
                f"schema={obj.get('schema')!r}"
            )
        keep = ("nodeid", "issue_number", "outcome", "category", "exception_type", "summary")
        entries = [FailureRecord(**{k: e[k] for k in keep}) for e in obj.get("entries", [])]
        return cls(
            entries=entries,
            provenance=obj.get("provenance") or {},
            selection=obj.get("selection", DEFAULT_SELECTION),
        )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _is_timeout(exception_type: str, message: str) -> bool:
    blob = f"{exception_type} {message}".lower()
    if "timeout" in exception_type.lower():
        return True
    return "timed out" in blob or "timeout" in blob


def classify(element: str, exception_type: str, message: str, nodeid: str) -> str:
    """Map a JUnit ``<failure>``/``<error>`` to a stable category.

    ``element`` is ``"failure"`` or ``"error"``. Collection errors are detected
    by the nodeid carrying no ``::`` (a module that failed to import never
    produces a test item, so its reconstructed identity is module-level). Do
    NOT key on the ``openace_nodeid`` property's absence: setup errors can also
    lack it when the autouse fixture did not get to run (e.g. an earlier
    fixture failed), and those must be baselined as ``setup_error``, not refused
    as collection errors.
    """
    if element == "failure":
        return "assertion_failure" if exception_type == "AssertionError" else "test_body_exception"
    # element == "error"
    if "::" not in nodeid:
        return "collection_error"
    if _is_timeout(exception_type, message):
        return "timeout"
    return "setup_error"


# ---------------------------------------------------------------------------
# JUnit parsing
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SuiteTotals:
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0


def _file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def reconstruct_nodeid(classname: str, name: str) -> str:
    """Best-effort nodeid reconstruction from xunit2 classname+name.

    xunit2 carries no ``file`` attribute. classname under importlib mode is a
    dotted ``dir.dir.file.Class`` path; the class boundary is the first segment
    matching ``Test*`` (``python_classes=Test*``). Collection errors have an
    empty classname and a dotted module path in ``name``.
    """
    classname = (classname or "").strip()
    if not classname:
        return (name or "").strip().replace(".", "/") + ".py"
    parts = classname.split(".")
    cls_idx = next((i for i, p in enumerate(parts) if p.startswith("Test")), None)
    if cls_idx is not None:
        module_parts, class_parts = parts[:cls_idx], parts[cls_idx:]
    else:
        module_parts, class_parts = parts, []
    module_path = "/".join(module_parts) + ".py"
    if class_parts:
        return f"{module_path}::{'::'.join(class_parts)}::{name}"
    return f"{module_path}::{name}"


def _short_summary(text: str, limit: int = 200) -> str:
    text = (text or "").strip()
    text = text.splitlines()[0] if text else ""
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


# Environment-specific noise that must not leak into the tracked baseline
# (handoff §4.2): absolute runner/workspace paths, ephemeral ports, temp HOME,
# runner-specific Python toolcache binaries (e.g. /opt/hostedtoolcache/...).
_RUNNER_PREFIX_RX = re.compile(r"/(?:[^/\s]+/)+open-ace/")
_PORT_RX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d+\b|\b(?:localhost|0\.0\.0\.0):\d+\b")
_TMPHOME_RX = re.compile(r"\.openace-ci-[A-Za-z0-9_-]+")
# Absolute python binaries: /opt/hostedtoolcache/Python/3.11.15/x64/bin/python3.11
_PYBIN_RX = re.compile(r"/[^\s\"]*/bin/python\d?(?:\.\d+)*")


def _scrub_env(text: str) -> str:
    text = _PYBIN_RX.sub("python", text or "")
    text = _RUNNER_PREFIX_RX.sub("", text or "")
    text = _TMPHOME_RX.sub(".openace-ci-<tmp>", text)
    text = _PORT_RX.sub("<host:port>", text)
    return text


# pytest xunit2 commonly omits the ``type`` attribute on <failure>/<error>, so
# recover the exception class from the stable message/text forms: a bare
# ``assert`` statement, or a leading ``<dotted.ExceptionClass>:`` / the
# traceback's ``E   <Class>:`` line.
_ASSERT_RX = re.compile(r"^\s*assert\b")
_EXC_CLASS_RX = re.compile(
    r"(?:^|\n\s*E\s+)([A-Za-z_][\w.]*(?:Error|Exception|Warning|Cancelled|NotFound))\s*:"
)


def _extract_exception_type(type_attr: str | None, message: str | None, text: str | None) -> str:
    if type_attr and type_attr.strip():
        return type_attr.strip()
    blob = f"{message or ''}\n{text or ''}"
    for line in blob.splitlines():
        if _ASSERT_RX.match(line):
            return "AssertionError"
    m = _EXC_CLASS_RX.search(blob)
    if m:
        return m.group(1).split(".")[-1]
    return ""


def _parse_testcase(tc: ET.Element) -> ParsedTestcase | None:
    classname = tc.get("classname", "")
    name = tc.get("name", "")
    prop = tc.find("properties/property[@name='openace_nodeid']")
    has_property = prop is not None and bool(prop.get("value"))
    raw_nodeid = prop.get("value") if has_property else reconstruct_nodeid(classname, name)
    nodeid = normalize_nodeid(raw_nodeid)
    if not nodeid:
        return None

    failure = tc.find("failure")
    error = tc.find("error")
    skipped = tc.find("skipped")
    if failure is not None:
        element = "failure"
        outcome = "failure"
        node = failure
    elif error is not None:
        element = "error"
        outcome = "error"
        node = error
    elif skipped is not None:
        return ParsedTestcase(
            nodeid, "skip", "skip", "", _short_summary(skipped.get("message", ""))
        )
    else:
        return ParsedTestcase(nodeid, "pass", "pass", "", "")

    exception_type = _extract_exception_type(node.get("type"), node.get("message"), node.text)
    message = _short_summary(_scrub_env(node.get("message") or (node.text or "")))
    category = classify(element, exception_type, message, nodeid)
    return ParsedTestcase(nodeid, outcome, category, exception_type, message)


def parse_junit(path: str | Path) -> tuple[list[ParsedTestcase], SuiteTotals]:
    """Parse one JUnit XML file into testcase records + aggregate totals."""
    try:
        tree = ET.parse(str(path))
    except (ET.ParseError, OSError) as exc:
        raise BaselineError(f"corrupt or unreadable JUnit XML: {path}: {exc}")
    root = tree.getroot()
    if root.tag == "testsuites":
        suites = root.findall("testsuite")
    elif root.tag == "testsuite":
        suites = [root]
    else:
        raise BaselineError(f"unknown JUnit root element <{root.tag}> in {path}")
    if not suites:
        raise BaselineError(f"no <testsuite> in JUnit XML: {path}")

    totals = SuiteTotals()
    records: list[ParsedTestcase] = []
    for suite in suites:
        a = suite.attrib
        totals.tests += int(a.get("tests", 0) or 0)
        totals.failures += int(a.get("failures", 0) or 0)
        totals.errors += int(a.get("errors", 0) or 0)
        totals.skipped += int(a.get("skipped", 0) or 0)
        for tc in suite.findall("testcase"):
            parsed = _parse_testcase(tc)
            if parsed is not None:
                records.append(parsed)  # do NOT dedupe; conflicts detected downstream
    if totals.tests == 0 and not records:
        raise BaselineError(f"zero tests in JUnit XML: {path}")
    return records, totals


# ---------------------------------------------------------------------------
# Expected-nodeid manifest
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ExpectedManifest:
    nodeids: list[str]
    files: list[str]
    selection: str
    targeted: bool

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def build_expected_manifest(
    targets: Iterable[str] = ("tests/issues",),
    selection: str = DEFAULT_SELECTION,
    targeted: bool = False,
) -> ExpectedManifest:
    """Collect the expected nodeid set with ``-m "<selection>"`` semantics.

    Uses ``--collect-only -qq`` (pytest 9 ``-q`` emits a tree view with no
    parseable nodeids; ``-qq`` emits flat ``path::nodeid`` lines). Only lines
    that start with ``tests/`` and contain ``::`` are kept, which drops the
    warnings-summary source references and the ``N tests collected`` footer.
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "--collect-only",
        "-qq",
        "-m",
        selection,
    ]
    proc = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    nodeids = sorted(
        {
            line.strip()
            for line in proc.stdout.splitlines()
            if line.startswith("tests/") and "::" in line
        }
    )
    if proc.returncode != 0:
        # Partial/degraded collection (import error, usage error, etc.). Even
        # with some nodeids, files that failed to import silently drop out of
        # the expected set, so the completeness check would be unsound. Fail
        # closed rather than trust a partial manifest.
        raise BaselineError(
            f"expected-nodeid manifest collection exited {proc.returncode} "
            f"({len(nodeids)} nodeids parsed); collection is degraded — "
            "resolve collection errors before comparing/snapshotting"
        )
    files = sorted({_file_of(n) for n in nodeids})
    return ExpectedManifest(nodeids=nodeids, files=files, selection=selection, targeted=targeted)


# ---------------------------------------------------------------------------
# Quarantine — tracked, gate-visible exclusions for nodeids that cannot run
# ---------------------------------------------------------------------------

QUARANTINE_FILE = PROJECT_ROOT / "ci" / "legacy-issue-quarantine.json"
_QUARANTINE_REQUIRED = (
    "nodeid",
    "reason",
    "owner",
    "tracking_issue",
    "exit_condition",
    "expires_on",
    "expected_probe_outcome",
)
# Valid machine-readable probe outcomes an entry may declare. Only ``timeout``
# is permitted: it is the only outcome that cannot mask a regression — a timeout
# entry is green only while it genuinely still times out. ``pass`` is forbidden
# because it would let a *recovered* test stay permanently deselected and the
# weekly probe green; ``fail`` is forbidden because it is too coarse (pytest
# rc 2-5 / no-tests / usage errors would all read as "expected fail"). If a
# future debt type needs a non-timeout outcome, it must declare a precise,
# machine-readable exit/failure fingerprint rather than reuse this enum.
PROBE_OUTCOMES = ("timeout",)


@dataclasses.dataclass(frozen=True)
class QuarantineEntry:
    nodeid: str
    reason: str
    owner: str
    tracking_issue: str
    exit_condition: str
    expires_on: str
    expected_probe_outcome: str


def load_quarantine(path: str | Path) -> list[QuarantineEntry]:
    text = Path(path).read_text()
    obj = json.loads(text)
    if obj.get("schema") != "openace-legacy-issue-quarantine":
        raise BaselineError(f"unsupported quarantine schema: {obj.get('schema')!r}")
    if obj.get("version") != 1:
        raise BaselineError(f"unsupported quarantine version: {obj.get('version')!r}")
    raw_entries = obj.get("entries")
    if not isinstance(raw_entries, list):
        raise BaselineError(f"quarantine entries must be a list, got {type(raw_entries).__name__}")
    entries: list[QuarantineEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise BaselineError(f"quarantine entry must be an object, got {type(raw).__name__}")
        entries.append(QuarantineEntry(**{k: raw.get(k, "") for k in _QUARANTINE_REQUIRED}))
    return entries


def _is_real_date(value: str) -> bool:
    try:
        datetime.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def validate_quarantine(
    entries: list[QuarantineEntry],
    collectable_nodeids: Iterable[str],
    today: str,
) -> list[str]:
    """Fail-closed validation of the quarantine list.

    Returns invalid reasons for: missing/empty required fields, malformed
    ``expires_on``, an expired entry, a duplicate nodeid, or an entry whose
    nodeid is no longer collectable (stale after deletion/rename). Empty list
    means valid.
    """
    collectable = set(collectable_nodeids)
    seen: set[str] = set()
    invalid: list[str] = []
    for e in entries:
        for field in _QUARANTINE_REQUIRED:
            if not getattr(e, field).strip():
                invalid.append(f"quarantine entry {e.nodeid!r} missing field '{field}'")
        if e.expected_probe_outcome and e.expected_probe_outcome not in PROBE_OUTCOMES:
            invalid.append(
                f"quarantine entry {e.nodeid!r} invalid expected_probe_outcome "
                f"{e.expected_probe_outcome!r} (choose from {PROBE_OUTCOMES})"
            )
        if not _is_real_date(e.expires_on):
            invalid.append(f"quarantine entry {e.nodeid!r} malformed expires_on {e.expires_on!r}")
        elif e.expires_on < today:
            invalid.append(f"quarantine entry {e.nodeid!r} expired on {e.expires_on}")
        if e.nodeid in seen:
            invalid.append(f"quarantine entry {e.nodeid!r} duplicated")
        seen.add(e.nodeid)
        if collectable and e.nodeid not in collectable:
            invalid.append(
                f"quarantine entry {e.nodeid!r} is no longer collectable "
                "(remove it or update its nodeid)"
            )
    return invalid


def _load_and_validate_quarantine(
    path: str | Path, collectable_nodeids: Iterable[str]
) -> dict[str, Any]:
    """Load the quarantine list and validate it; return entries-as-dicts + invalids.

    A missing file means no quarantine (empty). Validation failures (expired,
    uncollectable, duplicate, missing fields) are returned as invalid reasons
    so the caller can fail closed.
    """
    if not Path(path).exists():
        return {"entries": [], "invalid": []}
    entries = load_quarantine(path)
    today = datetime.date.today().isoformat()
    invalid = validate_quarantine(entries, collectable_nodeids, today)
    as_dicts = [
        {
            "nodeid": e.nodeid,
            "reason": e.reason,
            "owner": e.owner,
            "tracking_issue": e.tracking_issue,
            "exit_condition": e.exit_condition,
            "expires_on": e.expires_on,
        }
        for e in entries
    ]
    return {"entries": as_dicts, "invalid": invalid}


# ---------------------------------------------------------------------------
# Merge, completeness, bidirectional diff
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CompareResult:
    exit_code: int
    known: list[dict[str, Any]]
    new: list[dict[str, Any]]
    resolved: list[str]
    changed: list[dict[str, Any]]
    invalid: list[str]
    collection_errors: list[dict[str, Any]]
    quarantined: list[dict[str, Any]]
    observed_files: int
    expected_total: int
    targeted: bool

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _failure_index(records: list[FailureRecord]) -> dict[tuple[str, str, str], FailureRecord]:
    """Index failures by key; raise on any conflict.

    Two failure modes are rejected loudly (never last-write-wins): same key with
    a different summary, and the same nodeid mapping to more than one key (e.g.
    a hand-edited baseline carrying both ``failure`` and ``error`` for one node).
    """
    index: dict[tuple[str, str, str], FailureRecord] = {}
    nodeid_keys: dict[str, set[tuple[str, str, str]]] = {}
    for r in records:
        existing = index.get(r.key)
        if existing is not None and (
            existing.exception_type != r.exception_type or existing.summary != r.summary
        ):
            raise BaselineError(
                f"conflicting results for {r.nodeid} "
                f"({existing.exception_type}/{existing.summary!r} vs "
                f"{r.exception_type}/{r.summary!r}); refusing last-write-wins"
            )
        index[r.key] = r
        nodeid_keys.setdefault(r.nodeid, set()).add(r.key)
    for nodeid, keys in nodeid_keys.items():
        if len(keys) > 1:
            raise BaselineError(
                f"nodeid {nodeid} has multiple failure keys {sorted(keys)}; "
                f"a test must have exactly one (outcome, category)"
            )
    return index


def compare(
    baseline: Baseline,
    parsed_testcases: list[ParsedTestcase],
    expected_nodeids: Iterable[str],
    *,
    baseline_min_files: int = 0,
    require_review_threshold_pct: float = 0.0,
    observed_files: set[str] | None = None,
    targeted: bool = False,
    exit_codes: dict[str, int] | None = None,
    expected_shard_count: int = 0,
    quarantined: list[dict[str, Any]] | None = None,
) -> CompareResult:
    """Compute the bidirectional diff and fail-closed completeness verdict."""
    quarantined = quarantined or []
    current_nodeids = {tc.nodeid for tc in parsed_testcases}
    if observed_files is None:
        observed_files = {_file_of(n) for n in current_nodeids}

    current_failures = [tc.as_failure() for tc in parsed_testcases]
    current_failures = [f for f in current_failures if f is not None]
    try:
        current_index = _failure_index(current_failures)
    except BaselineError as exc:
        return CompareResult(
            1,
            [],
            [],
            [],
            [],
            [str(exc)],
            [],
            quarantined,
            len(observed_files),
            len(list(expected_nodeids)),
            targeted,
        )
    baseline_index = _failure_index(baseline.entries)

    current_keys = set(current_index)
    baseline_keys = set(baseline_index)
    current_failure_nodeids = {k[0] for k in current_keys}
    baseline_failure_nodeids = {k[0] for k in baseline_keys}

    known = sorted(
        (current_index[k].to_dict() for k in (current_keys & baseline_keys)),
        key=lambda d: (d["nodeid"], d["outcome"], d["category"]),
    )
    new = sorted(
        (
            current_index[k].to_dict()
            for k in (current_keys - baseline_keys)
            if k[0] not in baseline_failure_nodeids
        ),
        key=lambda d: (d["nodeid"], d["outcome"], d["category"]),
    )
    changed: list[dict[str, Any]] = []
    for n in sorted(current_failure_nodeids & baseline_failure_nodeids):
        cur = next(k for k in current_keys if k[0] == n)
        base = next(k for k in baseline_keys if k[0] == n)
        if cur != base:
            changed.append({"nodeid": n, "baseline": list(base), "current": list(cur)})

    resolved = sorted(baseline_failure_nodeids - current_failure_nodeids)
    collection_errors = sorted(
        (f.to_dict() for f in current_failures if f.category == "collection_error"),
        key=lambda d: (d["nodeid"], d["outcome"], d["category"]),
    )

    expected_set = set(expected_nodeids)
    invalid = verify_completeness(
        parsed_testcases,
        expected_nodeids,
        min_files=baseline_min_files,
        require_review_threshold_pct=require_review_threshold_pct,
        exit_codes=exit_codes,
        expected_shard_count=expected_shard_count,
    )
    if targeted:
        invalid.append(
            "targeted run: compared against selected subset only, not a full nightly gate"
        )

    exit_code = (
        0
        if (not new and not changed and not resolved and not collection_errors and not invalid)
        else 1
    )

    return CompareResult(
        exit_code=exit_code,
        known=known,
        new=new,
        resolved=resolved,
        changed=changed,
        invalid=invalid,
        collection_errors=collection_errors,
        quarantined=quarantined,
        observed_files=len(observed_files),
        expected_total=len(expected_set),
        targeted=targeted,
    )


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def _issue_dir(nodeid: str) -> str:
    m = _ISSUE_RE.search(nodeid)
    return f"tests/issues/{m.group(1)}" if m else "(unknown)"


def _category_of_failure(d: dict[str, Any]) -> str:
    return d.get("category", "")


def render_markdown(result: CompareResult) -> str:
    from collections import Counter

    known_by_issue = Counter(_issue_dir(d["nodeid"]) for d in result.known)
    known_by_cat = Counter(_category_of_failure(d) for d in result.known)
    lines = [
        "## Legacy issue failure baseline",
        "",
        f"- exit code: **{result.exit_code}**",
        f"- known debt: {len(result.known)}  | new: {len(result.new)} | "
        f"resolved: {len(result.resolved)} | changed: {len(result.changed)} | "
        f"collection errors: {len(result.collection_errors)} | invalid: {len(result.invalid)}",
        f"- observed files: {result.observed_files} / expected nodeids: {result.expected_total}"
        + ("  *(targeted run — not a full nightly gate)*" if result.targeted else ""),
        "",
    ]
    if result.known:
        lines += [
            "### Known debt — top issue directories",
            "",
            *(f"- `{d}`: {n}" for d, n in known_by_issue.most_common(10)),
            "",
        ]
        lines += [
            "### Known debt — top categories",
            "",
            *(f"- `{c}`: {n}" for c, n in known_by_cat.most_common(10)),
            "",
        ]
    if result.quarantined:
        lines += [
            f"### Quarantined debt ({len(result.quarantined)} — excluded from execution, tracked)",
            "",
            *(
                f"- `{e['nodeid']}` — owner `{e['owner']}`, expires `{e['expires_on']}` — {e['reason']} (exit: {e['exit_condition']}; {e['tracking_issue']})"
                for e in result.quarantined
            ),
            "",
        ]
    for label, items, fmt in (
        (
            "New failures",
            result.new,
            lambda d: f"- `{d['nodeid']}` [{d['category']}] {d['summary']}",
        ),
        (
            "Resolved (remove from baseline)",
            [{"nodeid": n} for n in result.resolved],
            lambda d: f"- `{d['nodeid']}`",
        ),
        (
            "Changed",
            result.changed,
            lambda d: f"- `{d['nodeid']}`: {d['baseline']} -> {d['current']}",
        ),
        (
            "Collection errors (never baselined)",
            result.collection_errors,
            lambda d: f"- `{d['nodeid']}` {d['summary']}",
        ),
        ("Invalid / incomplete", [{"m": m} for m in result.invalid], lambda d: f"- {d['m']}"),
    ):
        if items:
            lines += [f"### {label}", "", *(fmt(i) for i in items[:50]), ""]
    return "\n".join(lines)


def _load_junit_glob(pattern: str) -> list[ParsedTestcase]:
    paths = sorted(glob.glob(pattern, recursive=True))
    if not paths:
        raise BaselineError(
            f"no JUnit reports matched '{pattern}'; expected shard artifacts missing "
            f"— run `snapshot` with a complete run to generate a candidate baseline"
        )
    merged: list[ParsedTestcase] = []
    for p in paths:
        testcases, _totals = parse_junit(p)
        merged.extend(testcases)  # do NOT dedupe across shards; conflicts detected in compare()
    return merged


def _load_test_baseline(path: Path) -> tuple[int, float]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(f"cannot read test baseline {path}: {exc}")
    issues = data.get("layers", {}).get("issues", {})
    return int(issues.get("min_files", 0) or 0), float(
        data.get("tolerance", {}).get("require_review_threshold", 0) or 0
    )


def _write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def _ensure_parent(path: str | Path) -> None:
    """Create the parent directory of an output path (the comparator job runs
    in a fresh checkout where ``test-results/`` does not exist yet)."""
    parent = Path(path).parent
    if str(parent) and parent != Path("."):
        parent.mkdir(parents=True, exist_ok=True)


# Canonical CI artifact-relative locations for snapshot inputs. The
# extended-tests.yml ``legacy-issue-baseline`` job downloads the ``issue-tests-*``
# shard artifacts (each carrying ``test-results/issues-N.xml`` and
# ``issues-N.exit-code``) into ``artifacts/``, so a reproducible snapshot reads
# them from here regardless of where the command is invoked.
_JUNIT_GLOB_CANONICAL = "artifacts/test-results/issues-*.xml"
_EXITCODE_GLOB_CANONICAL = "artifacts/test-results/issues-*.exit-code"


def _repo_relative(path: str) -> str:
    """Normalize a snapshot path to a stable repo-relative POSIX form."""
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def _snapshot_generated_command(args: argparse.Namespace) -> str:
    """Build a path-normalized, parameter-complete, executable snapshot command.

    JUnit/exit-code globs are emitted in their canonical CI artifact-relative
    form; single-file destinations are repo-relative. Every flag the run actually
    used is recorded (including defaults) so copying the command reproduces the
    baseline from any run's downloaded ``issue-tests-*`` artifacts. The result
    MUST round-trip through :func:`build_parser` (``--output`` present, all flags
    valid) — see ``test_baseline_provenance_round_trips``.
    """
    parts = [
        "python",
        "scripts/legacy_issue_baseline.py",
        "snapshot",
        "--junit",
        _JUNIT_GLOB_CANONICAL,
        "--output",
        _repo_relative(args.output),
    ]
    if args.exit_code_glob:
        parts += ["--exit-code-glob", _EXITCODE_GLOB_CANONICAL]
    parts += ["--shard-count", str(args.shard_count)]
    for flag, key in (
        ("--manifest", "manifest"),
        ("--quarantine", "quarantine"),
        ("--test-baseline", "test_baseline"),
    ):
        value = getattr(args, key, "") or ""
        if value:
            parts += [flag, _repo_relative(value)]
    if args.source_run:
        parts += ["--source-run", args.source_run]
    if args.source_run_url:
        parts += ["--source-run-url", args.source_run_url]
    if args.reference_commit:
        parts += ["--reference-commit", args.reference_commit]
    if args.run_contract:
        parts += ["--run-contract", args.run_contract]
    return shlex.join(parts)


def _load_exit_codes(pattern: str) -> dict[str, int]:
    """Load shard pytest exit-code files (``issues-N.exit-code`` → int).

    Absent files are reported by the caller via the expected-shard count; a
    present-but-unparseable file OR a duplicate basename (two paths writing the
    same ``issues-N.exit-code``) is a hard error — never last-write-wins.
    """
    codes: dict[str, int] = {}
    for p in sorted(glob.glob(pattern, recursive=True)):
        name = Path(p).name
        if name in codes:
            raise BaselineError(f"duplicate exit-code basename {name!r} ({p})")
        raw = Path(p).read_text().strip()
        try:
            codes[name] = int(raw)
        except ValueError as exc:
            raise BaselineError(f"unparseable exit-code file {p}: {raw!r}") from exc
    return codes


def verify_completeness(
    parsed: list[ParsedTestcase],
    expected_nodeids: Iterable[str],
    *,
    min_files: int = 0,
    require_review_threshold_pct: float = 0.0,
    exit_codes: dict[str, int] | None = None,
    expected_shard_count: int = 0,
) -> list[str]:
    """Canonical fail-closed completeness checks shared by compare + snapshot.

    Returns a list of human-readable invalid reasons (empty == complete).
    """
    seen: dict[str, ParsedTestcase] = {}
    duplicates: list[str] = []
    for tc in parsed:
        if tc.nodeid in seen:
            duplicates.append(tc.nodeid)
        seen[tc.nodeid] = tc
    observed_nodeids = set(seen)
    observed_files = {_file_of(n) for n in observed_nodeids}
    expected_set = set(expected_nodeids)

    invalid: list[str] = []
    for n in sorted(expected_set - observed_nodeids):
        invalid.append(f"expected nodeid never observed: {n}")
    # Bidirectional: an unexpected observed nodeid (e.g. a quarantined nodeid
    # that ran anyway, or a stale artifact bundle) must also fail closed.
    for n in sorted(observed_nodeids - expected_set):
        invalid.append(f"unexpected observed nodeid (not in expected set): {n}")
    if not parsed and expected_set:
        invalid.append("no JUnit reports matched the glob; expected shard artifacts missing")
    for n in sorted(set(duplicates))[:50]:
        invalid.append(f"duplicate nodeid result (cross-shard/rerun conflict): {n}")
    invalid.extend(_check_exit_codes(exit_codes, expected_shard_count))
    floor = min_files * (1 - require_review_threshold_pct / 100.0)
    if min_files and len(observed_files) < floor:
        invalid.append(
            f"observed {len(observed_files)} files, below floor {floor:.0f} "
            f"({min_files} × {100 - require_review_threshold_pct:.0f}%)"
        )
    return invalid


def _check_exit_codes(exit_codes: dict[str, int] | None, expected_shard_count: int) -> list[str]:
    """Validate shard exit-code cardinality + values.

    With ``expected_shard_count`` set, require EXACTLY the N expected files
    (``issues-{1..N}.exit-code``): missing/extra/duplicate/wrong-name or any
    unparseable value fails closed. Without it, only non-{0,1} values fail.
    """
    invalid: list[str] = []
    if expected_shard_count <= 0:
        if exit_codes:
            for name, code in sorted(exit_codes.items()):
                if code not in (0, 1):
                    invalid.append(
                        f"shard {name} exited {code} (infrastructure failure, not a test failure)"
                    )
        return invalid
    expected_names = {f"issues-{i}.exit-code" for i in range(1, expected_shard_count + 1)}
    present = set(exit_codes or {})
    for name in sorted(expected_names - present):
        invalid.append(f"missing shard exit-code file: {name}")
    for name in sorted(present - expected_names):
        invalid.append(f"unexpected shard exit-code file: {name}")
    for name in sorted(expected_names & present):
        code = (exit_codes or {})[name]
        if code not in (0, 1):
            invalid.append(
                f"shard {name} exited {code} (infrastructure failure, not a test failure)"
            )
    return invalid


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_compare(args: argparse.Namespace) -> int:
    try:
        baseline = Baseline.from_json(Path(args.baseline).read_text())
    except OSError as exc:
        raise BaselineError(
            f"cannot read baseline {args.baseline}: {exc} "
            f"— generate it with `snapshot` from a complete reference run"
        ) from exc
    parsed = _load_junit_glob(args.junit)
    if args.manifest:
        manifest = json.loads(Path(args.manifest).read_text())
        expected = manifest.get("nodeids", [])
        targeted = bool(manifest.get("targeted"))
    else:
        em = build_expected_manifest(targeted=bool(args.targeted_issues))
        expected, targeted = em.nodeids, em.targeted
    # Quarantine: tracked exclusions (must be collectable + non-expired). The
    # quarantined nodeids are removed from the expected-executed set (so the
    # shard's deselect + the manifest stay consistent) and reported as debt.
    quarantine = _load_and_validate_quarantine(args.quarantine, expected)
    quarantined_ids = {e["nodeid"] for e in quarantine["entries"]}
    expected = [n for n in expected if n not in quarantined_ids]
    min_files, thr = _load_test_baseline(Path(args.test_baseline))
    observed = {_file_of(tc.nodeid) for tc in parsed}
    exit_codes = _load_exit_codes(args.exit_code_glob) if args.exit_code_glob else None
    result = compare(
        baseline,
        parsed,
        expected,
        baseline_min_files=min_files,
        require_review_threshold_pct=thr,
        observed_files=observed,
        targeted=targeted,
        exit_codes=exit_codes,
        expected_shard_count=args.shard_count,
        quarantined=quarantine["entries"],
    )
    # quarantine validation problems fail closed
    result.invalid.extend(quarantine["invalid"])
    if quarantine["invalid"]:
        result.exit_code = 1
    if args.json_output:
        _ensure_parent(args.json_output)
        Path(args.json_output).write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
        )
    md = render_markdown(result)
    if args.markdown_output:
        _ensure_parent(args.markdown_output)
        Path(args.markdown_output).write_text(md + "\n")
    _write_summary(md)
    if result.exit_code:
        head = (
            "new failures: " + ", ".join(d["nodeid"] for d in result.new[:5])
            if result.new
            else (
                result.invalid[0]
                if result.invalid
                else "baseline drift detected (resolved/changed/collection error)"
            )
        )
        print(f"BASELINE FAIL: {head}", file=sys.stderr)
    else:
        print(f"BASELINE OK: {len(result.known)} known, 0 new, 0 resolved")
    return result.exit_code


def _cmd_snapshot(args: argparse.Namespace) -> int:
    parsed = _load_junit_glob(args.junit)
    failures: list[FailureRecord] = []
    collection_errors: list[ParsedTestcase] = []
    for tc in parsed:
        f = tc.as_failure()
        if f is None:
            continue
        if f.category == "collection_error":
            collection_errors.append(tc)
        else:
            failures.append(f)
    if collection_errors:
        print(
            "snapshot refused: collection errors cannot be baselined (the "
            "collection gate must stay at zero). Offenders:\n  "
            + "\n  ".join(tc.nodeid for tc in collection_errors[:50]),
            file=sys.stderr,
        )
        return 2
    # P0#2: a baseline may only come from a COMPLETE reference run. Refuse to
    # snapshot a partial bundle (missing nodeids, duplicate/conflicting results,
    # infrastructure exit codes, file-count regression).
    if args.manifest:
        expected = json.loads(Path(args.manifest).read_text()).get("nodeids", [])
    else:
        expected = build_expected_manifest().nodeids
    # Quarantine entries are excluded from the expected-executed set (same as
    # compare) and validated; an invalid/expired/stale quarantine fails closed.
    quarantine = _load_and_validate_quarantine(args.quarantine, expected)
    quarantined_ids = {e["nodeid"] for e in quarantine["entries"]}
    expected = [n for n in expected if n not in quarantined_ids]
    min_files, thr = (
        _load_test_baseline(Path(args.test_baseline)) if args.test_baseline else (0, 0.0)
    )
    exit_codes = _load_exit_codes(args.exit_code_glob) if args.exit_code_glob else None
    incomplete = verify_completeness(
        parsed,
        expected,
        min_files=min_files,
        require_review_threshold_pct=thr,
        exit_codes=exit_codes,
        expected_shard_count=args.shard_count,
    )
    incomplete.extend(quarantine["invalid"])
    if incomplete:
        print(
            "snapshot refused: reference run is not complete / quarantine invalid "
            "(baseline may only come from a complete run). Reasons:\n  - "
            + "\n  - ".join(incomplete[:30]),
            file=sys.stderr,
        )
        return 2
    provenance = {
        "reference_commit": args.reference_commit or "",
        "source_run": args.source_run or "",
        "source_run_url": args.source_run_url or "",
        "run_contract": args.run_contract or "",
        # Reproducible, repo/artifact-relative, parameter-complete command that
        # round-trips through build_parser (see _snapshot_generated_command).
        "generated_command": _snapshot_generated_command(args),
    }
    baseline = Baseline(entries=failures, provenance=provenance, selection=DEFAULT_SELECTION)
    _ensure_parent(args.output)
    Path(args.output).write_text(baseline.to_json())
    print(f"snapshot: {len(failures)} entries -> {args.output}")
    return 0


def _cmd_manifest(args: argparse.Namespace) -> int:
    em = build_expected_manifest()
    _ensure_parent(args.output)
    Path(args.output).write_text(json.dumps(em.to_dict(), indent=2, sort_keys=True) + "\n")
    print(f"manifest: {len(em.nodeids)} nodeids / {len(em.files)} files -> {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    cmp = sub.add_parser("compare", help="read-only compare; authoritative gate")
    cmp.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    cmp.add_argument("--junit", required=True)
    cmp.add_argument("--manifest", default="")
    cmp.add_argument("--test-baseline", default=str(TEST_BASELINE_FILE))
    cmp.add_argument("--exit-code-glob", default="")
    cmp.add_argument("--quarantine", default=str(QUARANTINE_FILE))
    cmp.add_argument("--shard-count", type=int, default=4)
    cmp.add_argument("--json-output", default="")
    cmp.add_argument("--markdown-output", default="")
    cmp.add_argument("--targeted-issues", action="store_true")
    cmp.set_defaults(func=_cmd_compare)

    snap = sub.add_parser("snapshot", help="generate a reviewable candidate baseline")
    snap.add_argument("--junit", required=True)
    snap.add_argument("--output", required=True)
    snap.add_argument("--manifest", default="")
    snap.add_argument("--test-baseline", default=str(TEST_BASELINE_FILE))
    snap.add_argument("--exit-code-glob", default="")
    snap.add_argument("--quarantine", default=str(QUARANTINE_FILE))
    snap.add_argument("--shard-count", type=int, default=4)
    snap.add_argument("--source-run", default="")
    snap.add_argument("--source-run-url", default="")
    snap.add_argument("--reference-commit", default="")
    snap.add_argument("--run-contract", default="")
    snap.set_defaults(func=_cmd_snapshot)

    man = sub.add_parser("manifest", help="emit the expected nodeid set")
    man.add_argument("--output", required=True)
    man.set_defaults(func=_cmd_manifest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BaselineError as exc:
        print(f"BASELINE ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
