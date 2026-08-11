"""Terminal-failure report rendering for autonomous workflows (#2443).

Distinct from ``progress_report_i18n.py``, which renders *intermediate* phase
reports. A CI-repair terminal report is posted when merge-phase CI repair
exhausts, so a workflow that lands in the absorbing ``failed`` state is visible
on its issue with a retry entry point instead of silent DB absorption (the
#2443 gap). English only for now; the structure is stable so i18n can follow.
"""

from __future__ import annotations


def render_ci_repair_terminal_report(
    *,
    category: str,
    reason: str,
    attempts: int,
    pr_number: int,
    failure_names: str = "",
    branch_name: str = "",
) -> str:
    """Render the Tier2 CI-repair terminal report.

    ``category`` is the milestone type that triggered the report
    (e.g. ``ci_repair_exhausted``); it is echoed for ops correlation only.
    """
    lines = [
        "## ⚠️ Autonomous CI repair exhausted",
        "",
        "The workflow stopped at the **merge** phase: the agent could not repair",
        "the PR's CI failures within its automatic repair budget, so it will not",
        "retry on its own.",
        "",
        f"- **Reason**: {reason}",
        f"- **Repair attempts**: {attempts}",
        f"- **PR**: #{pr_number}",
    ]
    if failure_names:
        lines.append(f"- **Failing checks**: {failure_names}")
    if branch_name:
        lines.append(f"- **Branch**: `{branch_name}`")
    lines += [
        "",
        "A maintainer can resume it by either:",
        "",
        "1. fixing the failing checks on the branch, rerunning CI, then",
        "   `POST /workflows/<id>/retry` (repair counters reset on retry); or",
        "2. closing the PR so the issue re-enters the queue.",
        "",
        f"_(terminal report category: `{category}`)_",
    ]
    return "\n".join(lines)
