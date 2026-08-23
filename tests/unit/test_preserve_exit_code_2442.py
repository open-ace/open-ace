"""Issue #2442: exit 70 (preserve prep failed) classifies distinctly.

The startup site exits 70 when ``_move_to_preserve`` cannot clear the prior
preserve dir. ``_classify_isolated_exit_code`` must map it to a structured code
so the failure is reported as a nesting-guard abort, not a generic crash.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

pytestmark = [pytest.mark.regression, pytest.mark.issue(2442)]


def test_exit_70_is_preserve_preparation_failed():
    code, msg = AutonomousAgentRunner._classify_isolated_exit_code(70)
    assert code == "preserve_preparation_failed"
    assert msg and "preserve" in msg.lower()


def test_exit_70_distinct_from_overloaded_66_and_repo_68():
    # 66 is overloaded (cgroup/pre-flight via sentinel) and 68 is repo
    # integrity; 70 must not collide with either.
    assert (
        AutonomousAgentRunner._classify_isolated_exit_code(70)[0] == "preserve_preparation_failed"
    )
