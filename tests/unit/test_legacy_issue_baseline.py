"""Unit + contract tests for the legacy issue failure baseline comparator."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "legacy_issue_baseline", ROOT / "scripts" / "legacy_issue_baseline.py"
)
assert SPEC and SPEC.loader
lib = importlib.util.module_from_spec(SPEC)
# Register before exec so dataclasses can resolve PEP 563 string annotations.
sys.modules["legacy_issue_baseline"] = lib
SPEC.loader.exec_module(lib)

FailureRecord = lib.FailureRecord
ParsedTestcase = lib.ParsedTestcase
Baseline = lib.Baseline
BaselineError = lib.BaselineError


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


# Minimal xunit2 XML builders -------------------------------------------------


def _xml(
    testcases: str, tests: int = 1, failures: int = 0, errors: int = 0, skipped: int = 0
) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="pytest" tests="{tests}" failures="{failures}" '
        f'errors="{errors}" skipped="{skipped}">{testcases}</testsuite></testsuites>'
    )


def _tc(
    name: str,
    classname: str = "tests.issues.716.test_x",
    *,
    failure: str = "",
    error: str = "",
    skipped: str = "",
    nodeid_prop: str = "",
) -> str:
    props = ""
    if nodeid_prop:
        props = f'<properties><property name="openace_nodeid" value="{nodeid_prop}"/></properties>'
    child = ""
    if failure:
        child = f'<failure message="{failure}" type="AssertionError">{failure}</failure>'
    elif error:
        etype, msg = error.split(":", 1) if ":" in error else (error, "")
        child = f'<error message="{msg}" type="{etype}">{msg}</error>'
    elif skipped:
        child = f'<skipped message="{skipped}"/>'
    return f'<testcase classname="{classname}" name="{name}">{props}{child}</testcase>'


# --- Task 1: schema ---------------------------------------------------------


def test_normalize_nodeid_strips_runner_root_and_anchors():
    n = lib.normalize_nodeid(
        "/home/runner/work/open-ace/open-ace/tests/issues/716/test_x.py::test_y",
        runner_root="/home/runner/work/open-ace/open-ace",
    )
    assert n == "tests/issues/716/test_x.py::test_y"


def test_normalize_nodeid_keeps_params():
    assert lib.normalize_nodeid("tests/issues/716/test_x.py::test_y[1-2]").endswith("::test_y[1-2]")


def test_failure_record_key():
    r = FailureRecord("a::b", "716", "failure", "assertion_failure", "AssertionError", "x")
    assert r.key == ("a::b", "failure", "assertion_failure")


def test_baseline_byte_stable_roundtrip(tmp_path):
    recs = [
        FailureRecord("tests/issues/9/t.py::b", "9", "error", "setup_error", "E", "s"),
        FailureRecord(
            "tests/issues/716/t.py::a", "716", "failure", "assertion_failure", "AssertionError", "x"
        ),
    ]
    b = Baseline(entries=recs, provenance={"reference_commit": "abc", "source_run": "1"})
    s1 = b.to_json()
    s2 = Baseline.from_json(s1).to_json()
    assert s1 == s2
    obj = json.loads(s1)
    assert obj["entries"][0]["nodeid"].endswith("716/t.py::a")  # sorted
    assert obj["version"] == lib.SCHEMA_VERSION
    assert "/home/" not in s1 and "openace_nodeid" not in s1


def test_baseline_rejects_unknown_schema():
    bad = json.dumps({"version": 99, "schema": lib.SCHEMA_NAME, "entries": []})
    with pytest.raises(BaselineError):
        Baseline.from_json(bad)


# --- Task 2: classify -------------------------------------------------------


@pytest.mark.parametrize(
    "element,exc,msg,nodeid,expected",
    [
        (
            "failure",
            "AssertionError",
            "assert 1==2",
            "tests/issues/716/t.py::a",
            "assertion_failure",
        ),
        ("failure", "ValueError", "bad", "tests/issues/716/t.py::a", "test_body_exception"),
        (
            "error",
            "TimeoutError",
            "timed out after 240s",
            "tests/issues/716/t.py::a",
            "timeout",
        ),
        ("error", "Exception", "", "tests/issues/716/t.py::a", "setup_error"),
        # setup error lacking the openace_nodeid property must NOT be collection_error
        (
            "error",
            "Exception",
            "failed on setup",
            "tests/issues/144/e2e_x.py::test_a",
            "setup_error",
        ),
        ("error", "Exception", "", "tests.issues.716.test_broken", "collection_error"),
    ],
)
def test_classify(element, exc, msg, nodeid, expected):
    assert lib.classify(element, exc, msg, nodeid) == expected


def test_classify_collection_word_in_normal_test_is_not_collection_error():
    assert (
        lib.classify("error", "Exception", "collection", "tests/issues/716/t.py::test_a")
        == "setup_error"
    )


# --- Task 3: junit parser ---------------------------------------------------


def test_parse_prefers_nodeid_property(tmp_path):
    xml = _xml(
        _tc(
            "test_a",
            classname="tests.issues.716.test_x",
            failure="boom",
            nodeid_prop="tests/issues/716/test_x.py::test_a",
        ),
        tests=1,
        failures=1,
    )
    tcs, totals = lib.parse_junit(_write(tmp_path, "a.xml", xml))
    assert tcs[0].nodeid == "tests/issues/716/test_x.py::test_a"
    assert tcs[0].outcome == "failure"


def test_parse_reconstruction_class_and_param(tmp_path):
    # no property -> reconstruct from classname+name
    xml = _xml(
        _tc("test_bar[1-2]", classname="tests.issues.716.test_x.TestFoo", failure="x"),
        tests=1,
        failures=1,
    )
    tcs, _ = lib.parse_junit(_write(tmp_path, "a.xml", xml))
    assert tcs[0].nodeid == "tests/issues/716/test_x.py::TestFoo::test_bar[1-2]"


def test_parse_reconstruction_plain(tmp_path):
    xml = _xml(
        _tc("test_a", classname="tests.issues.517.e2e_codex", failure="x"), tests=1, failures=1
    )
    tcs, _ = lib.parse_junit(_write(tmp_path, "a.xml", xml))
    assert tcs[0].nodeid == "tests/issues/517/e2e_codex.py::test_a"


def test_parse_collection_error_shape(tmp_path):
    # verified real shape: empty classname, dotted module name, <error>, no property
    xml = _xml(
        '<testcase classname="" name="tests.issues.517.test_broken"><error message="" type="Exception"></error></testcase>',
        tests=1,
        errors=1,
    )
    tcs, _ = lib.parse_junit(_write(tmp_path, "a.xml", xml))
    assert tcs[0].category == "collection_error"
    assert tcs[0].nodeid == "tests/issues/517/test_broken.py"
    assert tcs[0].as_failure() is not None and tcs[0].as_failure().category == "collection_error"


def test_parse_does_not_dedupe_duplicates(tmp_path):
    # parse_junit returns ALL testcases; dedup/conflict detection is compare()'s
    # job (never last-write-wins at parse time).
    body = _tc("test_a", failure="x", nodeid_prop="tests/issues/716/t.py::test_a") + _tc(
        "test_a", failure="x", nodeid_prop="tests/issues/716/t.py::test_a"
    )
    xml = _xml(body, tests=1, failures=1)
    tcs, _ = lib.parse_junit(_write(tmp_path, "a.xml", xml))
    assert len(tcs) == 2


def test_parse_rerun_then_pass_keeps_parsed_for_completeness(tmp_path):
    # final attempt is a pass: 0 FailureRecords but the nodeid is still tracked
    body = _tc("test_a", nodeid_prop="tests/issues/716/t.py::test_a")  # no failure -> pass
    xml = _xml(body, tests=1)
    tcs, _ = lib.parse_junit(_write(tmp_path, "a.xml", xml))
    assert len(tcs) == 1
    assert tcs[0].as_failure() is None
    assert tcs[0].outcome == "pass"


def test_parse_corrupt_xml_raises(tmp_path):
    with pytest.raises(BaselineError):
        lib.parse_junit(_write(tmp_path, "a.xml", "<not xml<"))


def test_parse_empty_raises(tmp_path):
    with pytest.raises(BaselineError):
        lib.parse_junit(_write(tmp_path, "a.xml", _xml("", tests=0)))


def test_parse_summary_scrubs_runner_paths_and_ports(tmp_path):
    body = (
        '<testcase classname="tests.issues.1071.test_x" name="test_a">'
        '<properties><property name="openace_nodeid" value="tests/issues/1071/test_x.py::test_a"/></properties>'
        '<failure message="AttributeError from /home/runner/work/open-ace/open-ace/app/routes/workspace.py at localhost:19888" type="AttributeError"></failure>'
        "</testcase>"
    )
    xml = _xml(body, tests=1, failures=1)
    tcs, _ = lib.parse_junit(_write(tmp_path, "a.xml", xml))
    assert "/home/runner" not in tcs[0].summary
    assert "localhost:19888" not in tcs[0].summary
    assert "app/routes/workspace.py" in tcs[0].summary  # repo-relative retained
    assert "<host:port>" in tcs[0].summary


# --- Task 4: manifest -------------------------------------------------------


def test_manifest_parser_drops_warnings_and_footer(monkeypatch):
    fake_out = (
        "tests/issues/716/t.py::test_a\n"
        "tests/issues/716/t.py::test_b\n"
        "warnings summary\n"
        "  tests/issues/559/e2e_terminal_ws_handler.py:28: PytestWarning\n"
        "    some message\n"
        "4475 tests collected in 2.1s\n"
    )
    captured = {}

    class _Proc:
        stdout = fake_out
        returncode = 0

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(lib.subprocess, "run", fake_run)
    em = lib.build_expected_manifest()
    assert em.nodeids == ["tests/issues/716/t.py::test_a", "tests/issues/716/t.py::test_b"]
    assert em.files == ["tests/issues/716/t.py"]
    assert "-qq" in captured["cmd"]


def test_manifest_smoke_real_collection():
    em = lib.build_expected_manifest()
    assert len(em.nodeids) > 0
    assert all(n.startswith("tests/") and "::" in n for n in em.nodeids)


# --- Task 5: compare --------------------------------------------------------


def _fail(nodeid, outcome="failure", category="assertion_failure"):
    return FailureRecord(
        nodeid,
        nodeid.split("/")[2] if "/issues/" in nodeid else "0",
        outcome,
        category,
        "AssertionError",
        "x",
    )


def _tc_parsed(nodeid, outcome="failure", category="assertion_failure"):
    return ParsedTestcase(nodeid, outcome, category, "AssertionError", "x")


def _compare(baseline_entries, parsed, expected, *, min_files=0, thr=0.0):
    return lib.compare(
        Baseline(entries=baseline_entries),
        parsed,
        expected,
        baseline_min_files=min_files,
        require_review_threshold_pct=thr,
    )


def test_known_only_succeeds():
    r = _compare(
        [_fail("tests/issues/716/t.py::a")],
        [_tc_parsed("tests/issues/716/t.py::a")],
        ["tests/issues/716/t.py::a"],
    )
    assert r.exit_code == 0 and r.known and not r.new and not r.resolved


def test_new_failure_fails():
    r = _compare([], [_tc_parsed("tests/issues/716/t.py::b")], ["tests/issues/716/t.py::b"])
    assert r.exit_code != 0 and r.new


def test_changed_not_also_in_new():
    base = [_fail("tests/issues/716/t.py::a", "error", "setup_error")]
    cur = [_tc_parsed("tests/issues/716/t.py::a", "failure", "assertion_failure")]
    r = _compare(base, cur, ["tests/issues/716/t.py::a"])
    assert r.exit_code != 0 and r.changed
    assert all(d["nodeid"] != "tests/issues/716/t.py::a" for d in r.new)


def test_resolved_rerun_pass_or_deleted_forces_shrink():
    # baselined failure now passes -> ParsedTestcase outcome=pass, no current failure -> resolved
    r = _compare(
        [_fail("tests/issues/716/t.py::a")],
        [ParsedTestcase("tests/issues/716/t.py::a", "pass", "pass", "", "")],
        ["tests/issues/716/t.py::a"],
    )
    assert r.exit_code != 0 and r.resolved == ["tests/issues/716/t.py::a"]


def test_collection_error_always_fails_even_if_baselined():
    base = [_fail("tests/issues/716/test_m.py", "error", "collection_error")]
    cur = [_tc_parsed("tests/issues/716/test_m.py", "error", "collection_error")]
    r = _compare(base, cur, ["tests/issues/716/test_m.py"])
    assert r.exit_code != 0 and r.collection_errors


def test_incomplete_coverage_fails():
    r = _compare([], [], ["tests/issues/716/t.py::missing"])
    assert r.exit_code != 0 and r.invalid


def test_zero_reports_message():
    r = _compare([], [], ["tests/issues/716/t.py::a"])
    assert r.exit_code != 0
    assert any("no JUnit reports" in i or "never observed" in i for i in r.invalid)


def test_file_count_regression_fails():
    parsed = [_tc_parsed(f"tests/issues/716/t.py::t{i}") for i in range(5)]
    expected = [f"tests/issues/716/t.py::t{i}" for i in range(5)]
    r = _compare([], parsed, expected, min_files=430, thr=10.0)
    assert r.exit_code != 0 and any("below floor" in i for i in r.invalid)


def test_conflict_same_key_different_summary_rejected():
    a = ParsedTestcase(
        "tests/issues/716/t.py::a", "failure", "assertion_failure", "AssertionError", "S1"
    )
    b = ParsedTestcase(
        "tests/issues/716/t.py::a", "failure", "assertion_failure", "AssertionError", "S2"
    )
    r = _compare([], [a, b], ["tests/issues/716/t.py::a"])
    assert r.exit_code != 0 and any("conflict" in i for i in r.invalid)


def test_duplicate_nodeids_flagged_as_conflict():
    # P1#5: never last-write-wins. A nodeid appearing twice (even identically)
    # is a cross-shard/rerun anomaly and must be flagged, not silently deduped.
    parsed = [_tc_parsed("tests/issues/716/t.py::a"), _tc_parsed("tests/issues/716/t.py::a")]
    r = _compare([], parsed, ["tests/issues/716/t.py::a"])
    assert r.exit_code != 0
    assert any("duplicate nodeid" in i for i in r.invalid)


def test_multi_key_per_nodeid_rejected():
    # a single nodeid carrying two different (outcome,category) keys must not be
    # silently collapsed (would let a resolved/changed entry drop silently)
    a = ParsedTestcase("tests/issues/716/t.py::a", "failure", "assertion_failure", "E", "s")
    b = ParsedTestcase("tests/issues/716/t.py::a", "error", "setup_error", "E2", "s2")
    r = _compare([], [a, b], ["tests/issues/716/t.py::a"])
    assert r.exit_code != 0 and any("multiple failure keys" in i for i in r.invalid)


def test_targeted_run_marked_invalid_not_full_gate():
    parsed = [_tc_parsed("tests/issues/716/t.py::a")]
    r = lib.compare(
        Baseline(entries=[]),
        parsed,
        ["tests/issues/716/t.py::a"],
        baseline_min_files=0,
        require_review_threshold_pct=0.0,
        targeted=True,
    )
    assert r.exit_code != 0 and any("targeted run" in i for i in r.invalid)


# --- Task 6: cli ------------------------------------------------------------


def test_cli_compare_known_only_exits_zero(tmp_path, monkeypatch):
    junit = _xml(
        _tc("test_a", failure="boom", nodeid_prop="tests/issues/716/t.py::test_a"),
        tests=1,
        failures=1,
    )
    jp = _write(tmp_path, "issues-1.xml", junit)
    baseline = Baseline(
        entries=[
            FailureRecord(
                "tests/issues/716/t.py::test_a",
                "716",
                "failure",
                "assertion_failure",
                "AssertionError",
                "boom",
            )
        ]
    )
    bp = _write(tmp_path, "baseline.json", baseline.to_json())
    monkeypatch.setattr(
        lib,
        "build_expected_manifest",
        lambda *a, **k: lib.ExpectedManifest(
            ["tests/issues/716/t.py::test_a"], ["tests/issues/716/t.py"], "not postgres", False
        ),
    )
    rc = lib.main(
        [
            "compare",
            "--baseline",
            str(bp),
            "--junit",
            str(jp),
            "--test-baseline",
            str(
                _write(
                    tmp_path,
                    "tb.json",
                    json.dumps(
                        {
                            "layers": {"issues": {"min_files": 1}},
                            "tolerance": {"require_review_threshold": 10},
                        }
                    ),
                )
            ),
            "--json-output",
            str(tmp_path / "diff.json"),
            "--markdown-output",
            str(tmp_path / "sum.md"),
            "--shard-count",
            "0",
            "--quarantine",
            str(tmp_path / "none.json"),
        ]
    )
    assert rc == 0
    assert json.loads((tmp_path / "diff.json").read_text())["exit_code"] == 0


def test_cli_snapshot_refuses_collection_errors(tmp_path):
    junit = _xml(
        '<testcase classname="" name="tests.issues.716.test_broken"><error type="Exception"></error></testcase>',
        tests=1,
        errors=1,
    )
    jp = _write(tmp_path, "issues-1.xml", junit)
    rc = lib.main(["snapshot", "--junit", str(jp), "--output", str(tmp_path / "out.json")])
    assert rc == 2
    assert not (tmp_path / "out.json").exists()


def test_cli_snapshot_writes_baseline(tmp_path, monkeypatch):
    junit = _xml(
        _tc("test_a", failure="boom", nodeid_prop="tests/issues/716/t.py::test_a"),
        tests=1,
        failures=1,
    )
    jp = _write(tmp_path, "issues-1.xml", junit)
    out = tmp_path / "out.json"
    tb = _write(tmp_path, "tb.json", json.dumps({"layers": {"issues": {"min_files": 0}}}))
    # snapshot requires a complete reference run; stub the expected manifest so
    # the single testcase satisfies completeness.
    monkeypatch.setattr(
        lib,
        "build_expected_manifest",
        lambda *a, **k: lib.ExpectedManifest(
            ["tests/issues/716/t.py::test_a"], ["tests/issues/716/t.py"], "not postgres", False
        ),
    )
    rc = lib.main(
        [
            "snapshot",
            "--junit",
            str(jp),
            "--output",
            str(out),
            "--test-baseline",
            str(tb),
            "--shard-count",
            "0",
            "--quarantine",
            str(tmp_path / "none.json"),
            "--source-run",
            "123",
        ]
    )
    assert rc == 0
    b = Baseline.from_json(out.read_text())
    assert len(b.entries) == 1
    assert b.provenance["source_run"] == "123"


def test_cli_snapshot_refuses_partial_run(tmp_path, monkeypatch):
    # P0#2: snapshot must refuse a reference run missing expected nodeids.
    junit = _xml(
        _tc("test_a", failure="boom", nodeid_prop="tests/issues/716/t.py::test_a"),
        tests=1,
        failures=1,
    )
    jp = _write(tmp_path, "issues-1.xml", junit)
    out = tmp_path / "out.json"
    monkeypatch.setattr(
        lib,
        "build_expected_manifest",
        lambda *a, **k: lib.ExpectedManifest(
            ["tests/issues/716/t.py::test_a", "tests/issues/716/t.py::missing"],
            ["tests/issues/716/t.py"],
            "not postgres",
            False,
        ),
    )
    rc = lib.main(["snapshot", "--junit", str(jp), "--output", str(out)])
    assert rc == 2
    assert not out.exists()


def test_compare_creates_output_parent_dirs(tmp_path, monkeypatch):
    # P0#1: writing into a non-existent test-results/ must not FileNotFoundError.
    junit = _xml(
        _tc("test_a", failure="boom", nodeid_prop="tests/issues/716/t.py::test_a"),
        tests=1,
        failures=1,
    )
    jp = _write(tmp_path, "issues-1.xml", junit)
    baseline = Baseline(
        entries=[
            FailureRecord(
                "tests/issues/716/t.py::test_a",
                "716",
                "failure",
                "assertion_failure",
                "AssertionError",
                "boom",
            )
        ]
    )
    bp = _write(tmp_path, "baseline.json", baseline.to_json())
    tb = _write(
        tmp_path,
        "tb.json",
        json.dumps(
            {"layers": {"issues": {"min_files": 1}}, "tolerance": {"require_review_threshold": 10}}
        ),
    )
    monkeypatch.setattr(
        lib,
        "build_expected_manifest",
        lambda *a, **k: lib.ExpectedManifest(
            ["tests/issues/716/t.py::test_a"], ["tests/issues/716/t.py"], "not postgres", False
        ),
    )
    nested = tmp_path / "nested" / "sub" / "diff.json"
    rc = lib.main(
        [
            "compare",
            "--baseline",
            str(bp),
            "--junit",
            str(jp),
            "--test-baseline",
            str(tb),
            "--json-output",
            str(nested),
            "--shard-count",
            "0",
            "--quarantine",
            str(tmp_path / "none.json"),
        ]
    )
    assert rc == 0
    assert nested.exists()


def test_manifest_fail_closed_on_nonzero_returncode(monkeypatch):
    # P1#6: partial collection (non-zero exit) must fail closed even if some
    # nodeids were parsed.
    class _Proc:
        stdout = "tests/issues/716/t.py::test_a\n"
        returncode = 2

    monkeypatch.setattr(lib.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(lib.BaselineError):
        lib.build_expected_manifest()


def test_exception_type_extracted_from_message(tmp_path):
    # P1#7: xunit2 omits type; recover it from the message form.
    body = (
        '<testcase classname="tests.issues.1071.t" name="test_a">'
        '<properties><property name="openace_nodeid" value="tests/issues/1071/t.py::test_a"/></properties>'
        '<failure message="app.utils.tenant_resolver.TenantResolutionError: cannot resolve" >tb</failure>'
        "</testcase>"
    )
    tcs, _ = lib.parse_junit(_write(tmp_path, "a.xml", _xml(body, tests=1, failures=1)))
    assert tcs[0].exception_type == "TenantResolutionError"
    assert tcs[0].category == "test_body_exception"


def test_exception_type_assertion_from_message(tmp_path):
    body = (
        '<testcase classname="tests.issues.716.t" name="test_a">'
        '<properties><property name="openace_nodeid" value="tests/issues/716/t.py::test_a"/></properties>'
        '<failure message="assert 1 == 2">tb</failure>'
        "</testcase>"
    )
    tcs, _ = lib.parse_junit(_write(tmp_path, "a.xml", _xml(body, tests=1, failures=1)))
    assert tcs[0].exception_type == "AssertionError"
    assert tcs[0].category == "assertion_failure"


def test_compare_infra_exit_code_fails_closed(tmp_path):
    # P1#4: a shard pytest exit code of 2/3/4/5 (infrastructure) must fail even
    # if the XML looks whole.
    parsed = [_tc_parsed("tests/issues/716/t.py::a")]
    r = lib.compare(
        Baseline(entries=[_fail("tests/issues/716/t.py::a")]),
        parsed,
        ["tests/issues/716/t.py::a"],
        exit_codes={"issues-2.exit-code": 2},
    )
    assert r.exit_code != 0
    assert any("exited 2" in i for i in r.invalid)


def test_exit_code_cardinality_enforced(tmp_path):
    # P1#4/P0#2: with expected_shard_count=4, exactly issues-{1..4}.exit-code are
    # required; 0/1/3 files, wrong names, and 2-5 codes all fail closed.
    parsed = [_tc_parsed("tests/issues/716/t.py::a")]
    base = Baseline(entries=[_fail("tests/issues/716/t.py::a")])
    exp = ["tests/issues/716/t.py::a"]

    def _res(codes):
        return lib.compare(base, parsed, exp, exit_codes=codes, expected_shard_count=4)

    # only 1 of 4 -> missing three
    r = _res({"issues-1.exit-code": 1})
    assert r.exit_code != 0 and any("missing shard exit-code" in i for i in r.invalid)
    # all 4 but one exited 2 (infra) -> fail
    r = _res({f"issues-{i}.exit-code": 0 for i in range(1, 5)} | {"issues-3.exit-code": 2})
    assert r.exit_code != 0 and any("exited 2" in i for i in r.invalid)
    # extra wrong-name file -> fail
    r = _res({f"issues-{i}.exit-code": 0 for i in range(1, 5)} | {"issues-9.exit-code": 0})
    assert r.exit_code != 0 and any("unexpected shard exit-code" in i for i in r.invalid)
    # exactly 4, all 0/1 -> pass (known-only)
    r = _res({f"issues-{i}.exit-code": (0 if i % 2 else 1) for i in range(1, 5)})
    assert r.exit_code == 0


def _q(nodeid="tests/issues/604/t.py::a", expires="2099-01-01"):
    return lib.QuarantineEntry(nodeid, "r", "o", "https://t", "exit", expires, "timeout")


def test_quarantine_valid_entry_passs():
    inv = lib.validate_quarantine([_q()], ["tests/issues/604/t.py::a"], "2026-08-10")
    assert inv == []


def test_quarantine_expired_fails_closed():
    inv = lib.validate_quarantine(
        [_q(expires="2020-01-01")], ["tests/issues/604/t.py::a"], "2026-08-10"
    )
    assert any("expired" in i for i in inv)


def test_quarantine_uncollectable_fails_closed():
    inv = lib.validate_quarantine(
        [_q(nodeid="tests/issues/604/t.py::gone")], ["tests/issues/604/t.py::a"], "2026-08-10"
    )
    assert any("no longer collectable" in i for i in inv)


def test_quarantine_duplicate_and_missing_fields():
    inv = lib.validate_quarantine(
        [_q(), _q()],
        ["tests/issues/604/t.py::a"],
        "2026-08-10",
    )
    assert any("duplicated" in i for i in inv)
    bad = lib.QuarantineEntry(
        "tests/issues/604/t.py::a", "", "o", "t", "exit", "2099-01-01", "timeout"
    )
    inv2 = lib.validate_quarantine([bad], ["tests/issues/604/t.py::a"], "2026-08-10")
    assert any("missing field" in i for i in inv2)


def test_compare_reports_quarantined_as_debt(tmp_path):
    parsed = [_tc_parsed("tests/issues/716/t.py::a")]
    base = Baseline(entries=[_fail("tests/issues/716/t.py::a")])
    q = [
        {
            "nodeid": "tests/issues/604/t.py::x",
            "reason": "deadlock",
            "owner": "o",
            "tracking_issue": "t",
            "exit_condition": "e",
            "expires_on": "2099-01-01",
        }
    ]
    r = lib.compare(
        base, parsed, ["tests/issues/716/t.py::a"], quarantined=q, expected_shard_count=0
    )
    assert r.exit_code == 0  # quarantined is debt, not a failure
    assert r.quarantined == q


def test_unexpected_observed_nodeid_fails_closed():
    # P1 (bidirectional): observed - expected must also fail (e.g. a quarantined
    # nodeid that ran anyway, or a stale artifact).
    parsed = [_tc_parsed("tests/issues/716/t.py::a"), _tc_parsed("tests/issues/716/t.py::extra")]
    r = _compare([], parsed, ["tests/issues/716/t.py::a"])
    assert r.exit_code != 0
    assert any("unexpected observed nodeid" in i and "extra" in i for i in r.invalid)


def test_load_exit_codes_rejects_duplicate_basename(tmp_path):
    # P1: two paths with the same issues-N.exit-code basename must not last-write-win.
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "issues-1.exit-code").write_text("0")
    (tmp_path / "b" / "issues-1.exit-code").write_text("1")
    with pytest.raises(lib.BaselineError):
        lib._load_exit_codes(str(tmp_path / "**" / "issues-*.exit-code"))


def test_quarantine_bad_calendar_date_rejected():
    # P1: 2026-99-99 matches the old shape regex but is not a real date.
    bad = lib.QuarantineEntry(
        "tests/issues/604/t.py::a", "r", "o", "t", "e", "2026-99-99", "timeout"
    )
    inv = lib.validate_quarantine([bad], ["tests/issues/604/t.py::a"], "2026-08-10")
    assert any("malformed expires_on" in i for i in inv)


def test_load_quarantine_rejects_wrong_version(tmp_path):
    p = tmp_path / "q.json"
    p.write_text('{"version": 99, "schema": "openace-legacy-issue-quarantine", "entries": []}')
    with pytest.raises(lib.BaselineError):
        lib.load_quarantine(p)


def test_scrub_strips_toolcache_python_binary():
    # P1: runner-specific /opt/hostedtoolcache/.../bin/python must not leak.
    s = lib._scrub_env("from /opt/hostedtoolcache/Python/3.11.15/x64/bin/python -m pytest")
    assert "/opt/hostedtoolcache" not in s and "bin/python" not in s
    assert "python" in s


def test_quarantine_invalid_probe_outcome_rejected():
    bad = lib.QuarantineEntry(
        "tests/issues/604/t.py::a", "r", "o", "t", "e", "2099-01-01", "explodes"
    )
    inv = lib.validate_quarantine([bad], ["tests/issues/604/t.py::a"], "2026-08-10")
    assert any("invalid expected_probe_outcome" in i for i in inv)
    good = lib.QuarantineEntry(
        "tests/issues/604/t.py::a", "r", "o", "t", "e", "2099-01-01", "timeout"
    )
    assert lib.validate_quarantine([good], ["tests/issues/604/t.py::a"], "2026-08-10") == []
