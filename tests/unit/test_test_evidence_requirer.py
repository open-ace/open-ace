"""Requirer semantics for the test-evidence gate (#2391).

The gate is a *recognizer*: recognized test commands must pass, but a changed
domain with no evidence is not required to have any — an app+frontend PR passes
on pytest alone. #2391 adds a *requirer*: derive required evidence domains from
changed paths (independent of the recognizer), and — under enforce mode — fail
closed when a required domain has no passing evidence. Ships shadow-first, so on
merge it is a no-op (emits an observability event only).

Pure-function coverage here; the gate wiring is exercised at method level.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.command_evidence.test_evidence import (
    ParserConfidence,
    TestExecutionEvidence,
)
from app.modules.workspace.autonomous.command_evidence.types import ExecutionVerdict
from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.orchestrator import (
    MAX_TEST_RETRIES,
    AutonomousOrchestrator,
    _evidence_domains_covered,
    _required_evidence_domains,
    _test_evidence_requirer_mode,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(2391)]

_STRUCTURED = (
    "app.modules.workspace.autonomous.orchestrator."
    "AutonomousOrchestrator._compute_structured_test_verdict"
)
_PASSING_TOOL = "app.modules.workspace.autonomous.orchestrator._has_passing_test_tool_result"


@pytest.fixture
def full_tree(tmp_path):
    """A tree where BOTH the python and js suites exist, so suite-exists never
    discards a domain — isolates the path→domain rules."""
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    return str(tmp_path)


class TestRequiredEvidenceDomains:
    @pytest.mark.parametrize(
        "changed,expected",
        [
            (["app/x.py"], {"python"}),
            (["backend/y.py"], {"python"}),
            (["tests/unit/z.py"], {"python"}),
            (["frontend/src/a.tsx"], {"javascript"}),
            (["frontend/src/a.ts"], {"javascript"}),
            (["app/x.py", "frontend/src/a.tsx"], {"python", "javascript"}),
            # docs-only → nothing
            (["README.md", "docs/guide.md"], set()),
            (["app/x.py", "app/y.md"], {"python"}),  # not docs-ONLY
            # C4 traps that must NOT misfire:
            (["frontend/src/migration_wizard.tsx"], {"javascript"}),  # not python
            (["frontend/README.md"], set()),  # doc under frontend
            # de-scopes (F6): shell, e2e, migrations, .ts outside frontend, config
            (["scripts/deploy.sh"], set()),
            (["tests/e2e/test_flow.py"], set()),
            (["migrations/0001_init.py"], set()),
            (["src/app.ts"], set()),  # .ts outside frontend/
            (["package-lock.json"], set()),
            (["docs/cn/x.rst"], set()),
        ],
    )
    def test_domain_derivation(self, changed, expected, full_tree):
        assert _required_evidence_domains(changed, full_tree) == expected

    def test_suite_absent_python_is_not_required(self, tmp_path):
        # No pytest.ini / tests dir / pyproject pytest config → python not required
        # even though a .py changed (never hard-fail a repo with no suite).
        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / "package.json").write_text(json.dumps({"scripts": {"test": "v"}}))
        assert _required_evidence_domains(["app/x.py"], str(tmp_path)) == set()

    def test_suite_absent_js_is_not_required(self, tmp_path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        assert _required_evidence_domains(["frontend/src/a.tsx"], str(tmp_path)) == set()

    def test_empty_changed_files_requires_nothing(self, full_tree):
        # Fail-open on no/unavailable changed files (F4).
        assert _required_evidence_domains([], full_tree) == set()
        assert _required_evidence_domains(None, full_tree) == set()


def _ev(framework, verdict, confidence=ParserConfidence.HIGH):
    return TestExecutionEvidence(
        command_id="c",
        framework=framework,
        verdict=verdict.value if hasattr(verdict, "value") else verdict,
        parser_confidence=confidence.value,
    )


class TestEvidenceDomainsCovered:
    def test_passing_python_and_js_cover_their_domains(self):
        evs = [
            _ev("python", ExecutionVerdict.PASSED),
            _ev("javascript", ExecutionVerdict.PASSED),
        ]
        assert _evidence_domains_covered(evs) == {"python", "javascript"}

    def test_generic_covers_nothing(self):
        # F2: a wrapper-invoked run parses as generic → maps to no domain.
        assert _evidence_domains_covered([_ev("generic", ExecutionVerdict.PASSED)]) == set()

    def test_failed_or_low_confidence_does_not_cover(self):
        assert _evidence_domains_covered([_ev("python", ExecutionVerdict.FAILED)]) == set()
        assert (
            _evidence_domains_covered(
                [_ev("python", ExecutionVerdict.PASSED, ParserConfidence.LOW)]
            )
            == set()
        )

    def test_empty_evidences(self):
        assert _evidence_domains_covered([]) == set()
        assert _evidence_domains_covered(None) == set()


class TestRequirerMode:
    def test_default_is_shadow(self, monkeypatch):
        monkeypatch.delenv("OPENACE_TEST_EVIDENCE_REQUIRER_MODE", raising=False)
        assert _test_evidence_requirer_mode() == "shadow"

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("off", "off"),
            ("shadow", "shadow"),
            ("enforce", "enforce"),
            ("ENFORCE", "enforce"),
            (" off ", "off"),
            ("bogus", "shadow"),
            ("", "shadow"),
        ],
    )
    def test_mode_parsing(self, monkeypatch, value, expected):
        monkeypatch.setenv("OPENACE_TEST_EVIDENCE_REQUIRER_MODE", value)
        assert _test_evidence_requirer_mode() == expected


class TestApplyRequirer:
    """The gate-facing method: computes (mode, missing) and emits the shadow event."""

    def _orch(self):
        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        orch._workflow_id = "wf"
        orch.repo = MagicMock()
        return orch

    def test_enforce_flags_missing_frontend_on_a_passed_python_run(self, full_tree, monkeypatch):
        # The #2391 primary case (review F1): the run PASSED on python evidence,
        # frontend changed but was never verified → missing={javascript}. This is
        # exactly the run that flows straight to pr_review today.
        monkeypatch.setenv("OPENACE_TEST_EVIDENCE_REQUIRER_MODE", "enforce")
        orch = self._orch()
        mode, missing = orch._apply_test_evidence_requirer(
            "ms1",
            ["app/x.py", "frontend/src/a.tsx"],
            full_tree,
            [_ev("python", ExecutionVerdict.PASSED)],
        )
        assert mode == "enforce"
        assert missing == {"javascript"}

    def test_shadow_emits_the_event_and_still_reports_missing(self, full_tree, monkeypatch):
        monkeypatch.setenv("OPENACE_TEST_EVIDENCE_REQUIRER_MODE", "shadow")
        orch = self._orch()
        mode, missing = orch._apply_test_evidence_requirer(
            "ms1",
            ["app/x.py", "frontend/src/a.tsx"],
            full_tree,
            [_ev("python", ExecutionVerdict.PASSED)],
        )
        assert mode == "shadow"
        assert missing == {"javascript"}
        orch.repo.create_event.assert_called_once()
        event = orch.repo.create_event.call_args[0][0]
        assert event["event_type"] == "test_evidence_requirer_shadow"
        payload = json.loads(event["event_data"])
        assert payload["mode"] == "shadow"
        assert set(payload["required"]) == {"python", "javascript"}
        assert payload["covered"] == ["python"]
        assert payload["missing"] == ["javascript"]
        assert payload["changed_files"] == ["app/x.py", "frontend/src/a.tsx"]
        # per-evidence framework/confidence carried for F2/F3 triage
        assert payload["evidences"][0]["framework"] == "python"

    def test_covered_domain_yields_no_missing(self, full_tree, monkeypatch):
        monkeypatch.setenv("OPENACE_TEST_EVIDENCE_REQUIRER_MODE", "enforce")
        orch = self._orch()
        mode, missing = orch._apply_test_evidence_requirer(
            "ms1",
            ["app/x.py", "frontend/src/a.tsx"],
            full_tree,
            [_ev("python", ExecutionVerdict.PASSED), _ev("javascript", ExecutionVerdict.PASSED)],
        )
        assert missing == set()

    def test_off_mode_computes_and_emits_nothing(self, full_tree, monkeypatch):
        monkeypatch.setenv("OPENACE_TEST_EVIDENCE_REQUIRER_MODE", "off")
        orch = self._orch()
        mode, missing = orch._apply_test_evidence_requirer(
            "ms1", ["frontend/src/a.tsx"], full_tree, []
        )
        assert mode == "off"
        assert missing == set()
        orch.repo.create_event.assert_not_called()

    def test_never_raises_and_never_fabricates_missing_on_error(self, monkeypatch):
        # Fail-open: an event-emit failure must not raise, and a tree with no
        # suite requires nothing (so missing stays empty), never blocking a run.
        monkeypatch.setenv("OPENACE_TEST_EVIDENCE_REQUIRER_MODE", "enforce")
        orch = self._orch()
        orch.repo.create_event.side_effect = RuntimeError("db down")
        mode, missing = orch._apply_test_evidence_requirer(
            "ms1", ["frontend/src/a.tsx"], "/does/not/exist", []
        )
        assert mode == "enforce"
        assert missing == set()


def _js_repo(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text('{"scripts": {"test": "vitest"}}')
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    return str(tmp_path)


def _orchestrator(wf):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as repo_cls,
    ):
        repo = MagicMock()
        repo.get_workflow.return_value = wf
        repo.list_milestones.return_value = []
        repo.create_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": wf["workflow_id"],
        }
        repo.update_workflow.return_value = wf
        repo.update_milestone.return_value = {}
        repo.create_event.return_value = {"id": 1}
        repo_cls.return_value = repo
        orch = AutonomousOrchestrator(wf["workflow_id"])
        orch.repo = repo
        orch.emitter = MagicMock()
        orch._gh = MagicMock()
        orch._gh.has_uncommitted_changes.return_value = False
        return orch


def _drive_test_phase(
    orch,
    wf,
    *,
    structured,
    changed_files,
    mode,
    monkeypatch,
    session_success=True,
    response_text="243 passed in 3.0s",
):
    """Drive ``_run_test_phase`` end-to-end with a scripted structured verdict,
    changed files, and a requirer mode; return the ``_update_workflow`` patches so
    the caller can assert the route taken. Mirrors the proven harness in
    tests/issues/2376/test_gate_flags.py (which is collect-only in CI).

    ``session_success`` / ``response_text`` let a caller drive the agent-session
    failure (Situation A) and the ``[UNFIXABLE]`` report (Situation B), which the
    enforce guard must not preempt."""
    monkeypatch.setenv("OPENACE_TEST_EVIDENCE_REQUIRER_MODE", mode)
    result = AgentTaskResult(
        session_id="sess",
        response_text=response_text,
        visible_response_text=response_text,
        success=session_success,
        tool_calls=[{"tool": {"name": "Bash", "input": {"command": "python -m pytest tests/ -q"}}}],
    )
    patches: list[dict] = []
    orch._update_workflow = lambda p: patches.append(p)
    orch._post_github_comment = MagicMock()
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-1"})
    orch._find_or_create_milestone = MagicMock(return_value={"milestone_id": "ms-1"})
    orch._run_agent = MagicMock(return_value=result)
    orch._runtime_environment_gate = MagicMock(return_value="")
    orch._build_test_execution_context = MagicMock(return_value=("", changed_files))
    orch._project_runtime_contract = MagicMock(return_value="")
    orch._artifact_visible_text = MagicMock(return_value=result.response_text)
    orch._artifact_text = MagicMock(return_value=result.response_text)
    orch._shadow_compare_evidence = MagicMock()
    orch._emit_structured_test_fallback = MagicMock()
    orch._validate_test_report_format = MagicMock(return_value=(True, ""))
    with (
        patch(_STRUCTURED, return_value=structured),
        patch(_PASSING_TOOL, return_value=False),
    ):
        orch._run_test_phase(wf, 1, orch._gh)
    return patches


def _workflow(**overrides):
    base = {
        "workflow_id": "wf-2391",
        "user_id": 1,
        "title": "T",
        "status": "developing",
        "requirements_text": "r",
        "project_path": "/tmp/p",
        "worktree_path": "/tmp/p",
        "workspace_type": "local",
        "cli_tool": "claude-code",
        "branch_name": "auto-dev/x",
        "branch_strategy": "new-branch",
        "current_phase": "development",
        "dev_round": 1,
        "current_round": 1,
        "github_issue_number": 4321,
        "test_retries": 0,
        "skip_retries": 0,
        "dev_retries_on_test_fail": 0,
        "error_message": "",
    }
    base.update(overrides)
    return base


class TestEnforceControlFlow:
    """End-to-end: the enforce branch intercepts the otherwise-proceeding PASSED
    path — the #2391 review F1 case that flows straight to pr_review today."""

    def test_enforce_retries_a_passed_run_missing_a_changed_domain(self, tmp_path, monkeypatch):
        tree = _js_repo(tmp_path)
        wf = _workflow(worktree_path=tree, project_path=tree)
        orch = _orchestrator(wf)
        patches = _drive_test_phase(
            orch,
            wf,
            structured=(ExecutionVerdict.PASSED, [_ev("python", ExecutionVerdict.PASSED)], "ok"),
            changed_files=["app/x.py", "frontend/src/a.tsx"],
            mode="enforce",
            monkeypatch=monkeypatch,
        )
        # The enforce branch increments test_retries to 1 and returns; a proceeding
        # run instead RESETS test_retries to 0 on its way to pr_review.
        assert any(patch_.get("test_retries") == 1 for patch_ in patches), patches
        assert not any(patch_.get("current_phase") == "pr_review" for patch_ in patches)

    def test_shadow_does_not_gate_the_same_run(self, tmp_path, monkeypatch):
        tree = _js_repo(tmp_path)
        wf = _workflow(worktree_path=tree, project_path=tree)
        orch = _orchestrator(wf)
        patches = _drive_test_phase(
            orch,
            wf,
            structured=(ExecutionVerdict.PASSED, [_ev("python", ExecutionVerdict.PASSED)], "ok"),
            changed_files=["app/x.py", "frontend/src/a.tsx"],
            mode="shadow",
            monkeypatch=monkeypatch,
        )
        # Default shadow mode is a gate no-op: the run proceeds to pr_review.
        assert any(patch_.get("current_phase") == "pr_review" for patch_ in patches), patches
        assert not any(patch_.get("test_retries") == 1 for patch_ in patches)

    def test_enforce_does_not_gate_when_the_domain_is_covered(self, tmp_path, monkeypatch):
        tree = _js_repo(tmp_path)
        wf = _workflow(worktree_path=tree, project_path=tree)
        orch = _orchestrator(wf)
        patches = _drive_test_phase(
            orch,
            wf,
            structured=(
                ExecutionVerdict.PASSED,
                [
                    _ev("python", ExecutionVerdict.PASSED),
                    _ev("javascript", ExecutionVerdict.PASSED),
                ],
                "ok",
            ),
            changed_files=["app/x.py", "frontend/src/a.tsx"],
            mode="enforce",
            monkeypatch=monkeypatch,
        )
        # Both domains covered → no missing → proceeds even under enforce.
        assert any(patch_.get("current_phase") == "pr_review" for patch_ in patches), patches
        assert not any(patch_.get("test_retries") == 1 for patch_ in patches)

    @staticmethod
    def _requirer_status_posted(orch) -> bool:
        """True if any posted issue comment carried the requirer's retry line."""
        for call in orch._post_github_comment.call_args_list:
            body = call.args[2] if len(call.args) > 2 else call.kwargs.get("body", "")
            if "no passing evidence for it" in str(body):
                return True
        return False

    def test_enforce_does_not_preempt_agent_session_failure(self, tmp_path, monkeypatch):
        # PR-review F1 guard gap: a PASSED structured verdict can coexist with a
        # FAILED agent session (pytest ran + was recorded, then the session timed
        # out). With a missing domain under enforce, the requirer must NOT preempt
        # Situation A's agent-failure retry. Mutation check: dropping
        # ``test_result.success`` from the guard fires the requirer instead, which
        # this test's silence assertion catches.
        tree = _js_repo(tmp_path)
        wf = _workflow(worktree_path=tree, project_path=tree)
        orch = _orchestrator(wf)
        patches = _drive_test_phase(
            orch,
            wf,
            structured=(ExecutionVerdict.PASSED, [_ev("python", ExecutionVerdict.PASSED)], "ok"),
            changed_files=["app/x.py", "frontend/src/a.tsx"],
            mode="enforce",
            monkeypatch=monkeypatch,
            session_success=False,
            response_text="fatal: agent session terminated",
        )
        assert any(patch_.get("test_retries") == 1 for patch_ in patches), patches
        assert not self._requirer_status_posted(orch)
        assert not any(patch_.get("current_phase") == "pr_review" for patch_ in patches)

    def test_enforce_does_not_preempt_an_unfixable_report(self, tmp_path, monkeypatch):
        # PR-review F1 guard gap: a PASSED structured verdict + an [UNFIXABLE]
        # report must take Situation B's dev-round retry, not the requirer's test
        # retry — different counter, and [UNFIXABLE] means "return to development",
        # which a test rerun cannot satisfy. Mutation check: dropping
        # ``not has_unfixable`` from the guard fires the requirer (test_retries)
        # instead of the dev round.
        tree = _js_repo(tmp_path)
        wf = _workflow(worktree_path=tree, project_path=tree)
        orch = _orchestrator(wf)
        patches = _drive_test_phase(
            orch,
            wf,
            structured=(ExecutionVerdict.PASSED, [_ev("python", ExecutionVerdict.PASSED)], "ok"),
            changed_files=["app/x.py", "frontend/src/a.tsx"],
            mode="enforce",
            monkeypatch=monkeypatch,
            response_text="243 passed, but the flaky harness is [UNFIXABLE]",
        )
        assert any(patch_.get("dev_round") == wf["dev_round"] + 1 for patch_ in patches), patches
        assert not any(patch_.get("test_retries") == 1 for patch_ in patches)
        assert not self._requirer_status_posted(orch)

    def test_enforce_exhaustion_fails_the_workflow(self, tmp_path, monkeypatch):
        # After MAX_TEST_RETRIES the requirer stops retrying and fails the run,
        # mirroring the inconclusive path's cap.
        tree = _js_repo(tmp_path)
        wf = _workflow(worktree_path=tree, project_path=tree, test_retries=MAX_TEST_RETRIES)
        orch = _orchestrator(wf)
        patches = _drive_test_phase(
            orch,
            wf,
            structured=(ExecutionVerdict.PASSED, [_ev("python", ExecutionVerdict.PASSED)], "ok"),
            changed_files=["app/x.py", "frontend/src/a.tsx"],
            mode="enforce",
            monkeypatch=monkeypatch,
        )
        assert any(patch_.get("status") == "failed" for patch_ in patches), patches
        assert not any(patch_.get("current_phase") == "pr_review" for patch_ in patches)

    def test_enforce_is_suppressed_on_an_inconclusive_run(self, tmp_path, monkeypatch):
        # A missing domain on an INCONCLUSIVE run takes the inconclusive retry, not
        # the requirer branch (the guard's ``not test_result_inconclusive``).
        tree = _js_repo(tmp_path)
        wf = _workflow(worktree_path=tree, project_path=tree)
        orch = _orchestrator(wf)
        patches = _drive_test_phase(
            orch,
            wf,
            structured=(ExecutionVerdict.INCONCLUSIVE, [], "inconclusive"),
            changed_files=["app/x.py", "frontend/src/a.tsx"],
            mode="enforce",
            monkeypatch=monkeypatch,
            response_text="a test command ran but produced no parseable result",
        )
        assert any(patch_.get("test_retries") == 1 for patch_ in patches), patches
        assert not self._requirer_status_posted(orch)
