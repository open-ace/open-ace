"""The OPENACE_UTILS sudoers rule must permit cross-user runas (#2280).

github_ops runs the orchestrator's server-side git/gh as ``sudo -u
<system_account>`` (Issue #1395: the openace service user drives a repo owned
by another user under a 0700 home). ``test_github_ops_sudo.py`` pins that
command shape (``["sudo","-u","<account>","gh"]``), so the generated sudoers
MUST authorize non-root runas for git/gh.

Autonomous commit 26508b077 (Issue #2181) tightened ``install.sh`` to emit
``ALL=(root) NOPASSWD: OPENACE_UTILS`` on the assumption that every cross-user
operation goes through ``openace-run-as --isolated``. That assumption is false
for github_ops, which cannot use run-as:

  1. run-as rejects ``target_user == project_owner`` (the common case: worktree
     owned by the system_account) — exit 67.
  2. run-as starts the child with ``env -i`` → strips ``GH_TOKEN``/git
     credentials → gh and authenticated git push break.
  3. run-as is a credentialless-account, worktree-only-ACL model for untrusted
     agent CLIs — not a run-as-repo-owner runner for trusted server git.

So ``(root)``-only is unrecoverable for github_ops. Once an install ran the
regressed ``install.sh`` (prod 2026-08-04 10:21), every
``system_account != openace`` workflow failed at preparation ``git fetch``.
"""

from __future__ import annotations

import re
from pathlib import Path

INSTALL_SH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "install-central"
    / "package-method"
    / "install.sh"
)


def _openace_utils_runas_targets() -> list[str]:
    """Runas targets of every OPENACE_UTILS *user-rule* in install.sh.

    Matches ``<user> ALL=(<target>) NOPASSWD: OPENACE_UTILS`` (the user spec
    that references the alias), NOT the ``Cmnd_Alias OPENACE_UTILS = ...``
    definition line.
    """
    text = INSTALL_SH.read_text()
    return re.findall(
        r"ALL=\(([A-Za-z_][A-Za-z0-9_-]*|\*)\)\s*NOPASSWD:\s*OPENACE_UTILS\b",
        text,
    )


def test_openace_utils_permits_cross_user_runas():
    """OPENACE_UTILS must allow non-root runas so github_ops can sudo -u <account>.

    Asserting ``ALL`` (not ``root``) because github_ops's cross-user git/gh has
    no run-as alternative (#2280). A ``(root)``-only rule fails every
    system_account!=openace workflow at the first ``git fetch``.
    """
    targets = _openace_utils_runas_targets()
    assert targets, "No OPENACE_UTILS user-rule found in install.sh (did the rule move?)"
    assert "ALL" in targets, (
        f"OPENACE_UTILS runas tightened to {targets!r}; github_ops needs "
        "cross-user (ALL) for `sudo -u <system_account>` (#2280, #1395)."
    )


def test_agent_cli_isolation_hardening_intact():
    """Reverting OPENACE_UTILS runas must NOT undo #2181's agent-CLI hardening.

    Agent CLIs still launch only through ``openace-run-as --isolated``; the
    broad standalone AI-CLI wildcard rule (``NOPASSWD: OPENACE_CLI``) stays
    removed. Only the OPENACE_UTILS *runas target* is restored.
    """
    text = INSTALL_SH.read_text()
    assert "openace-run-as --isolated" in text, "run-as --isolated launcher rule missing"
    assert "NOPASSWD: OPENACE_CLI" not in text, "broad OPENACE_CLI wildcard rule reintroduced"
