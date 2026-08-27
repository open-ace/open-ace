"""Cross-user sudoers runas for github_ops git/gh (Issue #2280).

github_ops runs the orchestrator's server-side git/gh as ``sudo -u
<system_account>`` (Issue #1395: the openace service user drives a repo owned
by another user under a 0700 home). ``sudo -u`` only matches a sudoers rule
whose runas target covers that account. Autonomous commit 26508b077 (Issue
#2181) had tightened the OPENACE_UTILS rule to ``(root)`` on the assumption
that every cross-user operation goes through ``openace-run-as --isolated``.
That assumption is false for github_ops, which cannot use run-as:

  1. run-as rejects ``target_user == project_owner`` (the common case: worktree
     owned by the system_account) — exit 67.
  2. run-as starts the child with ``env -i`` → strips ``GH_TOKEN``/git
     credentials → gh and authenticated git push break.
  3. run-as is a credentialless-account, worktree-only-ACL model for untrusted
     agent CLIs — not a run-as-repo-owner runner for trusted server git.

So ``(root)``-only was unrecoverable: once an install ran the regressed
``install.sh`` (prod 2026-08-04 10:21), every ``system_account != openace``
workflow failed at preparation ``git fetch``.

Since #2334 (with the #2650 wrapper delegation), where that cross-user runas
permission lives has shifted: git/gh moved OUT of OPENACE_UTILS into the
dedicated GIT_SAFE/GH_SAFE aliases (executed through the root-owned
openace-git/openace-gh validating wrappers), OPENACE_UTILS was tightened to
the low-risk read-only set (test/ls/stat/id/find), and cross-user mkdir moved
to MKDIR_SAFE (#2674). The #2280 cross-user ``(ALL)`` runas therefore now
lives on GIT_SAFE/GH_SAFE for git/gh, while OPENACE_UTILS keeps ``(ALL)`` for
the cross-user ``test``/``ls``/``stat`` probes (fs.py/projects.py/
autonomous.py).

Migrated from tests/issues/2280/test_sudoers_cross_user_git.py and realigned
to the current post-#2334 install.sh semantics (cross-checked against
tests/unit/test_sudoers_hardening.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2280), pytest.mark.security]

INSTALL_SH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "install-central"
    / "package-method"
    / "install.sh"
)

# The post-#2334 alias shapes pinned here (the wrapper-only GIT_SAFE/GH_SAFE
# invariant is additionally drift-locked in test_sudoers_hardening.py).
GIT_SAFE_ALIAS = "Cmnd_Alias GIT_SAFE = /usr/local/bin/openace-git *"
GH_SAFE_ALIAS = "Cmnd_Alias GH_SAFE = /usr/local/bin/openace-gh *"
OPENACE_UTILS_ALIAS = (
    "Cmnd_Alias OPENACE_UTILS = /usr/bin/test *, /usr/bin/ls *, "
    "/usr/bin/stat *, /usr/bin/id *, /usr/bin/find *"
)


def _runas_targets(text: str, alias: str) -> list[str]:
    """Runas targets of every user rule referencing ``alias``.

    Matches ``<user> ALL=(<target>) NOPASSWD: <alias>`` (the user spec that
    references the alias), NOT the ``Cmnd_Alias <alias> = ...`` definition
    line.
    """
    return re.findall(
        rf"ALL=\(([A-Za-z_][A-Za-z0-9_-]*|\*)\)\s*NOPASSWD:\s*{alias}\b",
        text,
    )


def test_cross_user_runas_lives_in_git_safe_and_gh_safe():
    """git/gh cross-user runas must be carried by GIT_SAFE/GH_SAFE under (ALL).

    github_ops invokes ``sudo -u <system_account>`` git/gh through the
    validating openace-git/openace-gh wrappers, so a ``(root)``-only rule on
    those aliases fails every system_account!=openace workflow at the first
    ``git fetch`` (#2280, #1395). Since #2334 git/gh are no longer in
    OPENACE_UTILS, which is now the read-only probe set and keeps ``(ALL)``
    only for the cross-user test/ls/stat probes.
    """
    text = INSTALL_SH.read_text()

    # The specific alias lines: git/gh live in the wrapper-only safe aliases…
    assert GIT_SAFE_ALIAS in text, "GIT_SAFE wrapper-only alias missing from install.sh"
    assert GH_SAFE_ALIAS in text, "GH_SAFE wrapper-only alias missing from install.sh"
    # …and OPENACE_UTILS is the tightened read-only set with no git/gh entries.
    assert OPENACE_UTILS_ALIAS in text, (
        "OPENACE_UTILS alias drifted from the #2334 read-only set; git/gh "
        f"cross-user commands must stay in GIT_SAFE/GH_SAFE, not here (expected {OPENACE_UTILS_ALIAS!r})"
    )

    for alias in ("GIT_SAFE", "GH_SAFE"):
        targets = _runas_targets(text, alias)
        assert targets, f"No {alias} user-rule found in install.sh (did the rule move?)"
        assert "ALL" in targets, (
            f"{alias} runas tightened to {targets!r}; github_ops needs cross-user "
            "(ALL) for `sudo -u <system_account>` git/gh (#2280, #1395, #2334)."
        )

    # OPENACE_UTILS keeps (ALL) for the cross-user test/ls/stat probes
    # (fs.py/projects.py/autonomous.py); a (root)-only rule would break them
    # the same way it broke git fetch in #2280.
    utils_targets = _runas_targets(text, "OPENACE_UTILS")
    assert utils_targets, "No OPENACE_UTILS user-rule found in install.sh (did the rule move?)"
    assert "ALL" in utils_targets, (
        f"OPENACE_UTILS runas tightened to {utils_targets!r}; the cross-user "
        "test/ls/stat probes need (ALL) runas (#2280, #2334)."
    )


def test_agent_cli_isolation_hardening_intact():
    """The #2280 cross-user runas must NOT undo #2181's agent-CLI hardening.

    Agent CLIs still launch only through ``openace-run-as --isolated`` under
    ``(root)`` runas; the broad standalone AI-CLI wildcard rule
    (``NOPASSWD: OPENACE_CLI``) stays removed. Only the cross-user runas
    targets of the safe aliases are (ALL).
    """
    text = INSTALL_SH.read_text()
    assert "openace-run-as --isolated" in text, "run-as --isolated launcher rule missing"
    assert "NOPASSWD: OPENACE_CLI" not in text, "broad OPENACE_CLI wildcard rule reintroduced"
    # The launcher itself stays a (root)-runas rule — never cross-user.
    assert (
        "ALL=(root) NOPASSWD: $wrapper_path --isolated *" in text
    ), "openace-run-as --isolated launcher must remain a (root)-runas rule"
    assert (
        "ALL=(ALL) NOPASSWD: $wrapper_path" not in text
    ), "openace-run-as launcher must not be promoted to (ALL) runas"
    assert (
        "ALL=(ALL) NOPASSWD: /usr/local/bin/openace-run-as" not in text
    ), "openace-run-as launcher must not be promoted to (ALL) runas"
