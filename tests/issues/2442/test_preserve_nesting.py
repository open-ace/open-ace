"""Issue #2442: a failed preserve-dir removal must not let mv nest .claude.

If ``rm -rf "$preserve_claude_dir"`` fails (full tmpfs, permission), the
survivor stays and ``mv .claude preserve`` nests .claude inside it; the next
restore then hands Claude a mis-shaped tree and ``--resume`` silently loses
history. The ``_move_to_preserve`` helper removes the prior dir and fails
closed if it cannot, so the caller can abort (startup) or log (reclaim).

Behavioural: extracts the helper from the script and runs it for real.
"""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "openace-run-as.sh"


def _extract_function(name: str) -> str:
    src = SCRIPT.read_text(encoding="utf-8")
    m = re.search(
        rf"^(?P<ind>[ \t]*){re.escape(name)}\(\) \{{\n(?P<body>.*?)^(?P=ind)\}}$",
        src,
        re.MULTILINE | re.DOTALL,
    )
    assert m, f"{name}() not found in {SCRIPT.name}"
    return textwrap.dedent(m.group(0))


def _run(snippet: str) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", snippet],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_rm_failure_returns_nonzero_and_does_not_nest(tmp_path: Path):
    fn = _extract_function("_move_to_preserve")
    src = tmp_path / "src/.claude"
    src.mkdir(parents=True)
    (src / "f").write_text("x")
    dest_parent = tmp_path / "dp"
    dest_parent.mkdir()
    dest = dest_parent / "preserve"
    dest.mkdir()
    os.chmod(dest_parent, 0o500)  # no write → rm -rf of child fails
    try:
        r = _run(fn + f'\n_move_to_preserve "{src}" "{dest}"')
        assert r.returncode != 0, "rm failure must fail closed (non-zero)"
        assert not (dest / ".claude").exists(), "mv must not nest source into surviving dest"
        assert src.is_dir(), "source must be left in place when the move is skipped"
    finally:
        os.chmod(dest_parent, 0o700)


def test_successful_move_is_chmod_700(tmp_path: Path):
    fn = _extract_function("_move_to_preserve")
    src = tmp_path / "src/.claude"
    src.mkdir(parents=True)
    (src / "f").write_text("x")
    dest = tmp_path / "preserve"
    r = _run(fn + f'\n_move_to_preserve "{src}" "{dest}"; echo ok')
    assert r.returncode == 0, r.stderr
    assert (dest / "f").read_text() == "x"
    assert oct(os.stat(dest).st_mode & 0o777) == "0o700"


def test_second_move_clears_prior_dest_without_nesting(tmp_path: Path):
    fn = _extract_function("_move_to_preserve")
    s1 = tmp_path / "s1/.claude"
    s1.mkdir(parents=True)
    (s1 / "a").write_text("1")
    s2 = tmp_path / "s2/.claude"
    s2.mkdir(parents=True)
    (s2 / "b").write_text("2")
    dest = tmp_path / "preserve"
    assert _run(fn + f'\n_move_to_preserve "{s1}" "{dest}"; echo ok').returncode == 0
    r2 = _run(fn + f'\n_move_to_preserve "{s2}" "{dest}"; echo ok')
    assert r2.returncode == 0, r2.stderr
    assert (dest / "b").read_text() == "2"
    assert not (dest / ".claude").exists(), "second move must clear prior dest, not nest"
