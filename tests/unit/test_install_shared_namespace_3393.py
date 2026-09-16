"""Issue #3393: package-method install.sh provisions the shared-namespace root.

The Docker entrypoint provisions ``<base>/shared`` at boot (#3379), and the
API provisions it on demand (``ensure_shared_namespace_root``) — but the
package-method multi-user install (base dir ``/home`` by default) never did,
which is exactly the "entrypoint didn't run" shape the on-demand path was
built for. The installer now provisions the root at install/upgrade time so
the very first shared-project creation works without relying on the app-side
fallback.

The block is tested FUNCTIONALLY (the repo's established convention for shell
provisioning — see tests/unit/test_shared_namespace_provisioning_3379.py):
``provision_shared_namespace`` is extracted from install.sh between its
SHARED_NAMESPACE_PROVISION markers and executed with ``as_root``,
``groupadd``/``mkdir``/``chgrp``/``chmod``/``id``/``stat`` and the print
helpers replaced by recording stubs; the executed command sequences are then
asserted per scenario. A textual ordering test pins the call into the
multi-user section (the functional scenarios cannot see the surrounding file).
"""

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(3393)]

INSTALL_SH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "install-central"
    / "package-method"
    / "install.sh"
)

BEGIN_MARKER = "# SHARED_NAMESPACE_PROVISION_BEGIN"
END_MARKER = "# SHARED_NAMESPACE_PROVISION_END"

# Executes the extracted function with stubs; one line per stub invocation.
# FAIL injects a failure for the named command (shared-root steps only).
# The `shared` account probe and the stat owner are steered via env vars so
# the scenarios never touch the host's real accounts.
_HARNESS = """
CALLS_FILE="$TEST_TMP/calls"
touch "$CALLS_FILE"
as_root() {
    echo "as_root $*" >> "$CALLS_FILE"
    case "$1" in
        groupadd) [ "$FAIL" = "groupadd" ] && return 1; return 0 ;;
        mkdir)    [ "$FAIL" = "mkdir" ] && return 1; return 0 ;;
        chgrp)    [ "$FAIL" = "chgrp" ] && return 1; return 0 ;;
        chmod)    [ "$FAIL" = "chmod" ] && return 1; return 0 ;;
    esac
    return 0
}
groupadd() { echo "groupadd $*" >> "$CALLS_FILE"; [ "$FAIL" = "groupadd" ] && return 1; return 0; }
mkdir()    { echo "mkdir $*" >> "$CALLS_FILE"; [ "$FAIL" = "mkdir" ] && return 1; return 0; }
chgrp()    { echo "chgrp $*" >> "$CALLS_FILE"; [ "$FAIL" = "chgrp" ] && return 1; return 0; }
chmod()    { echo "chmod $*" >> "$CALLS_FILE"; [ "$FAIL" = "chmod" ] && return 1; return 0; }
id()   { echo "id $*" >> "$CALLS_FILE"; [ -n "$ID_SHARED_OK" ] && [ "$1" = "shared" ] && return 0; return 1; }
stat() { echo "stat $*" >> "$CALLS_FILE"; if [ -n "$STAT_OWNER" ]; then echo "$STAT_OWNER"; else echo "root"; fi; return 0; }
print_warning() { echo "WARNING: $*" >> "$CALLS_FILE"; }
print_success() { echo "OK: $*" >> "$CALLS_FILE"; }
print_error()   { echo "ERROR: $*" >> "$CALLS_FILE"; }
set -e
"""


def _extract_block() -> str:
    content = INSTALL_SH.read_text(encoding="utf-8")
    start = content.index(BEGIN_MARKER)
    end = content.index(END_MARKER, start) + len(END_MARKER)
    block = content[start:end]
    assert "provision_shared_namespace()" in block
    return block


def _run_block(
    workspace_base_dir: str,
    *,
    id_shared_ok: bool = False,
    stat_owner: str = "",
    fail: str = "",
    precreate_shared: bool = False,
) -> str:
    """Run the extracted provisioning function with stubs; returns rc + log.

    When workspace_base_dir is the literal "TMP", a throwaway directory is
    used as the base (so scenarios can pre-create <base>/shared and exercise
    the -e branch of the collision guard)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ws = str(Path(tmp) / "base") if workspace_base_dir == "TMP" else workspace_base_dir
        if precreate_shared:
            Path(ws).mkdir(parents=True, exist_ok=True)
            (Path(ws) / "shared").mkdir(exist_ok=True)
        script = _HARNESS + _extract_block() + "\nprovision_shared_namespace\n"
        env = {
            "PATH": "/usr/bin:/bin",
            "TEST_TMP": tmp,
            "WORKSPACE_BASE_DIR": ws,
            "FAIL": fail,
        }
        if id_shared_ok:
            env["ID_SHARED_OK"] = "1"
        if stat_owner:
            env["STAT_OWNER"] = stat_owner
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        calls_path = Path(tmp) / "calls"
        calls = calls_path.read_text() if calls_path.exists() else ""
        return f"rc={proc.returncode}\n{calls}"


class TestInstallShSharedNamespaceProvisioning:
    def test_provisions_root_owned_group_sticky_mode(self):
        log = _run_block("/home")
        assert "groupadd -f openace-shared" in log
        assert "mkdir -p /home/shared" in log
        assert "chgrp openace-shared /home/shared" in log
        assert "chmod 3770 /home/shared" in log, (
            "sticky+setgid with no others bits — must match the entrypoint and "
            "the app-side ensure_shared_namespace_root"
        )
        assert "OK: provisioned shared namespace root" in log
        assert log.startswith("rc=0")

    def test_comma_list_iterates_and_trims(self):
        log = _run_block("  /data , /srv/ws/ ,")
        assert "mkdir -p /data/shared" in log and "mkdir -p /srv/ws/shared" in log
        assert log.count("chmod 3770") == 2, "exactly two bases provisioned"
        assert not any(
            ", /" in ln or "/," in ln for ln in log.splitlines()
        ), "no literal comma directory"

    def test_defaults_to_home_when_unset(self):
        log = _run_block("", fail="")
        # WORKSPACE_BASE_DIR="" -> ${WORKSPACE_BASE_DIR:-/home} default
        assert "mkdir -p /home/shared" in log

    def test_skips_when_real_shared_account_exists(self):
        log = _run_block("/home", id_shared_ok=True)
        assert "id shared" in log, "the guard probe must run"
        assert "chmod 3770" not in log, "guard must skip provisioning on collision"
        assert "WARNING" in log

    def test_skips_when_existing_path_not_root_owned(self):
        log = _run_block("TMP", stat_owner="someoneelse", precreate_shared=True)
        assert (
            "chgrp openace-shared" not in log
        ), "a non-root-owned existing root must not be re-provisioned"
        assert "WARNING" in log

    def test_reconverges_existing_root_owned_root(self):
        """A root-owned existing root (stat reports root) is idempotently
        re-provisioned — mkdir -p no-ops and chgrp/chmod fix drift, the
        entrypoint's boot convergence semantics."""
        log = _run_block("TMP", stat_owner="root", precreate_shared=True)
        assert "chgrp openace-shared" in log
        assert "chmod 3770" in log

    def test_failure_degrades_to_warning_not_abort(self):
        for fail in ("groupadd", "mkdir", "chgrp", "chmod"):
            log = _run_block("/home", fail=fail)
            assert log.startswith(
                "rc=0"
            ), f"{fail} failure must not abort the installer (degrade to warning)"
            assert "WARNING" in log

    def test_call_sits_inside_multi_user_section(self):
        """Textual ordering by necessity (the functional scenarios cannot see
        the surrounding file): the call must live in the multi-user block,
        after WORKSPACE_MULTI_USER_MODE is decided and before the sudoers
        configuration it precedes in the flow."""
        content = INSTALL_SH.read_text(encoding="utf-8")
        multi_user_block = content.index('if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then')
        call_site = content.index("provision_shared_namespace\n", multi_user_block)
        assert call_site > multi_user_block
        # And the function definition precedes the call site.
        assert content.index("provision_shared_namespace()") < call_site
