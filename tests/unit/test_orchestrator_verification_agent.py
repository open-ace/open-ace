"""Regression: the acceptance verifier spawn must pass workflow_id.

``_run_verification_agent`` calls ``_run_agent`` to spawn the independent
verifier. Every other ``_run_agent`` caller passes ``workflow_id`` (it is a
required arg of ``AutonomousAgentRunner.run_agent_task``), but the verification
call omitted it — so any workflow reaching acceptance_verification crashed with
``TypeError: run_agent_task() missing 1 required positional argument:
'workflow_id'`` (caught on b48179df / issue #2394 after the datetime fix let it
reach this phase).
"""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

pytestmark = [pytest.mark.regression]


def test_run_verification_agent_passes_workflow_id():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-verify-test"
    orch._build_verification_prompt = MagicMock(return_value="verify prompt")
    orch._checkout_merged_main = MagicMock(return_value="/tmp/merged-checkout")
    orch._remove_verification_worktree = MagicMock()
    # success=False so the method returns the infra_error dict early (no parsing).
    orch._run_agent = MagicMock(return_value=MagicMock(success=False, error_code=""))

    with patch.object(
        type(orch),
        "workflow",
        new_callable=PropertyMock,
        return_value={"cli_tool": "claude-code", "model": ""},
    ):
        orch._run_verification_agent(
            snapshot=MagicMock(),
            merge_sha="merge-sha",
            base_sha="base-sha",
            issue_number=2394,
            pr_number=2465,
        )

    orch._run_agent.assert_called_once()
    # The spawn must carry workflow_id like every other _run_agent caller.
    assert orch._run_agent.call_args.kwargs.get("workflow_id") == "wf-verify-test"


def test_run_verification_agent_clears_branch_name_for_detached_checkout():
    """The verifier runs in a ``--detach`` worktree on merged main, NOT the dev
    branch. If ``verify_wf`` keeps the workflow's dev ``branch_name``, the
    post-run repo-integrity check (``_validate_repo_context_after_run``) sees
    detached HEAD != ``auto-dev/<id>`` and false-flags a
    ``repo_integrity_violation`` ("Agent changed the workflow branch
    unexpectedly"), stranding the workflow at acceptance_verification.

    ``_run_verification_agent`` must clear ``branch_name`` on the verify copy so
    the branch-mismatch check is skipped for the deliberately-detached checkout.
    The repo-escape check (a different guard) still applies, so the verifier
    remains confined to the checkout path. (prod b48179df / cd939cbf after the
    glm JSON-tolerance deploy let the verifier reach this code path.)
    """

    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-verify-branch"
    orch._build_verification_prompt = MagicMock(return_value="verify prompt")
    orch._checkout_merged_main = MagicMock(return_value="/tmp/merged-checkout")
    orch._remove_verification_worktree = MagicMock()
    orch._run_agent = MagicMock(return_value=MagicMock(success=False, error_code=""))

    with patch.object(
        type(orch),
        "workflow",
        new_callable=PropertyMock,
        return_value={
            "cli_tool": "claude-code",
            "model": "",
            "branch_name": "auto-dev/wf-verify-branch",
            "worktree_path": "/home/x/open-ace/.worktrees/wf-verify-branch",
        },
    ):
        orch._run_verification_agent(
            snapshot=MagicMock(),
            merge_sha="merge-sha",
            base_sha="base-sha",
            issue_number=2394,
            pr_number=2465,
        )

    orch._run_agent.assert_called_once()
    verify_wf = orch._run_agent.call_args.args[0]
    # branch_name cleared so the detached-checkout branch-mismatch check skips.
    assert verify_wf["branch_name"] == ""
    # The merged-main checkout path is still bound.
    assert verify_wf["worktree_path"] == "/tmp/merged-checkout"
    assert verify_wf["project_path"] == "/tmp/merged-checkout"
