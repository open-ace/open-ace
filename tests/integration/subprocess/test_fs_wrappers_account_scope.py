"""Behaviour of the #3410 account-scoped file wrappers.

``scripts/openace-write-as.sh`` (upload) and ``scripts/openace-rm.sh``
(delete-file) run as root through sudoers on package non-root multi-user
deployments. After the #3410 review they must touch the filesystem only AS the
target user (``runuser``): as root, a path component swapped for a symlink
made root write or delete another user's file.

These tests run a COPY of each script in which the hard-coded prefixes, lock
dir, audit log and minimum uid point at a temp tree, so the current
unprivileged user is an acceptable target. ``runuser`` is a PATH stub that
records every call and then runs it as the current user (a real privilege
drop needs root), so the tests pin that every filesystem operation goes
THROUGH runuser and the control flow around it — not the kernel DAC effect,
which the multi-user acceptance run covers for the Docker shape. GNU-only
tools a macOS host lacks (getent, flock, ``stat -c``, ``mv -T``) are stubbed
only when missing.
"""

from __future__ import annotations

import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
USER = pwd.getpwuid(os.getuid()).pw_name


def _modern_bash() -> str | None:
    """A bash >= 4.4: openace-rm expands an empty array under ``set -u``."""
    for candidate in (shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash"):
        if not candidate or not os.path.exists(candidate):
            continue
        out = subprocess.run(
            [candidate, "-c", 'echo "${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"'],
            capture_output=True,
            text=True,
        )
        try:
            major, minor = (int(x) for x in out.stdout.split())
        except ValueError:
            continue
        if (major, minor) >= (4, 4):
            return candidate
    return None


BASH = _modern_bash()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.security,
    pytest.mark.regression,
    pytest.mark.issue(3410),
    pytest.mark.skipif(os.name == "nt", reason="POSIX wrappers"),
    pytest.mark.skipif(BASH is None, reason="needs bash >= 4.4"),
    pytest.mark.skipif(
        re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", USER) is None,
        reason="current user name is not a valid wrapper target",
    ),
]

_RUNUSER_STUB = """#!/bin/sh
# Test stub: record the call, then run it as the current user.
[ "$1" = "-u" ] || { echo "runuser stub: expected -u" >&2; exit 99; }
shift 2
[ "$1" = "--" ] && shift
printf '%s\\n' "$*" >> "$RUNUSER_LOG"
if [ "$1" = "tee" ] && [ -n "$TEE_STARTED" ]; then : > "$TEE_STARTED"; fi
exec "$@"
"""


def _gnu(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True).returncode == 0
    except OSError:
        return False


def _install_stubs(bin_dir: Path) -> None:
    bin_dir.mkdir()
    py = sys.executable

    def stub(name: str, body: str) -> None:
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)

    stub("runuser", _RUNUSER_STUB)
    if not shutil.which("getent"):
        stub(
            "getent",
            f'#!/bin/sh\n[ "$1" = passwd ] || exit 2\nexec {py} -c '
            "'import pwd, sys\n"
            'try:\n    print(":".join(map(str, pwd.getpwnam(sys.argv[1]))))\n'
            'except KeyError:\n    sys.exit(2)\' "$2"\n',
        )
    if not shutil.which("flock"):
        stub("flock", "#!/bin/sh\nexit 0\n")
    if not _gnu(["stat", "-c", "%U", "/"]):
        # GNU `stat -c %U` does not follow a symlink at the final component.
        stub(
            "stat",
            f'#!/bin/sh\n[ "$1" = -c ] && [ "$2" = %U ] || exit 97\nexec {py} -c '
            "'import os, pwd, sys; print(pwd.getpwuid(os.lstat(sys.argv[1]).st_uid).pw_name)' "
            '"$3"\n',
        )
    if not _gnu(["mv", "--version"]):
        # `mv -fT src dst` is rename(2); onto a directory it fails.
        stub(
            "mv",
            f'#!/bin/sh\n[ "$1" = -fT ] || exit 98\nexec {py} -c '
            "'import os, sys\n"
            "try:\n    os.rename(sys.argv[1], sys.argv[2])\n"
            'except OSError as e:\n    sys.exit(f"mv: {e}")\' "$2" "$3"\n',
        )


def _script_copy(name: str, dest: Path, base: Path, work: Path) -> Path:
    text = (REPO_ROOT / "scripts" / name).read_text()
    replacements = {
        'ALLOWED_PREFIXES=("/workspace/" "/home/")': f'ALLOWED_PREFIXES=("{base}/")',
        'AUDIT_LOG="/app/logs/sudoers-audit.log"': f'AUDIT_LOG="{work}/audit.log"',
        "MIN_UID=1000": "MIN_UID=0",
    }
    if name == "openace-rm.sh":
        replacements.update(
            {
                'allowed_prefixes_local=("/workspace" "/home")': (
                    f'allowed_prefixes_local=("{base}")'
                ),
                'LOCK_DIR="/var/lock"': f'LOCK_DIR="{work}/lock"',
                # macOS temp dirs live under /private/var; the pattern list is
                # not what these tests are about.
                'FORBIDDEN_PATTERNS="/etc /root /var /.ssh /.gnupg /.config/openace"': (
                    'FORBIDDEN_PATTERNS="/etc /root /.ssh /.gnupg /.config/openace"'
                ),
            }
        )
    for old, new in replacements.items():
        assert old in text, f"{name} changed shape; update this test's copy: {old!r}"
        text = text.replace(old, new)
    dest.write_text(text)
    dest.chmod(0o755)
    return dest


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path.resolve()
    base = root / "base"
    (base / "alice").mkdir(parents=True)
    (root / "outside").mkdir()
    _install_stubs(root / "bin")
    env = dict(os.environ)
    env["PATH"] = f"{root / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["RUNUSER_LOG"] = str(root / "runuser.log")
    env.pop("TEE_STARTED", None)
    return {
        "root": root,
        "base": base,
        "home": base / "alice",
        "outside": root / "outside",
        "env": env,
        "log": root / "runuser.log",
        "write_as": _script_copy("openace-write-as.sh", root / "write-as.sh", base, root),
        "rm": _script_copy("openace-rm.sh", root / "rm.sh", base, root),
    }


def _calls(sb) -> list[str]:
    log = sb["log"]
    return log.read_text().splitlines() if log.exists() else []


def _temps(sb) -> list[Path]:
    return list(sb["base"].rglob(".openace-write-as.*"))


def _write_as(sb, target, payload=b"hello"):
    return subprocess.run(
        [BASH, str(sb["write_as"]), USER, str(target)],
        input=payload,
        capture_output=True,
        env=sb["env"],
        timeout=30,
    )


class TestWriteAs:
    def test_writes_through_runuser_and_leaves_no_temp_file(self, sandbox):
        target = sandbox["home"] / "f.txt"
        result = _write_as(sandbox, target)
        assert result.returncode == 0, result.stderr
        assert target.read_bytes() == b"hello"
        assert _temps(sandbox) == []
        commands = [line.split(" ", 1)[0] for line in _calls(sandbox)]
        for expected in ("test", "readlink", "mktemp", "tee", "mv", "rm"):
            assert expected in commands, (expected, commands)

    def test_paths_outside_the_prefix_are_refused_before_any_probe(self, sandbox):
        """Finding 7: the refusal code must not reveal what an outside path is."""
        outside = sandbox["outside"]
        (outside / "plain").write_text("x")
        (outside / "dir").mkdir()
        os.symlink(str(outside / "plain"), str(outside / "link"))
        codes = {
            name: _write_as(sandbox, outside / name).returncode for name in ("plain", "dir", "link")
        }
        assert codes == {"plain": 2, "dir": 2, "link": 2}
        assert _calls(sandbox) == []  # nothing touched the filesystem, as anyone

    def test_symlink_and_directory_targets_keep_their_codes(self, sandbox):
        home, outside = sandbox["home"], sandbox["outside"]
        (outside / "victim.txt").write_text("VICTIM")
        os.symlink(str(outside / "victim.txt"), str(home / "link.txt"))
        (home / "subdir").mkdir()
        os.symlink(str(home / "subdir"), str(home / "dirlink"))
        assert _write_as(sandbox, home / "link.txt").returncode == 5
        assert _write_as(sandbox, home / "subdir").returncode == 6
        assert _write_as(sandbox, home / "dirlink").returncode == 5
        assert (outside / "victim.txt").read_text() == "VICTIM"
        assert list((home / "subdir").iterdir()) == []

    def test_sigterm_stops_before_mv_and_removes_the_temp_file(self, sandbox):
        """Finding 8: the TERM trap used to clean up and then keep running."""
        target = sandbox["home"] / "late.txt"
        started = sandbox["root"] / "tee.started"
        env = dict(sandbox["env"], TEE_STARTED=str(started))
        proc = subprocess.Popen(
            [BASH, str(sandbox["write_as"]), USER, str(target)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        try:
            deadline = time.monotonic() + 15
            while not started.exists():
                assert proc.poll() is None, "wrapper exited before reading the body"
                assert time.monotonic() < deadline, "tee never started"
                time.sleep(0.05)
            proc.stdin.write(b"partial")
            proc.stdin.flush()
            # bash runs the trap once its foreground child (tee) has exited.
            proc.send_signal(signal.SIGTERM)
            time.sleep(0.2)
            proc.stdin.close()
            rc = proc.wait(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        assert rc == 143
        assert not target.exists()
        assert _temps(sandbox) == []
        assert not any(line.startswith("mv ") for line in _calls(sandbox))


class TestRm:
    def _rm(self, sb, target):
        return subprocess.run(
            [BASH, str(sb["rm"]), USER, str(target)],
            capture_output=True,
            text=True,
            env=sb["env"],
            timeout=30,
        )

    def test_every_probe_and_the_rm_go_through_runuser(self, sandbox):
        target = sandbox["home"] / "trash.txt"
        target.write_text("x")
        result = self._rm(sandbox, target)
        assert result.returncode == 0, result.stderr
        assert not target.exists()
        calls = _calls(sandbox)
        assert f"rm -- {target}" in calls
        assert f"stat -c %U {target}" in calls
        assert f"readlink -f {target}" in calls
        assert f"test -e {target}" in calls

    def test_a_link_out_of_the_prefix_is_refused(self, sandbox):
        victim = sandbox["outside"] / "victim.txt"
        victim.write_text("VICTIM")
        link = sandbox["home"] / "link.txt"
        os.symlink(str(victim), str(link))
        result = self._rm(sandbox, link)
        assert result.returncode == 2, result.stderr
        assert victim.read_text() == "VICTIM"
        assert link.is_symlink()
        assert not any(line.startswith("rm ") for line in _calls(sandbox))
