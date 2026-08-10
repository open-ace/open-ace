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
