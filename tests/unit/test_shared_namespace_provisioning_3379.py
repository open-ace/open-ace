"""
Shared-namespace root provisioning in the Docker entrypoint (Issue #3379).

The multi-user acceptance run exposed a fresh-deployment gap: nothing in the
product created ``<base>/shared``, so ``POST /api/projects`` with
``create_dir: true`` ran ``sudo -u <user> mkdir -p`` against a root:root 0755
parent and every user's first shared-project creation EACCESed (HTTP 403).
The #3376 first-class ``<base>/shared/<name>`` namespace needs its root to
pre-exist, group-writable by ``openace-shared`` with setgid.

These tests follow the text-assertion convention of
``tests/unit/test_multi_user_config_2235.py`` (the entrypoint is a root-gated
bash script; its provisioning logic was verified functionally in a Linux
container during development — member user creates inside the namespace with
the group inherited, a non-member is denied, reruns are idempotent and never
touch existing contents).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent

pytestmark = [pytest.mark.regression, pytest.mark.issue(3379)]

ENTRYPOINT = ROOT / "docker-entrypoint.sh"


def _entrypoint_content() -> str:
    assert ENTRYPOINT.exists(), "docker-entrypoint.sh must exist"
    return ENTRYPOINT.read_text(encoding="utf-8")


def _multi_user_block(content: str) -> str:
    """The multi-user setup block (group creation → /home perms)."""
    start = content.index('SHARED_GROUP="openace-shared"')
    end = content.index("# Fix /home directory permissions", start)
    return content[start:end]


class TestSharedNamespaceProvisioning:
    def test_shared_root_provisioned_in_multi_user_block(self):
        """<base>/shared is created group-writable (setgid) for the shared
        project namespace, inside the multi-user block."""
        block = _multi_user_block(_entrypoint_content())
        assert (
            'mkdir -p "$_base_dir/shared"' in block
        ), "the shared namespace root must be provisioned in the multi-user block"
        assert (
            'chgrp "$SHARED_GROUP" "$_base_dir/shared"' in block
        ), "the root must be group-owned by the shared project group"
        assert 'chmod 2775 "$_base_dir/shared"' in block, (
            "the root must be group-writable with setgid so shared files "
            "inherit the openace-shared group"
        )

    def test_provisioning_is_idempotent_and_non_recursive(self):
        """Only the root itself is touched — chgrp/chmod apply to
        ``$_base_dir/shared`` alone, never ``-R``: user contents inside the
        namespace survive restarts untouched."""
        content = _entrypoint_content()
        assert not re.search(
            r'chgrp\s+-R\s+"\$SHARED_GROUP"\s+"\$_base_dir/shared"', content
        ), "provisioning must not recursively rewrite ownership of user data"
        assert not re.search(
            r'chmod\s+-R\s+2775\s+"\$_base_dir/shared"', content
        ), "provisioning must not recursively rewrite permissions of user data"

    def test_collision_guard_skips_real_shared_account(self):
        """A real account named `shared` (or a non-root-owned path) must be
        skipped with a warning, not re-chowned — ownership ping-pong with
        the app's home provisioning would briefly group-open a private
        home (review MINOR)."""
        block = _multi_user_block(_entrypoint_content())
        assert re.search(
            r'id "shared" &>/dev/null', block
        ), "the provisioning must probe for a real account named shared"
        assert re.search(r"continue\n", block), "the guard must skip, not proceed"

    def test_workspace_base_dir_supports_comma_list(self):
        """WORKSPACE_BASE_DIR may be a comma-separated list (the fs layer's
        multi-root semantics) — the provisioning iterates instead of creating
        a literal ``a,b`` directory, and trims whitespace around each base."""
        block = _multi_user_block(_entrypoint_content())
        assert (
            "IFS=',' read -r -a _workspace_base_dirs" in block
        ), "the base dir must be split on commas before use"
        # pure-bash trim (review NIT: `echo | xargs` aborts under set -e
        # when a base dir contains a quote character)
        assert (
            "${_base_dir%%[![:space:]]*}" in block
        ), "each base must be leading-trimmed (pure-bash parameter expansion)"
        assert (
            "${_base_dir##*[![:space:]]}" in block
        ), "each base must be trailing-trimmed (pure-bash parameter expansion)"

    def test_provisioning_runs_after_group_creation(self):
        """The chgrp targets $SHARED_GROUP, so the block must define the
        group before provisioning (ordering guarantee)."""
        block = _multi_user_block(_entrypoint_content())
        assert block.index('SHARED_GROUP="openace-shared"') < block.index(
            'mkdir -p "$_base_dir/shared"'
        ), "shared root provisioning must come after the shared group exists"
        # Pin the actual groupadd call too: the assignment alone would let a
        # reordered groupadd (chgrp -> "invalid group" under set -e) slip
        # through (review MINOR — mutation-verified).
        assert block.index("groupadd") < block.index(
            'mkdir -p "$_base_dir/shared"'
        ), "the shared group must be CREATED before provisioning references it"
