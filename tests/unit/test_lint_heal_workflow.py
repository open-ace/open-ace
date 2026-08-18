"""Tests for the Lint Heal workflow's local-hook skip derivation.

The heal job holds contents:write + actions:write, so it must never run the
PR branch's local pre-commit hooks. Its skip list is derived at runtime from
the pinned ``.pre-commit-config.yaml`` (fail closed). That derivation is the
one piece of the otherwise un-runnable ``workflow_run`` workflow that can be
exercised offline, and a config restructure that silently narrows the list
would re-run PR-branch hook scripts under the write token — the exact hole
the pin closes.

These tests extract the python snippet from the workflow file itself (no
copy to drift) and run it against the repository's real config, pinning the
parse assumption: every ``repo: local`` hook id plus ``no-commit-to-branch``
comes out, and nothing else.
"""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "lint-heal.yml"
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"

pytestmark = [pytest.mark.regression, pytest.mark.issue(2721)]

_HEREDOC = re.compile(r"<<'PY'\n(.*?)\nPY\n", re.DOTALL)


def _derivation_snippet() -> str:
    """Pull the skip-derivation python heredoc out of the workflow."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    run_block = next(
        step["run"] for step in workflow["jobs"]["heal"]["steps"] if step.get("id") == "derive"
    )
    match = _HEREDOC.search(run_block)
    assert match, "skip-derivation heredoc not found in lint-heal.yml derive step"
    return match.group(1)


def _run_snippet(snippet: str, cwd: Path):
    return subprocess.run(
        ["python3", "-"],
        input=snippet,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestHealSkipDerivation:
    def test_snippet_covers_every_local_hook(self):
        """The workflow's own snippet, run against the real config, returns
        exactly all repo:local hook ids plus no-commit-to-branch."""
        result = _run_snippet(_derivation_snippet(), REPO_ROOT)
        assert result.returncode == 0, result.stderr

        config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
        expected = [
            hook["id"]
            for repo in config["repos"]
            if repo.get("repo") == "local"
            for hook in repo["hooks"]
        ]
        assert expected, "fixture repo has no local hooks — test premise stale"
        assert result.stdout.strip().split(",") == [*expected, "no-commit-to-branch"]

    def test_snippet_fails_closed_without_local_hooks(self, tmp_path):
        """A config without local hooks must exit non-zero (the heal refuses
        to run rather than execute unknown hooks under write tokens)."""
        (tmp_path / ".pre-commit-config.yaml").write_text(
            "repos:\n"
            "  - repo: https://github.com/psf/black\n"
            "    rev: 26.5.1\n"
            "    hooks:\n"
            "      - id: black\n"
        )
        result = _run_snippet(_derivation_snippet(), tmp_path)
        assert result.returncode != 0
        assert "parse assumptions stale" in result.stderr
