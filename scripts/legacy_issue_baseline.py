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
import fnmatch
import glob
import json
import os
import re
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


def classify(
    element: str, has_nodeid_property: bool, exception_type: str, message: str, nodeid: str
) -> str:
    """Map a JUnit ``<failure>``/``<error>`` to a stable category.

    ``element`` is ``"failure"`` or ``"error"``. Collection errors are detected
    by the absence of the ``openace_nodeid`` property (a collection failure
    never creates a test item, so the autouse conftest fixture never ran).
    """
    if element == "failure":
        return "assertion_failure" if exception_type == "AssertionError" else "test_body_exception"
    # element == "error"
    if not has_nodeid_property or "::" not in nodeid:
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

    exception_type = (node.get("type") or "").strip()
    message = _short_summary(node.get("message") or (node.text or ""))
    category = classify(element, has_property, exception_type, message, nodeid)
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
    by_nodeid: dict[str, ParsedTestcase] = {}
    for suite in suites:
        a = suite.attrib
        totals.tests += int(a.get("tests", 0) or 0)
        totals.failures += int(a.get("failures", 0) or 0)
        totals.errors += int(a.get("errors", 0) or 0)
        totals.skipped += int(a.get("skipped", 0) or 0)
        for tc in suite.findall("testcase"):
            parsed = _parse_testcase(tc)
            if parsed is not None:
                by_nodeid[parsed.nodeid] = parsed  # last document order wins (rerun-safe)
    if totals.tests == 0 and not by_nodeid:
        raise BaselineError(f"zero tests in JUnit XML: {path}")
    return list(by_nodeid.values()), totals


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
    files = sorted({_file_of(n) for n in nodeids})
    return ExpectedManifest(nodeids=nodeids, files=files, selection=selection, targeted=targeted)


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
    observed_files: int
    expected_total: int
    targeted: bool

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _failure_index(records: list[FailureRecord]) -> dict[tuple[str, str, str], FailureRecord]:
    """Index failures by key; raise on conflicting same-key records."""
    index: dict[tuple[str, str, str], FailureRecord] = {}
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
) -> CompareResult:
    """Compute the bidirectional diff and fail-closed completeness verdict."""
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

    invalid: list[str] = []
    expected_set = set(expected_nodeids)
    missing = sorted(expected_set - current_nodeids)
    for n in missing:
        invalid.append(f"expected nodeid never observed: {n}")
    if not parsed_testcases and expected_set:
        invalid.append("no JUnit reports matched the glob; expected shard artifacts missing")
    file_floor = baseline_min_files * (1 - require_review_threshold_pct / 100.0)
    if baseline_min_files and len(observed_files) < file_floor:
        invalid.append(
            f"observed {len(observed_files)} files, below floor "
            f"{file_floor:.0f} ({baseline_min_files} × "
            f"{100 - require_review_threshold_pct:.0f}%)"
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
    merged: dict[str, ParsedTestcase] = {}
    for p in paths:
        testcases, _totals = parse_junit(p)
        for tc in testcases:
            merged[tc.nodeid] = tc  # shards are file-disjoint; last-write is safe
    return list(merged.values())


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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_compare(args: argparse.Namespace) -> int:
    baseline = Baseline.from_json(Path(args.baseline).read_text())
    parsed = _load_junit_glob(args.junit)
    if args.manifest:
        manifest = json.loads(Path(args.manifest).read_text())
        expected = manifest.get("nodeids", [])
        targeted = bool(manifest.get("targeted"))
    else:
        em = build_expected_manifest(targeted=bool(args.targeted_issues))
        expected, targeted = em.nodeids, em.targeted
    min_files, thr = _load_test_baseline(Path(args.test_baseline))
    observed = {_file_of(tc.nodeid) for tc in parsed}
    result = compare(
        baseline,
        parsed,
        expected,
        baseline_min_files=min_files,
        require_review_threshold_pct=thr,
        observed_files=observed,
        targeted=targeted,
    )
    if args.json_output:
        Path(args.json_output).write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
        )
    md = render_markdown(result)
    if args.markdown_output:
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
    provenance = {
        "reference_commit": args.reference_commit or "",
        "source_run": args.source_run or "",
        "source_run_url": args.source_run_url or "",
        "run_contract": args.run_contract or "",
        "generated_command": (
            f"python scripts/legacy_issue_baseline.py snapshot "
            f"--junit {args.junit} --source-run {args.source_run or ''}"
        ),
    }
    baseline = Baseline(entries=failures, provenance=provenance, selection=DEFAULT_SELECTION)
    Path(args.output).write_text(baseline.to_json())
    print(f"snapshot: {len(failures)} entries -> {args.output}")
    return 0


def _cmd_manifest(args: argparse.Namespace) -> int:
    em = build_expected_manifest()
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
    cmp.add_argument("--json-output", default="")
    cmp.add_argument("--markdown-output", default="")
    cmp.add_argument("--targeted-issues", action="store_true")
    cmp.set_defaults(func=_cmd_compare)

    snap = sub.add_parser("snapshot", help="generate a reviewable candidate baseline")
    snap.add_argument("--junit", required=True)
    snap.add_argument("--output", required=True)
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
