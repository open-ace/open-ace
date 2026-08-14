"""Regression guard for the V1 launcher cross-run .git integrity false positive.

Issue #2529: ``scripts/openace-run-as.sh`` keeps a per-isolation-account
signature registry (``/run/openace-agent-git-signature-<uid>``) whose first
line is the project dir, second line the ``git_entry_signature`` of the
protected ``.git`` entry, and third line an ACL snapshot. The orchestrator's
worktree self-heal (``git worktree add``) can rebuild a worktree between two
runs that share an isolation key (a resumed session, ``task_id == session_id``),
giving the ``.git`` gitfile a brand-new inode while leaving its content
byte-identical (the gitdir pointer still targets the same
``worktrees/<uuid>``). The cross-run integrity check used to treat that inode
delta as tampering and ``exit 68`` (``repo_integrity_violation``) — a false
positive, since the agent never touched ``.git``.

The fix adds ``signatures_differ_only_by_inode`` and an extra ``elif`` in the
cross-run check that tolerates an inode-only delta. A push-redirect attack
must change the ``.git`` content (gitdir pointer -> sha256 changes) or swap the
entry type (file -> link/dir), both of which still mismatch and trip
``exit 68``. The within-run check (the agent is live, its own rebuild counts as
tampering) is deliberately untouched.

These are pure-function tests: they inline the helper bash and assert the
tolerance matrix without needing sudo, ``/run`` containment, or Linux ACLs, so
they are the locally-verifiable anchor (the launcher integration tests are
Linux/CI-gated). They follow the extraction harness pattern established by
``tests/issues/1395/test_cross_user_permissions.py``.
"""

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2529)]

WRAPPER = Path("scripts/openace-run-as.sh")


def _extract_helpers(*names: str) -> str:
    """Extract the named top-level bash helper functions from the launcher.

    The launcher defines these helpers indented four spaces inside the
    ``--isolated`` branch. We pull each by name so the extraction does not
    depend on a brittle brace count (which would need bumping every time a
    helper is added between two existing ones).
    """
    source = WRAPPER.read_text(encoding="utf-8")
    out = []
    for name in names:
        needle = f"    {name}() {{"
        start = source.index(needle)
        # Each helper ends with a line that is exactly "    }".
        end = source.index("\n    }\n", start) + len("\n    }")
        out.append(source[start:end])
    return "\n".join(out)


def _bash_signature_differ(sig_a: str, sig_b: str) -> bool:
    """Run ``signatures_differ_only_by_inode`` over two literal signatures.

    Returns True when the helper reports the two differ only by inode.
    """
    helpers = _extract_helpers(
        "normalize_group_class_signature",
        "signatures_differ_only_by_inode",
    )
    # Pass the signatures via bash variables to avoid quoting/quoting-in-awk
    # pitfalls; the helper reads them as $1/$2.
    script = (
        f"{helpers}\n"
        f'a="$1"; b="$2"; '
        f'if signatures_differ_only_by_inode "$a" "$b"; then echo yes; else echo no; fi'
    )
    result = subprocess.run(
        ["bash", "-c", script, "differ", sig_a, sig_b],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"helper invocation failed: rc={result.returncode}\n"
        f"stderr={result.stderr}\nscript=\n{script}"
    )
    return result.stdout.strip() == "yes"


# ── inode-only tolerance (the core regression) ──────────────────────────


class TestSignaturesDifferOnlyByInode:
    """Pin the tolerance matrix of ``signatures_differ_only_by_inode``.

    The signature layout for a .git gitfile is
    ``file:<dev>:<inode>:<mode>:<user>:<group>:<sha256>``. Only the inode may
    differ between a pre-rebuild and post-rebuild gitfile; every other field
    changing is real tampering and must be rejected.
    """

    BASE = "file:2049:11111:664:rhuang:rhuang:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_inode_only_difference_is_tolerated(self):
        # Two runs sharing an isolation key: the worktree self-heal recreated
        # the .git gitfile with a new inode but identical content/type/owner.
        rebuilt = self.BASE.replace(":11111:", ":99999:")
        assert _bash_signature_differ(self.BASE, rebuilt) is True

    def test_content_change_is_rejected(self):
        # A push-redirect attack changes the gitdir pointer -> sha256 changes.
        tampered = self.BASE.replace(":rhuang:aaaaaaaa", ":rhuang:bbbbbbbb")
        assert _bash_signature_differ(self.BASE, tampered) is False

    def test_type_change_to_link_is_rejected(self):
        # Swapping a gitfile for a symlink is structural, never tolerated.
        link_sig = "link:/somewhere/.git"
        assert _bash_signature_differ(self.BASE, link_sig) is False

    def test_type_change_from_link_is_rejected(self):
        # Symmetric: a link signature vs a file signature must mismatch.
        assert _bash_signature_differ("link:/x/.git", self.BASE) is False

    def test_device_change_is_rejected(self):
        # Different filesystem (dev) -> not the same entry at all.
        moved = self.BASE.replace(":2049:", ":2050:")
        assert _bash_signature_differ(self.BASE, moved) is False

    def test_mode_change_is_rejected(self):
        # A permission change (other than the ACL-mask group-class digit, which
        # normalize_group_class_signature already folds) is tampering.
        chmod = self.BASE.replace(":664:", ":660:")
        assert _bash_signature_differ(self.BASE, chmod) is False

    def test_owner_change_is_rejected(self):
        # Different owning user -> reject.
        other_owner = self.BASE.replace(":rhuang:rhuang:", ":evil:evil:")
        assert _bash_signature_differ(self.BASE, other_owner) is False

    def test_group_change_is_rejected(self):
        # Different owning group -> reject.
        other_group = self.BASE.replace(":rhuang:rhuang:", ":rhuang:wheel:")
        assert _bash_signature_differ(self.BASE, other_group) is False

    def test_mask_only_mode_change_is_tolerated(self):
        # The ACL mask is reflected in the group-class digit; that is already
        # normalized away, so a 664 vs 674 delta (mask churn) must compare
        # equal just like the existing within-run restore does.
        mask_churned = self.BASE.replace(":664:", ":674:")
        assert _bash_signature_differ(self.BASE, mask_churned) is True


# ── cross-run wiring: the inode-tolerant elif exists and is ordered ──────


class TestCrossRunInodeTolerantElif:
    """Structural guards that the cross-run check gained the inode-tolerant
    branch and that it sits between the verify-success and exit-68 arms — and
    that the within-run check (the live-agent arm) was NOT loosened."""

    def test_helper_is_defined(self):
        source = WRAPPER.read_text(encoding="utf-8")
        assert "signatures_differ_only_by_inode() {" in source

    def test_cross_run_uses_inode_tolerant_elif(self):
        source = WRAPPER.read_text(encoding="utf-8")
        cross_run_block = source.index(
            'if [ -n "$previous_signature_project" ] && [ -n "$previous_git_signature" ]; then'
        )
        section = source[cross_run_block:]

        verify_arm = (
            'verify_and_restore_git_entry \\\n                "$previous_signature_project"'
        )
        assert verify_arm in section
        inode_arm = 'signatures_differ_only_by_inode "$previous_git_signature"'
        assert inode_arm in section
        violation = "OPENACE_REPO_INTEGRITY_VIOLATION: .git entry changed during interrupted agent execution"

        verify_pos = section.index(verify_arm)
        inode_pos = section.index(inode_arm)
        violation_pos = section.index(violation)
        # The inode-tolerant elif must sit AFTER the verify-success arm and
        # BEFORE the terminal exit-68 arm.
        assert verify_pos < inode_pos < violation_pos

    def test_within_run_check_is_not_loosened(self):
        # The live-agent arm (agent session in progress) must remain strict:
        # it must still call verify_and_restore_git_entry directly and must NOT
        # reference signatures_differ_only_by_inode.
        source = WRAPPER.read_text(encoding="utf-8")
        agent_arm = 'if ! verify_and_restore_git_entry "$project_dir" "$git_entry_before" "$git_acl_before"; then'
        assert agent_arm in source
        agent_pos = source.index(agent_arm)
        # Grab the slice from the agent arm to its rm -f and assert no inode
        # tolerance sneaked in there.
        agent_section = source[agent_pos : source.index('rm -f "$signature_registry"', agent_pos)]
        assert "signatures_differ_only_by_inode" not in agent_section
        assert "during agent execution" in agent_section  # distinct message
